"""Device abstraction representing a single physical Android device via ADB and Scrcpy."""

import asyncio
import logging
import os
import random
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
        if self._controller is None:
            from pymordialdroid.android_controller import AndroidController

            self._controller = AndroidController(
                ip=self.record.ip,
                port=self.record.port,
                device_name=self.record.name,
                pin=self.record.pin,
            )
        return self._controller

    async def connect(self) -> bool:
        """Asynchronously connects to the device via ADB."""
        try:
            await self.device.connect(rsa_keys=[self.signer], auth_timeout_s=5)
            self._connected = True
            self.status = "Online"
            return True
        except Exception as e:
            self.status = "Connection Failed"
            self.last_action = f"Err: {str(e)[:15]}..."
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

    async def get_battery(self) -> None:
        """Queries and updates device battery level."""
        cmd = 'dumpsys battery | grep "level:" | head -n 1'
        res = await self.shell(cmd)
        if "level:" in res:
            try:
                self.battery_level = res.split(":")[1].strip() + "%"
                self.last_action = "Checked Battery"
            except IndexError:
                pass

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

    async def record_evidence(self, duration_sec: int = 60) -> None:
        """Records gameplay/screen video using scrcpy to an MP4 file."""
        filename = f"evidence_{self.record.name}_{int(time.time())}.mp4"
        log.info(f"[{self.record.name}] Recording evidence to {filename}...")

        cmd = [
            str(self.scrcpy_path),
            "--serial",
            f"{self.record.ip}:{self.record.port}",
            "--record",
            filename,
            "--no-display",
            "--record-format",
            "mp4",
            "--max-size",
            "1024",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.scrcpy_path.parent,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def _stop_recording():
            await asyncio.sleep(duration_sec)
            log.info(f"[{self.record.name}] Stopping recording...")
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

        asyncio.create_task(_stop_recording())

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
        self, rank: int = 0, extra_args: list[str] | None = None
    ) -> None:
        """Launches a scrcpy process positioned according to grid layout."""
        if extra_args is None:
            extra_args = []

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
        subprocess.Popen(cmd, cwd=self.scrcpy_path.parent, shell=False, env=env)

    def open_viewer(self, rank: int = 0) -> None:
        """Opens a standard scrcpy viewer window for this device."""
        self._launch_scrcpy_process(rank=rank)

    def open_ghost_viewer(self, rank: int = 0) -> None:
        """Opens a scrcpy viewer with the physical device screen turned off to save battery."""
        self._launch_scrcpy_process(rank=rank, extra_args=["--turn-screen-off"])
