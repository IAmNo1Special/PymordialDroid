"""Fleet management, inventory persistence, heartbeat monitoring, and batch orchestration."""

import asyncio
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pymordialdroid.config import DATA_DIR, SystemConfig, resolve_system_config
from pymordialdroid.device import Phone
from pymordialdroid.discovery import (
    discover_usb_devices,
    get_arp_candidate_ips,
    get_inventory_subnet_bases,
    get_local_subnet_bases,
    query_device_name_via_adb,
    scan_hotspot_devices,
    scan_subnets_for_phones,
)
from pymordialdroid.models import DeviceRecord
from pymordialdroid.window import WindowLayoutConfig, organize_windows_grid

log = logging.getLogger("pymordialdroid")


class FleetCommander:
    """Orchestrates multiple Phone devices, managing fleet inventory,

    connection heartbeats, viewer lifecycles, and bulk actions.
    """

    def __init__(
        self,
        config: SystemConfig | None = None,
        layout_config: WindowLayoutConfig | None = None,
    ):
        self.config = config or resolve_system_config()
        self.signer = self.config.get_signer()
        self.layout_config = layout_config or WindowLayoutConfig()

        self.inventory_file = DATA_DIR / "fleet_inventory.json"
        self.viewer_layout_file = DATA_DIR / "viewer_layout.json"

        # Fleet State
        self.phones: list[Phone] = []
        self.open_viewers: set[str] = set()  # Tracks device IPs with open viewers
        self.viewer_processes: dict[str, subprocess.Popen] = {}
        self.running = True

    # --- INVENTORY & STATE ---

    def load_inventory(self) -> list[Phone]:
        """Loads devices from the fleet inventory JSON file."""
        if not self.inventory_file.exists():
            self.phones = []
            return self.phones

        try:
            with open(self.inventory_file, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"Failed to read inventory file: {e}")
            return self.phones

        self.phones = []
        for item in data:
            rec = DeviceRecord(**item)
            p = Phone(
                record=rec,
                signer=self.signer,
                adb_path=self.config.adb_bin_path,
                scrcpy_path=self.config.scrcpy_bin_path,
                layout_config=self.layout_config,
            )
            self.phones.append(p)
        return self.phones

    def save_inventory(self, records: list[DeviceRecord] | None = None) -> None:
        """Saves current or provided device records to the fleet inventory JSON file."""
        recs = records if records is not None else [p.record for p in self.phones]
        data = [r.model_dump() for r in recs]
        with open(self.inventory_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        log.info(f"Saved {len(data)} device(s) to inventory.")

    def _resolve_device_name(self, ip: str, port: int, serial: str) -> str:
        """Returns real on-device name, falling back to Device-{ip}."""
        try:
            # Best effort: ensure CLI daemon knows the endpoint first.
            subprocess.run(
                [str(self.config.adb_bin_path), "connect", f"{ip}:{port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
        try:
            detected = query_device_name_via_adb(self.config.adb_bin_path, serial)
        except Exception:
            detected = None
        return detected or f"Device-{ip}"

    async def refresh_placeholder_names(self) -> int:
        """Renames connected Device-* placeholders to real on-device names.

        Returns number of devices renamed (and persists inventory if > 0).
        """
        renamed = 0
        for phone in self.phones:
            if not phone.record.name.startswith("Device-"):
                continue
            if not phone._connected:
                continue
            try:
                real = await phone.get_friendly_name()
            except Exception:
                real = None
            if real and real != phone.record.name:
                log.info(f"Renamed {phone.record.name} -> {real}")
                phone.record.name = real
                renamed += 1
        if renamed:
            self.save_inventory()
        return renamed

    def add_device(
        self,
        ip: str,
        port: int = 5555,
        name: str | None = None,
        pin: str | None = None,
        serial: str | None = None,
    ) -> DeviceRecord:
        """Adds or updates a device in the fleet inventory.

        If `name` is omitted, queries the device via ADB for its real
        user-visible name (device_name / marketing_name / model),
        falling back to Device-{ip} when offline.
        """
        self.load_inventory()
        resolved_serial = serial or f"{ip}:{port}"

        existing = next(
            (
                p
                for p in self.phones
                if p.record.ip == ip or p.record.serial == resolved_serial
            ),
            None,
        )
        if name is not None:
            resolved_name = name
        elif existing and not (
            existing.record.name.startswith("Device-")
            or existing.record.name == "Unknown"
        ):
            resolved_name = existing.record.name
        else:
            resolved_name = self._resolve_device_name(ip, port, resolved_serial)

        if existing:
            existing.record.port = port
            existing.record.name = resolved_name
            existing.record.serial = resolved_serial
            if pin is not None:
                existing.record.pin = pin
            rec = existing.record
        else:
            rec = DeviceRecord(
                serial=resolved_serial,
                ip=ip,
                port=port,
                name=resolved_name,
                pin=pin,
            )
            phone = Phone(
                record=rec,
                signer=self.signer,
                adb_path=self.config.adb_bin_path,
                scrcpy_path=self.config.scrcpy_bin_path,
                layout_config=self.layout_config,
            )
            self.phones.append(phone)

        self.save_inventory()
        return rec

    def _find_phone_index(self, index_or_id: int | str) -> int | None:
        """Resolves a device rank index, IP, serial, or name to a list index."""
        if isinstance(index_or_id, int):
            if 0 <= index_or_id < len(self.phones):
                return index_or_id
            return None
        ident = str(index_or_id)
        for i, p in enumerate(self.phones):
            if (
                p.record.ip == ident
                or p.record.serial == ident
                or p.record.name.lower() == ident.lower()
                or f"{p.record.ip}:{p.record.port}" == ident
            ):
                return i
        return None

    def _cleanup_after_removal(self, ip: str, port: int) -> None:
        """Removes viewer tracking and runs `adb disconnect` (best effort)."""
        self.open_viewers.discard(ip)
        self.viewer_processes.pop(ip, None)
        self.save_viewer_layout()
        try:
            subprocess.run(
                [str(self.config.adb_bin_path), "disconnect", f"{ip}:{port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception as e:
            log.warning(f"adb disconnect failed for {ip}:{port}: {e}")

    def close_single_viewer(self, rank: int, device_name: str) -> bool:
        """Closes a single scrcpy viewer window by its standardized title.

        Returns True if a window handle was found and a close was requested.
        """
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            from pymordialdroid.window import get_viewer_window_title

            title = get_viewer_window_title(rank, device_name)
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                WM_CLOSE = 0x0010
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return True
            return False
        except Exception as e:
            log.warning(f"Failed to close viewer window '{device_name}': {e}")
            return False

    async def disconnect_device(self, index_or_id: int | str) -> bool:
        """Disconnects a device's ADB session but keeps it in inventory.

        Note: the heartbeat monitor will auto-reconnect an inventory device
        that is still reachable. Use remove_connected_device() to fully
        remove/disconnect a device.
        """
        idx = self._find_phone_index(index_or_id)
        if idx is None:
            return False
        phone = self.phones[idx]
        try:
            await phone.disconnect()
        except Exception:
            pass
        self._cleanup_after_removal(phone.record.ip, phone.record.port)
        return True

    async def remove_connected_device(self, index_or_id: int | str) -> bool:
        """Disconnects, closes viewer tracking, and removes device from inventory."""
        idx = self._find_phone_index(index_or_id)
        if idx is None:
            return False
        phone = self.phones[idx]
        # Best effort: close its viewer window before dropping state.
        if phone.record.ip in self.open_viewers:
            self.close_single_viewer(idx, phone.record.name)
        try:
            await phone.disconnect()
        except Exception:
            pass
        ip, port = phone.record.ip, phone.record.port
        del self.phones[idx]
        self._cleanup_after_removal(ip, port)
        self.save_inventory()
        log.info(f"Removed device {phone.record.name} ({ip}) from fleet.")
        return True

    def remove_device(self, identifier: str) -> bool:
        """Removes a device from the fleet inventory by IP, serial, or name."""
        self.load_inventory()
        idx = self._find_phone_index(identifier)
        if idx is None:
            return False
        phone = self.phones[idx]
        ip, port, name = phone.record.ip, phone.record.port, phone.record.name
        if ip in self.open_viewers:
            self.close_single_viewer(idx, name)
        try:
            # Sync context (CLI): best-effort close of the live transport.
            if phone._connected:
                phone._connected = False
                phone.status = "Offline"
        except Exception:
            pass
        del self.phones[idx]
        self._cleanup_after_removal(ip, port)
        self.save_inventory()
        return True

    def get_controller(self, index_or_ip: int | str):
        """Retrieves the AndroidController for a device by index rank or IP."""
        if isinstance(index_or_ip, int):
            if 0 <= index_or_ip < len(self.phones):
                return self.phones[index_or_ip].controller
            return None
        for phone in self.phones:
            if phone.record.ip == index_or_ip or phone.record.serial == index_or_ip:
                return phone.controller
        return None

    def save_viewer_layout(self) -> None:
        """Saves currently open viewer device IPs to file."""
        data = {"open_viewers": list(self.open_viewers)}
        with open(self.viewer_layout_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def load_viewer_layout(self) -> set[str]:
        """Loads saved viewer layout from file."""
        if not self.viewer_layout_file.exists():
            return set()
        try:
            with open(self.viewer_layout_file, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("open_viewers", []))
        except (json.JSONDecodeError, KeyError, OSError):
            return set()

    # --- BACKGROUND WORKERS ---

    async def heartbeat_monitor(self) -> None:
        """Background task: Periodically pings devices and auto-heals dropped connections."""
        # Initial connect for all loaded devices
        initial_ports = {p.record.ip: p.record.port for p in self.phones}
        connect_tasks = [p.connect(auto_heal=True) for p in self.phones]
        if connect_tasks:
            await asyncio.gather(*connect_tasks)
            if any(
                p.record.port != initial_ports.get(p.record.ip) for p in self.phones
            ):
                log.info(
                    "Inventory ports updated after auto-heal. Persisting inventory..."
                )
                self.save_inventory()
            try:
                await self.refresh_placeholder_names()
            except Exception:
                pass

        while self.running:
            for phone in self.phones:
                try:
                    if phone._connected:
                        await phone.shell("echo ping")
                        if phone._connected:
                            phone.status = "Online"
                            phone.last_ping = time.time()

                    if not phone._connected:
                        old_port = phone.record.port
                        phone.status = "Connecting..."
                        healed = await phone.connect(auto_heal=True)
                        if healed and phone.record.port != old_port:
                            log.info(
                                f"Auto-healed connection for {phone.record.name} to port {phone.record.port}. Persisting inventory..."
                            )
                            self.save_inventory()
                except Exception:
                    phone.status = "Error"

            await asyncio.sleep(10)

    async def mission_runner(self) -> None:
        """Simulated background task loop (queries battery and status updates)."""
        while self.running:
            active_phones = [p for p in self.phones if p._connected]
            for phone in active_phones:
                await phone.get_battery()
                await asyncio.sleep(0.1)
            await asyncio.sleep(60)

    # --- VIEWER OPERATIONS ---

    def is_viewer_alive(self, ip: str) -> bool:
        """Returns True if we hold a live scrcpy handle for this IP."""
        proc = self.viewer_processes.get(ip)
        if proc is None:
            # Launched before handle tracking existed (or external viewer):
            # fall back to tracked-set membership.
            return ip in self.open_viewers and self._is_scrcpy_running(ip)
        try:
            return proc.poll() is None
        except Exception:
            return False

    def _is_scrcpy_running(self, ip: str) -> bool:
        """Fallback liveness check via OS process list when no handle exists."""
        try:
            if sys.platform == "win32":
                res = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq scrcpy.exe", "/FO", "CSV"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                return "scrcpy.exe" in (res.stdout or "")
            res = subprocess.run(
                ["pgrep", "-f", "scrcpy"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            # If we can't check, assume tracked state is correct.
            return True

    def prune_dead_viewers(self) -> list[str]:
        """Drops tracking for scrcpy processes that exited unexpectedly.

        This happens when locking the device drops Wi-Fi ADB and scrcpy
        quits itself. Without pruning, relaunch is blocked with a stale
        'Viewer already open' message.

        Returns list of pruned IPs.
        """
        pruned: list[str] = []
        for ip in list(self.open_viewers):
            proc = self.viewer_processes.get(ip)
            dead = False
            if proc is not None:
                try:
                    dead = proc.poll() is not None
                except Exception:
                    dead = False
                if dead:
                    self.viewer_processes.pop(ip, None)
            else:
                # No handle: only prune if NO scrcpy process exists at all.
                # (Conservative — avoids dropping a valid external viewer.)
                try:
                    if sys.platform == "win32":
                        res = subprocess.run(
                            [
                                "tasklist",
                                "/FI",
                                "IMAGENAME eq scrcpy.exe",
                                "/FO",
                                "CSV",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            check=False,
                        )
                        if "scrcpy.exe" not in (res.stdout or ""):
                            dead = True
                    else:
                        res = subprocess.run(
                            ["pgrep", "-f", "scrcpy"],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                        if res.returncode != 0:
                            dead = True
                except Exception:
                    dead = False
            if dead:
                self.open_viewers.discard(ip)
                pruned.append(ip)
                log.warning(
                    f"Viewer for {ip} exited unexpectedly "
                    "(screen lock drops Wi-Fi ADB and scrcpy quits). "
                    "Pruned tracking — relaunch when unlocked."
                )
        if pruned:
            self.save_viewer_layout()
        return pruned

    def launch_device_viewer(
        self,
        rank: int,
        ghost: bool = False,
        new_display: str | None = None,
        start_app: str | None = None,
        show_touches: bool = False,
    ) -> bool:
        """Launches a viewer for a specific device index."""
        if not (0 <= rank < len(self.phones)):
            return False

        self.prune_dead_viewers()
        device = self.phones[rank]
        if device.record.ip in self.open_viewers:
            return False

        if new_display is not None:
            proc = device.open_virtual_viewer(
                rank=rank, display=new_display, start_app=start_app
            )
        elif ghost:
            proc = device.open_ghost_viewer(rank=rank)
        else:
            proc = device.open_viewer(
                rank=rank, start_app=start_app, show_touches=show_touches
            )

        self.open_viewers.add(device.record.ip)
        if proc is not None:
            self.viewer_processes[device.record.ip] = proc
        self.save_viewer_layout()
        return True

    async def batch_launch_viewers(
        self,
        ghost: bool = False,
        new_display: str | None = None,
        start_app: str | None = None,
    ) -> int:
        """Launches viewers for all devices not currently opened."""
        self.prune_dead_viewers()
        phones_to_launch = [
            p for p in self.phones if p.record.ip not in self.open_viewers
        ]
        if not phones_to_launch:
            return 0

        async def _launch_one(p: Phone, rank: int) -> None:
            if await p.connect():
                await p.unlock_phone()
                await p.disconnect()

            if new_display is not None:
                proc = p.open_virtual_viewer(
                    rank=rank, display=new_display, start_app=start_app
                )
            elif ghost:
                proc = p.open_ghost_viewer(rank=rank)
            else:
                proc = p.open_viewer(rank=rank, start_app=start_app)
            self.open_viewers.add(p.record.ip)
            if proc is not None:
                self.viewer_processes[p.record.ip] = proc

        for p in phones_to_launch:
            rank = self.phones.index(p)
            await _launch_one(p, rank)
            await asyncio.sleep(0.5)

        self.save_viewer_layout()
        return len(phones_to_launch)

    async def restore_viewers_from_layout(self) -> int:
        """Restores scrcpy viewers according to the saved layout."""
        saved = self.load_viewer_layout()
        if not saved:
            return 0

        self.prune_dead_viewers()
        count = 0
        for i, phone in enumerate(self.phones):
            if phone.record.ip in saved:
                if await phone.connect():
                    await phone.unlock_phone()
                    await phone.disconnect()
                    proc = phone.open_viewer(rank=i)
                    self.open_viewers.add(phone.record.ip)
                    if proc is not None:
                        self.viewer_processes[phone.record.ip] = proc
                    count += 1
                await asyncio.sleep(0.5)
        return count

    def kill_viewers(self) -> None:
        """Kills all active scrcpy viewer processes."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    "taskkill /F /IM scrcpy.exe /T",
                    shell=True,
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", "scrcpy"],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                )
            self.open_viewers.clear()
            self.viewer_processes.clear()
            self.save_viewer_layout()
            log.info("Killed active viewers.")
        except Exception as e:
            log.error(f"Error terminating viewers: {e}")

    def organize_viewers_grid(self) -> int:
        """Tiles all open scrcpy windows into a desktop grid."""
        records = [p.record for p in self.phones]
        return organize_windows_grid(records, self.layout_config)

    # --- FLEET-WIDE ACTIONS ---

    async def install_apk_on_all(
        self, apk_path: str | Path, update: bool = True
    ) -> None:
        """Installs an APK file across all fleet devices concurrently."""
        if not self.phones:
            self.load_inventory()

        tasks = [p.install_apk(apk_path, update=update) for p in self.phones]
        if tasks:
            await asyncio.gather(*tasks)

    # --- FLEET-WIDE RECORDING (Feature 2) ---

    def start_recording_all(
        self,
        output_dir: str | Path | None = None,
        show_touches: bool = False,
        time_limit: int | None = None,
    ) -> dict[str, Path]:
        """Starts headless MP4 recording on every fleet device.

        Returns:
            Mapping of device IP -> output path for devices where recording
            started successfully.
        """
        from pymordialdroid.config import DATA_DIR

        if not self.phones:
            self.load_inventory()
        out_dir = Path(output_dir) if output_dir else DATA_DIR / "evidence"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        started: dict[str, Path] = {}
        for phone in self.phones:
            dest = out_dir / f"evidence_{phone.record.name}.mp4"
            try:
                path = phone.start_recording(
                    output_path=dest,
                    show_touches=show_touches,
                    time_limit=time_limit,
                )
            except Exception as e:
                log.warning(f"[{phone.record.name}] record start failed: {e}")
                continue
            if path is not None:
                started[phone.record.ip] = path
                phone.last_action = "Recording evidence"
        if started:
            log.info(f"Recording on {len(started)} device(s) -> {out_dir}")
        return started

    def stop_recording_all(self) -> dict[str, Path]:
        """Stops headless recording on every fleet device.

        Returns:
            Mapping of device IP -> output path for devices that were recording.
        """
        stopped: dict[str, Path] = {}
        for phone in self.phones:
            try:
                path = phone.stop_recording()
            except Exception as e:
                log.warning(f"[{phone.record.name}] record stop failed: {e}")
                continue
            if path is not None:
                stopped[phone.record.ip] = path
                phone.last_action = f"Evidence saved: {path.name}"
        return stopped

    async def record_evidence_all(
        self,
        duration_sec: int = 60,
        output_dir: str | Path | None = None,
        show_touches: bool = False,
    ) -> dict[str, Path]:
        """Records a time-limited evidence clip on every device concurrently."""
        if not self.phones:
            self.load_inventory()
        results = await asyncio.gather(
            *[
                p.record_evidence(
                    duration_sec=duration_sec,
                    output_dir=output_dir,
                    show_touches=show_touches,
                )
                for p in self.phones
            ]
        )
        clips: dict[str, Path] = {}
        for phone, path in zip(self.phones, results):
            if path is not None:
                clips[phone.record.ip] = path
        return clips

    # --- FLEET-WIDE HEADLESS FEED (Feature 1) ---

    def start_headless_feeds(
        self,
        max_size: int = 1024,
        max_fps: int = 30,
        new_display: str | None = None,
        targets: list[Phone] | None = None,
    ) -> int:
        """Starts windowless feeds. Returns count started."""
        phones = self._resolve_targets(targets)
        count = 0
        for phone in phones:
            try:
                if phone.start_headless_feed(
                    max_size=max_size, max_fps=max_fps, new_display=new_display
                ):
                    count += 1
            except Exception as e:
                log.warning(f"[{phone.record.name}] feed start failed: {e}")
        return count

    def stop_headless_feeds(self, targets: list[Phone] | None = None) -> int:
        """Stops windowless feeds. Returns count stopped."""
        count = 0
        for phone in targets if targets is not None else self.phones:
            try:
                if phone.stop_headless_feed():
                    count += 1
            except Exception:
                pass
        return count

    # --- FLEET-WIDE LIVE STREAM (sub-100ms H.264) ---

    def _resolve_targets(self, targets: list[Phone] | None) -> list[Phone]:
        """Returns explicit targets, else the full inventory (loading it)."""
        if targets is not None:
            return targets
        if not self.phones:
            self.load_inventory()
        return self.phones

    def start_live_streams(
        self,
        max_size: int = 960,
        max_fps: int = 30,
        new_display: str | None = None,
        targets: list[Phone] | None = None,
        max_workers: int = 8,
    ) -> int:
        """Starts direct live streams concurrently. Returns count started.

        Each start blocks up to ~20s (push/connect/first-frame), so devices
        are started in parallel threads instead of sequentially.
        """
        phones = self._resolve_targets(targets)
        if not phones:
            return 0

        def _start_one(phone: Phone) -> bool:
            try:
                return bool(
                    phone.start_live_stream(
                        max_size=max_size, max_fps=max_fps, new_display=new_display
                    )
                )
            except Exception as e:
                log.warning(f"[{phone.record.name}] live stream failed: {e}")
                return False

        workers = max(1, min(max_workers, len(phones)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return sum(1 for ok in pool.map(_start_one, phones) if ok)

    def stop_live_streams(self, targets: list[Phone] | None = None) -> int:
        """Stops live streams. Returns count stopped."""
        count = 0
        for phone in targets if targets is not None else self.phones:
            try:
                if phone.stop_live_stream():
                    count += 1
            except Exception:
                pass
        return count

    # --- DISCOVERY ADAPTERS ---

    def initialize_fleet_via_usb(
        self, prompt_for_names: bool = True
    ) -> list[DeviceRecord]:
        """Discovers USB devices, merges them into inventory, and updates fleet."""
        records = discover_usb_devices(
            self.config.adb_bin_path, prompt_for_names=prompt_for_names
        )
        if not records:
            return records
        # Merge (don't wipe existing Wi-Fi devices no longer on USB).
        self.load_inventory()
        by_key: dict[str, Phone] = {}
        for p in self.phones:
            by_key[p.record.ip] = p
            by_key[p.record.serial] = p
        for rec in records:
            existing = by_key.get(rec.ip) or by_key.get(rec.serial)
            if existing:
                existing.record.port = rec.port
                existing.record.serial = rec.serial
                # Keep custom names; only refresh placeholders/Unknown.
                is_placeholder = existing.record.name.startswith(
                    "Device-"
                ) or existing.record.name in ("Unknown", "")
                if is_placeholder and rec.name:
                    existing.record.name = rec.name
            else:
                phone = Phone(
                    record=rec,
                    signer=self.signer,
                    adb_path=self.config.adb_bin_path,
                    scrcpy_path=self.config.scrcpy_bin_path,
                    layout_config=self.layout_config,
                )
                self.phones.append(phone)
                by_key[rec.ip] = phone
                by_key[rec.serial] = phone
        self.save_inventory()
        return records

    def get_scan_subnets(self) -> list[str]:
        """Subnets the auto network scan will sweep.

        Union of local interface /24s, known-device /24s, and the Windows
        hotspot fallback (192.168.137) — so a fleet on 172.20.8.x is found
        without manual configuration.
        """
        subnets: list[str] = []
        seen: set[str] = set()
        for base in (
            get_local_subnet_bases()
            + get_inventory_subnet_bases([p.record.ip for p in self.phones])
            + ["192.168.137"]
        ):
            if base not in seen:
                seen.add(base)
                subnets.append(base)
        return sorted(subnets)

    async def scan_hotspot_for_phones(
        self,
        base_ip: str | None = None,
        start_host: int = 2,
        end_host: int = 254,
    ) -> list[str]:
        """Sweeps the network and adopts new devices into inventory.

        Args:
            base_ip: Single /24 base to scan (e.g. "192.168.137").
                None (default) = auto: local + inventory + hotspot subnets.

        Returns:
            List of newly added IP addresses (already-known IPs are skipped
            so callers can report 'No new devices' accurately).
        """
        if base_ip is not None:
            active_ips = await scan_hotspot_devices(
                self.signer, base_ip=base_ip, start_host=start_host, end_host=end_host
            )
        else:
            subnets = self.get_scan_subnets()
            # ARP cache covers hosts outside the local /24 (e.g. PC on
            # 172.20.1.x/16, phone on 172.20.8.x) without sweeping 65k.
            try:
                extra = await asyncio.to_thread(get_arp_candidate_ips)
            except Exception:
                extra = []
            active_ips = await scan_subnets_for_phones(
                self.signer,
                base_ips=subnets,
                start_host=start_host,
                end_host=end_host,
                extra_ips=extra,
            )
        if not active_ips:
            return []
        known_ips = {p.record.ip for p in self.phones}
        new_ips: list[str] = []
        for ip in active_ips:
            if ip in known_ips:
                continue
            # Resolve real name off the event loop (blocking ADB CLI).
            real_name = await asyncio.to_thread(
                self._resolve_device_name, ip, 5555, f"{ip}:5555"
            )
            rec = DeviceRecord(serial=f"{ip}:5555", ip=ip, port=5555, name=real_name)
            phone = Phone(
                record=rec,
                signer=self.signer,
                adb_path=self.config.adb_bin_path,
                scrcpy_path=self.config.scrcpy_bin_path,
                layout_config=self.layout_config,
            )
            self.phones.append(phone)
            known_ips.add(ip)
            new_ips.append(ip)
        if new_ips:
            self.save_inventory()
            log.info(f"Adopted {len(new_ips)} new device(s): {new_ips}")
        return new_ips

    # --- SHUTDOWN ---

    async def shutdown(self) -> None:
        """Gracefully disconnects all devices and shuts down fleet commander."""
        self.running = False
        try:
            self.stop_recording_all()
        except Exception:
            pass
        try:
            self.stop_live_streams()
        except Exception:
            pass
        try:
            self.stop_headless_feeds()
        except Exception:
            pass
        self.kill_viewers()
        for phone in self.phones:
            try:
                await phone.disconnect()
            except Exception:
                pass
