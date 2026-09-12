"""Fleet management, inventory persistence, heartbeat monitoring, and batch orchestration."""

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from pymordialdroid.config import DATA_DIR, SystemConfig, resolve_system_config
from pymordialdroid.device import Phone
from pymordialdroid.discovery import discover_usb_devices, scan_hotspot_devices
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
        connect_tasks = [p.connect() for p in self.phones]
        if connect_tasks:
            await asyncio.gather(*connect_tasks)

        while self.running:
            for phone in self.phones:
                try:
                    if phone._connected:
                        await phone.shell("echo ping")
                        if phone._connected:
                            phone.status = "Online"
                            phone.last_ping = time.time()

                    if not phone._connected:
                        phone.status = "Connecting..."
                        await phone.connect()
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

    def launch_device_viewer(self, rank: int, ghost: bool = False) -> bool:
        """Launches a viewer for a specific device index."""
        if not (0 <= rank < len(self.phones)):
            return False

        device = self.phones[rank]
        if device.record.ip in self.open_viewers:
            return False

        if ghost:
            device.open_ghost_viewer(rank=rank)
        else:
            device.open_viewer(rank=rank)

        self.open_viewers.add(device.record.ip)
        self.save_viewer_layout()
        return True

    async def batch_launch_viewers(self, ghost: bool = False) -> int:
        """Launches viewers for all devices not currently opened."""
        phones_to_launch = [
            p for p in self.phones if p.record.ip not in self.open_viewers
        ]
        if not phones_to_launch:
            return 0

        async def _launch_one(p: Phone, rank: int) -> None:
            if await p.connect():
                await p.unlock_phone()
                await p.disconnect()

            if ghost:
                p.open_ghost_viewer(rank=rank)
            else:
                p.open_viewer(rank=rank)
            self.open_viewers.add(p.record.ip)

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

        count = 0
        for i, phone in enumerate(self.phones):
            if phone.record.ip in saved:
                if await phone.connect():
                    await phone.unlock_phone()
                    await phone.disconnect()
                    phone.open_viewer(rank=i)
                    self.open_viewers.add(phone.record.ip)
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

    # --- DISCOVERY ADAPTERS ---

    def initialize_fleet_via_usb(
        self, prompt_for_names: bool = True
    ) -> list[DeviceRecord]:
        """Discovers USB devices, registers them in inventory, and updates fleet."""
        records = discover_usb_devices(
            self.config.adb_bin_path, prompt_for_names=prompt_for_names
        )
        if records:
            self.save_inventory(records)
            self.load_inventory()
        return records

    async def scan_hotspot_for_phones(self) -> list[str]:
        """Runs parallel subnet sweep on hotspot to discover devices."""
        return await scan_hotspot_devices(self.signer)

    # --- SHUTDOWN ---

    async def shutdown(self) -> None:
        """Gracefully disconnects all devices and shuts down fleet commander."""
        self.running = False
        self.kill_viewers()
        for phone in self.phones:
            try:
                await phone.disconnect()
            except Exception:
                pass
