"""Device abstraction representing a single physical Android device via ADB and Scrcpy."""

import asyncio
import logging
import os
import random
import re
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from pydantic import FilePath

from pymordialdroid.config import get_default_pin
from pymordialdroid.models import DeviceRecord
from pymordialdroid.window import WindowLayoutConfig, get_viewer_window_title

log = logging.getLogger("pymordialdroid")

# Anchored to a line starting with "level:" so device warnings (e.g. Samsung
# "Failed to write while dumping service ..."), "Capacity level:", and
# ACTION_BATTERY_CHANGED log lines can never pollute the reading.
_BATTERY_LEVEL_RE = re.compile(r"^\s*level:\s*(\d+)", re.MULTILINE)


def parse_battery_level(dumpsys_output: str) -> str | None:
    """Extracts ``"<pct>%"`` from `dumpsys battery` output, or None."""
    match = _BATTERY_LEVEL_RE.search(dumpsys_output or "")
    if not match:
        return None
    return f"{match.group(1)}%"


class Phone:
    """Represents a single Android device.

    Encapsulates async ADB connection logic, telemetry, screen capture,
    input actions, and scrcpy display streaming.
    """

    def __init__(
        self,
        record: DeviceRecord,
        signer: PythonRSASigner,
        adb_path: FilePath,
        scrcpy_path: FilePath,
        layout_config: WindowLayoutConfig | None = None,
    ):
        self.record = record
        self.signer = signer
        self.adb_path = Path(adb_path)
        self.scrcpy_path = Path(scrcpy_path)
        self.layout_config = layout_config or WindowLayoutConfig()

        # Async Device
        self.device = AdbDeviceTcpAsync(
            record.ip, record.port, default_transport_timeout_s=9
        )
        self._connected = False

        # State tracking for UI
        self.status = "Offline"
        self.battery_level = "N/A"
        self.last_action = "Idle"
        self.last_ping = 0.0
        self._controller = None

    @property
    def controller(self):
        """Returns the AndroidController instance for this device."""
        if self._controller is None or self._controller.port != self.record.port:
            from pymordialdroid.android_controller import AndroidController

            self._controller = AndroidController(
                ip=self.record.ip,
                port=self.record.port,
                device_name=self.record.name,
                pin=self.record.pin,
            )
        return self._controller

    async def _try_auto_heal_tcpip(self) -> bool:
        """Attempts to auto-heal a failed wireless connection using the bundled ADB CLI.

        Handles:
        1. Android 11+ Wireless Debugging TLS port mismatch (STLS error) -> runs 'tcpip 5555'.
        2. Dropped port 5555 after phone reboot/Wi-Fi reconnect -> scans attached devices
           for mDNS (_adb-tls-connect._tcp) or USB endpoints and restarts tcpip 5555.
        """
        log.info(f"[{self.record.name}] Attempting connection auto-heal...")
        self.status = "Auto-Healing..."
        adb_bin = str(self.adb_path)

        def _run_adb(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess:
            return subprocess.run(
                [adb_bin, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )

        loop = asyncio.get_running_loop()

        try:
            # 1. If configured on a non-5555 port (e.g. ephemeral TLS port), switch it via CLI
            if self.record.port != 5555:
                endpoint = f"{self.record.ip}:{self.record.port}"
                await loop.run_in_executor(None, _run_adb, "connect", endpoint)
                res = await loop.run_in_executor(
                    None, _run_adb, "-s", endpoint, "tcpip", "5555"
                )
                if res.returncode == 0:
                    log.info(
                        f"[{self.record.name}] Switched device {endpoint} to port 5555 via CLI."
                    )

            # 2. Check `adb devices -l` for candidate devices (mDNS TLS or USB)
            devices_res = await loop.run_in_executor(None, _run_adb, "devices", "-l")
            if devices_res.returncode == 0:
                lines = devices_res.stdout.strip().splitlines()[1:]
                for line in lines:
                    parts = line.split()
                    if not parts or parts[1] != "device":
                        continue
                    serial = parts[0]
                    is_candidate = (
                        self.record.ip in serial
                        or serial.endswith("._adb-tls-connect._tcp")
                        or not (":" in serial or "._" in serial)
                    )
                    if is_candidate:
                        ip_res = await loop.run_in_executor(
                            None, _run_adb, "-s", serial, "shell", "ip", "route"
                        )
                        if self.record.ip in ip_res.stdout:
                            log.info(
                                f"[{self.record.name}] Matched attached device {serial} for IP {self.record.ip}. Enabling tcpip 5555..."
                            )
                            await loop.run_in_executor(
                                None, _run_adb, "-s", serial, "tcpip", "5555"
                            )
                            await asyncio.sleep(1.0)
                            break

            # 3. Ensure CLI connects to port 5555
            await loop.run_in_executor(
                None, _run_adb, "connect", f"{self.record.ip}:5555"
            )

            # 4. Close previous python device transport
            try:
                await self.device.close()
            except Exception:
                pass

            # 5. Reinitialize async device on port 5555 and attempt handshake
            self.device = AdbDeviceTcpAsync(
                self.record.ip, 5555, default_transport_timeout_s=9
            )
            await self.device.connect(rsa_keys=[self.signer], auth_timeout_s=5)

            # Update record to 5555
            self.record.port = 5555
            self.record.serial = f"{self.record.ip}:5555"
            self._connected = True
            self.status = "Online"
            self.last_action = "Auto-Healed (Port 5555)"
            log.info(f"[{self.record.name}] Auto-heal succeeded! Connected on port 5555.")
            return True

        except Exception as e:
            log.warning(f"[{self.record.name}] Auto-heal failed: {e}")
            self.status = "Connection Failed"
            self.last_action = f"Heal failed: {str(e)[:15]}"
            return False

    async def connect(self, auto_heal: bool = True) -> bool:
        """Asynchronously connects to the device via ADB with optional auto-healing."""
        try:
            await self.device.connect(rsa_keys=[self.signer], auth_timeout_s=5)
            self._connected = True
            self.status = "Online"
            return True
        except Exception as e:
            err_str = str(e)
            log.warning(f"[{self.record.name}] Connect failed: {err_str}")
            if auto_heal:
                healed = await self._try_auto_heal_tcpip()
                if healed:
                    return True
            self.status = "Connection Failed"
            self.last_action = f"Err: {err_str[:15]}..."
            return False

    async def disconnect(self) -> None:
        """Closes the active ADB device connection."""
        try:
            await self.device.close()
        except Exception:
            pass
        self._connected = False
        self.status = "Offline"

    async def shell(self, cmd: str) -> str:
        """Executes a shell command on the device via ADB."""
        if not self._connected:
            return ""
        try:
            return await self.device.shell(cmd)
        except Exception:
            self._connected = False
            self.status = "Disconnected"
            return ""

    # --- ACTIONS & TELEMETRY ---

    async def get_friendly_name(self) -> str | None:
        """Returns the user-visible device name (e.g. 'Galaxy S24 Ultra').

        Queries `settings get global device_name`, then
        `ro.config.marketing_name`, then `ro.product.model`.
        Returns None if offline or all queries are empty.
        """
        for cmd in (
            "settings get global device_name",
            "getprop ro.config.marketing_name",
            "getprop ro.product.model",
        ):
            res = await self.shell(cmd)
            if not res:
                continue
            for line in res.replace("\r", "\n").split("\n"):
                cleaned = line.strip()
                if not cleaned or cleaned.lower() in ("null", "unknown", "(unknown)"):
                    continue
                return cleaned
        return None

    async def get_battery(self) -> None:
        """Queries and updates device battery level.

        Parses the anchored ``level:`` line only; device-side warnings and
        log spam in the output can no longer leak into ``battery_level``.
        """
        cmd = 'dumpsys battery | grep "level:" | head -n 1'
        res = await self.shell(cmd)
        parsed = parse_battery_level(res)
        if parsed is not None:
            self.battery_level = parsed
            self.last_action = "Checked Battery"

    async def toggle_power_button(self) -> None:
        """Wakes the device screen."""
        await self.input_keyevent(26)
        self.last_action = "Toggled Power"

    async def tap(self, x: int, y: int, w: int = 10, h: int = 10) -> None:
        """Sends a randomized tap within a bounding box using Gaussian distribution."""
        sigma_x = w / 4
        sigma_y = h / 4
        rand_x = int(random.gauss(x, sigma_x))
        rand_y = int(random.gauss(y, sigma_y))
        await self.shell(f"input tap {rand_x} {rand_y}")
        await asyncio.sleep(random.uniform(0.5, 1.2))

    async def input_keyevent(self, key_code: int | str) -> None:
        """Sends a keyevent to the device."""
        await self.shell(f"input keyevent {key_code}")

    async def type_text(self, text: str) -> None:
        """Types text into the currently focused input field."""
        safe_text = text.replace(" ", "%s")
        await self.shell(f"input text {safe_text}")

    async def get_screen_numpy(self) -> np.ndarray | None:
        """Pulls a screenshot from ADB directly into an OpenCV BGR numpy array."""
        try:
            raw_png = await self.device.shell("screencap -p", decode=False)
            image_array = np.asarray(bytearray(raw_png), dtype=np.uint8)
            return cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        except Exception as e:
            log.error(f"[{self.record.name}] Screenshot failed: {e}")
            return None

    async def find_and_click_image(self, template_path: str | Path) -> bool:
        """Finds a template image on screen using normalized cross-correlation and clicks it."""
        path_obj = Path(template_path)
        if not path_obj.exists():
            log.warning(f"Template not found: {template_path}")
            return False

        screen = await self.get_screen_numpy()
        if screen is None:
            return False

        template = cv2.imread(str(path_obj))
        if template is None:
            return False

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val > 0.8:
            h, w = template.shape[:2]
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2

            log.info(
                f"[{self.record.name}] Found match! Clicking {center_x}, {center_y}"
            )
            await self.tap(center_x, center_y, w, h)
            return True
        return False

    async def record_evidence(
        self,
        duration_sec: int = 60,
        output_dir: str | Path | None = None,
        show_touches: bool = False,
    ) -> Path | None:
        """Records screen video headless via scrcpy to an MP4 file.

        Uses ``--time-limit`` so scrcpy stops itself; updates ``last_action``
        when the clip finalizes. Returns the output path (or None on failure).
        """
        from pymordialdroid.config import DATA_DIR

        if output_dir is None:
            out_dir = DATA_DIR / "evidence"
        else:
            out_dir = Path(output_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        filename = out_dir / f"evidence_{self.record.name}_{int(time.time())}.mp4"

        path = self.start_recording(
            output_path=filename,
            show_touches=show_touches,
            time_limit=duration_sec,
        )
        if path is None:
            return None
        self.last_action = f"Recording evidence ({duration_sec}s)"

        async def _mark_done(total: int, dest: Path) -> None:
            await asyncio.sleep(total + 1)
            # Ensure our tracking is cleared even though scrcpy self-stopped.
            try:
                self.stop_recording()
            except Exception:
                pass
            self.last_action = f"Evidence saved: {dest.name}"

        asyncio.create_task(_mark_done(duration_sec, path))
        return path

    # --- SCRCPY RECORDING / HEADLESS FEED (delegated to controller's ScrcpyDevice) ---

    def start_recording(
        self,
        output_path: str | Path | None = None,
        show_touches: bool = False,
        time_limit: int | None = None,
        record_orientation: int | None = None,
    ) -> Path | None:
        """Starts headless MP4 recording via this device's ScrcpyDevice."""
        return self.controller.start_recording(
            output_path=output_path,
            show_touches=show_touches,
            time_limit=time_limit,
            record_orientation=record_orientation,
        )

    def stop_recording(self) -> Path | None:
        """Stops headless recording, returning the output path (if any)."""
        return self.controller.stop_recording()

    def is_recording(self) -> bool:
        """Checks if headless recording is running."""
        try:
            return self.controller.is_recording()
        except Exception:
            return False

    def start_headless_feed(
        self,
        output_path: str | Path | None = None,
        max_size: int = 1024,
        max_fps: int = 30,
        bit_rate: str = "2M",
        new_display: str | None = None,
        start_app: str | None = None,
        timeout: float = 30.0,
    ) -> bool:
        """Starts a headless live stream for high-FPS frame polling.

        Delegates to the live stream (direct scrcpy-server H.264 via PyAV).
        The legacy MKV file feed was removed because scrcpy cannot decode
        frames without a display surface when ``--no-window`` is used.
        """
        return self.controller.start_headless_feed(
            output_path=output_path,
            max_size=max_size,
            max_fps=max_fps,
            bit_rate=bit_rate,
            new_display=new_display,
            start_app=start_app,
            timeout=timeout,
        )

    def stop_headless_feed(self) -> bool:
        """Stops the headless feed."""
        try:
            return self.controller.stop_headless_feed()
        except Exception:
            return False

    def get_latest_frame(self) -> bytes | None:
        """Returns the latest headless-feed frame (or None if unavailable)."""
        try:
            return self.controller.get_latest_frame()
        except Exception:
            return None

    def start_live_stream(
        self,
        max_size: int = 960,
        bit_rate: str | int = "2M",
        max_fps: float | None = 30,
        new_display: str | None = None,
        timeout: float = 20.0,
    ) -> bool:
        """Starts a direct H.264 live stream (sub-100ms on USB/good WiFi)."""
        return self.controller.start_live_stream(
            max_size=max_size,
            bit_rate=bit_rate,
            max_fps=max_fps,
            new_display=new_display,
            timeout=timeout,
        )

    def stop_live_stream(self) -> bool:
        """Stops the live stream."""
        try:
            return self.controller.stop_live_stream()
        except Exception:
            return False

    def is_live_running(self) -> bool:
        """Checks if the live stream is running."""
        try:
            return self.controller.is_live_running()
        except Exception:
            return False

    def get_live_stats(self) -> dict:
        """Returns live-stream latency measurements (age_ms, fps, ...)."""
        try:
            return self.controller.get_live_stats()
        except Exception:
            return {"running": False, "has_frame": False, "stale": False}

    async def unlock_phone(self, pin: str | None = None) -> None:
        """Wakes phone and enters PIN if locked."""
        resolved_pin = pin or self.record.pin or get_default_pin()

        log.info(f"[{self.record.name}] Attempting Unlock...")
        await self.toggle_power_button()
        await self.input_keyevent(82)  # Menu key (dismisses swipe lock screen)
        await asyncio.sleep(1)

        await self.type_text(resolved_pin)
        await self.input_keyevent(66)  # Enter key

    async def install_apk(self, apk_path: str | Path, update: bool = True) -> bool:
        """Installs an APK on the device.

        Args:
            apk_path: Path to the .apk file.
            update: If True, uses '-r' to replace existing app. If False, skips if installed.
        """
        path_obj = Path(apk_path)
        if not path_obj.exists():
            log.warning(f"[{self.record.name}] APK not found: {apk_path}")
            return False

        log.info(f"[{self.record.name}] Installing {path_obj.name}...")

        cmd = [
            str(self.adb_path),
            "-s",
            f"{self.record.ip}:{self.record.port}",
            "install",
        ]
        if update:
            cmd.append("-r")
        cmd.append(str(path_obj))

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0:
            log.info(f"[{self.record.name}] INSTALL SUCCESS")
            return True

        err_msg = stderr.decode().strip()
        if "ALREADY_EXISTS" in err_msg and not update:
            log.info(f"[{self.record.name}] Skipped (Already Installed)")
            return True

        log.error(f"[{self.record.name}] INSTALL FAILED: {err_msg}")
        return False

    # --- SCRCPY PROCESS LAUNCHING ---

    def _launch_scrcpy_process(
        self,
        rank: int = 0,
        extra_args: list[str] | None = None,
        new_display: str | None = None,
        start_app: str | None = None,
        show_touches: bool = False,
    ) -> subprocess.Popen | None:
        """Launches a scrcpy process positioned according to grid layout.

        Returns the Popen handle so callers can detect unexpected exits
        (e.g. ADB Wi-Fi drop on screen lock kills scrcpy).
        """
        if extra_args is None:
            extra_args = []
        else:
            extra_args = list(extra_args)

        if new_display is not None:
            extra_args.append(
                f"--new-display={new_display}" if new_display else "--new-display"
            )
            if not any(a.startswith("--no-vd-system-decorations") for a in extra_args):
                extra_args.append("--no-vd-system-decorations")
        if start_app:
            extra_args.append(f"--start-app={start_app}")
        if show_touches and "--show-touches" not in extra_args and "-t" not in extra_args:
            extra_args.append("--show-touches")

        # Ensure ADB daemon knows the network endpoint
        try:
            subprocess.run(
                [str(self.adb_path), "connect", f"{self.record.ip}:{self.record.port}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

        env = os.environ.copy()
        env["ADB"] = str(self.adb_path)

        pos_x, pos_y = self.layout_config.get_position(rank)
        window_title = get_viewer_window_title(rank, self.record.name)

        cmd = [
            str(self.scrcpy_path),
            "--serial",
            f"{self.record.ip}:{self.record.port}",
            "--max-size",
            "1024",
            "--video-bit-rate",
            "2M",
            "--max-fps",
            "30",
            "--window-title",
            window_title,
            "--window-x",
            str(pos_x),
            "--window-y",
            str(pos_y),
            "--window-width",
            str(self.layout_config.width),
            "--window-height",
            str(self.layout_config.height),
            "--no-audio",
            "--stay-awake",
        ]
        cmd.extend(extra_args)
        try:
            return subprocess.Popen(
                cmd, cwd=self.scrcpy_path.parent, shell=False, env=env
            )
        except Exception as e:
            log.error(f"[{self.record.name}] Failed to launch scrcpy: {e}")
            return None

    def open_viewer(
        self,
        rank: int = 0,
        new_display: str | None = None,
        start_app: str | None = None,
        show_touches: bool = False,
    ) -> subprocess.Popen | None:
        """Opens a standard scrcpy viewer window for this device."""
        return self._launch_scrcpy_process(
            rank=rank,
            new_display=new_display,
            start_app=start_app,
            show_touches=show_touches,
        )

    def open_ghost_viewer(self, rank: int = 0) -> subprocess.Popen | None:
        """Opens a scrcpy viewer with the physical device screen turned off to save battery."""
        return self._launch_scrcpy_process(rank=rank, extra_args=["--turn-screen-off"])

    def open_virtual_viewer(
        self,
        rank: int = 0,
        display: str = "1920x1080",
        start_app: str | None = None,
    ) -> subprocess.Popen | None:
        """Opens a viewer on an isolated virtual display (Android 10+).

        Background automation that leaves the physical screen untouched.
        """
        return self._launch_scrcpy_process(
            rank=rank, new_display=display, start_app=start_app
        )
