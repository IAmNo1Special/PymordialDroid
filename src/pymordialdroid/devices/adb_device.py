"""Android Debug Bridge (ADB) device implementation for PymordialDroid."""

import logging
import re
import time
from pathlib import Path
from typing import Any

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from pymordial.core.blueprints.bridge_device import PymordialBridgeDevice

from pymordialdroid.config import SystemConfig, resolve_system_config

log = logging.getLogger("pymordialdroid")


class AdbDevice(PymordialBridgeDevice):
    """Handles Android device communication via pure Python ADB.

    Fulfills the PymordialBridgeDevice contract with native package resolution,
    activity detection, focused window parsing, and resilient app lifecycle commands.
    """

    name: str = "adb"
    version: str = "0.1.0"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5555,
        signer: PythonRSASigner | None = None,
        system_config: SystemConfig | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.system_config = system_config or resolve_system_config()
        self.signer = signer or self.system_config.get_signer()

        self._device: AdbDeviceTcp | None = None
        self._latest_frame: bytes | None = None
        self._is_streaming: bool = False

    def initialize(self, config: Any = None) -> None:
        """Initializes the ADB device plugin."""
        pass

    def shutdown(self) -> None:
        """Disconnects and cleans up resources."""
        self.disconnect()

    # --- CONNECTION MANAGEMENT ---

    def connect(self) -> bool:
        """Connects to the Android device via TCP socket with RSA authentication."""
        log.debug(f"Connecting ADB device to {self.host}:{self.port}...")
        if self._device is None:
            self._device = AdbDeviceTcp(
                self.host, self.port, default_transport_timeout_s=9
            )

        if self._device.available:
            return True

        try:
            self._device.connect(rsa_keys=[self.signer], auth_timeout_s=5)
            log.info(f"Connected to device {self.host}:{self.port}")
            return True
        except Exception as e:
            log.warning(f"Error connecting to ADB device {self.host}:{self.port}: {e}")
            self._device = None
            return False

    def is_connected(self) -> bool:
        """Checks if ADB connection is currently active and responsive."""
        return self._device is not None and self._device.available

    def disconnect(self) -> bool:
        """Disconnects the ADB device."""
        self.stop_stream()
        if self._device is None:
            return True
        try:
            self._device.close()
            self._device = None
            log.debug(f"Disconnected from ADB device {self.host}:{self.port}")
            return True
        except Exception as e:
            log.error(f"Error disconnecting ADB device: {e}")
            return False

    def run_command(self, command: str, decode: bool = True) -> str | bytes | None:
        """Executes a shell command on the device."""
        if not self.is_connected():
            if not self.connect():
                return None
        try:
            output = self._device.shell(command, decode=decode)
            return output.strip() if decode and isinstance(output, str) else output
        except Exception as e:
            log.error(f"Failed to execute command '{command}': {e}")
            return None

    # --- ANDROID PACKAGE & ACTIVITY RESOLUTION ---

    def find_package_by_keyword(self, keyword: str) -> str | None:
        """Finds an installed package matching a keyword using 'pm list packages'."""
        output = self.run_command("pm list packages", decode=True)
        if not output or not isinstance(output, str):
            return None

        packages = [
            line.replace("package:", "").strip()
            for line in output.splitlines()
            if line.strip()
        ]

        # 1. Exact match
        if keyword in packages:
            return keyword

        # 2. Case-insensitive substring match (shortest name wins)
        matches = [pkg for pkg in packages if keyword.lower() in pkg.lower()]
        if matches:
            return min(matches, key=len)

        return None

    def get_launch_activity(self, package_name: str) -> str | None:
        """Queries Android's activity manager to determine the exact launchable activity."""
        cmd = f"cmd package resolve-activity --brief {package_name}"
        output = self.run_command(cmd, decode=True)
        if not output or not isinstance(output, str):
            return None

        lines = output.strip().splitlines()
        if lines:
            activity = lines[-1].strip()
            if "/" in activity and "No activity found" not in activity:
                return activity

        return None

    def get_focused_app(self) -> dict[str, str] | None:
        """Parses 'dumpsys window' to detect the currently focused package and activity."""
        output = self.run_command(
            "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", decode=True
        )
        if not output or not isinstance(output, str):
            return None

        match = re.search(r"([a-zA-Z0-9._]+)/([a-zA-Z0-9._$]+)", output)
        if match:
            pkg, activity = match.groups()
            return {"package": pkg, "activity": activity}

        return None

    # --- APPLICATION LIFECYCLE ---

    def open_app(
        self,
        package_name: str,
        app_name: str | None = None,
        timeout: float = 10.0,
        wait_time: float = 1.0,
    ) -> bool:
        """Launches an app via resolved Activity or Monkey fallback, verifying it started."""
        pkg = package_name or (
            self.find_package_by_keyword(app_name) if app_name else None
        )
        if not pkg:
            log.error(f"Could not resolve package for app: {app_name}")
            return False

        # 1. Try activity-based launch first (fast and deterministic)
        activity = self.get_launch_activity(pkg)
        if activity:
            self.run_command(f"am start -n {activity}")
            log.debug(f"Launched {pkg} via activity: {activity}")
        else:
            # 2. Fallback to Monkey intent launcher
            self.run_command(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
            log.debug(f"Launched {pkg} via Monkey fallback")

        # Verify app is running within timeout
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.is_app_running(pkg, max_retries=1, wait_time=0):
                return True
            time.sleep(wait_time)

        log.warning(f"App {pkg} did not start within {timeout}s")
        return False

    def close_app(
        self,
        package_name: str | None = None,
        app_name: str | None = None,
        timeout: float = 5.0,
        wait_time: float = 0.5,
    ) -> bool:
        """Force stops an app and polls pidof to confirm closure."""
        pkg = package_name or (
            self.find_package_by_keyword(app_name) if app_name else None
        )
        if not pkg:
            log.error(f"Could not resolve package to close for app: {app_name}")
            return False

        self.run_command(f"am force-stop {pkg}")

        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.is_app_running(pkg, max_retries=1, wait_time=0):
                return True
            time.sleep(wait_time)

        return not self.is_app_running(pkg, max_retries=1, wait_time=0)

    def is_app_running(
        self,
        package_name: str | None = None,
        app_name: str | None = None,
        max_retries: int = 2,
        wait_time: float = 1.0,
    ) -> bool:
        """Checks if an app is actively running by querying process PID."""
        pkg = package_name or (
            self.find_package_by_keyword(app_name) if app_name else None
        )
        if not pkg:
            return False

        for attempt in range(max_retries):
            output = self.run_command(f"pidof {pkg}", decode=True)
            if output and str(output).strip():
                return True
            if attempt < max_retries - 1 and wait_time > 0:
                time.sleep(wait_time)

        return False

    def show_recent_apps(self) -> bool:
        """Opens recent apps / overview."""
        res = self.run_command("input keyevent 187")
        return res is not None

    def close_all_apps(self, exclude: list[str] | None = None) -> int:
        """Force stops all installed third-party/user packages to clear device state."""
        output = self.run_command("pm list packages", decode=True)
        if not output or not isinstance(output, str):
            return 0

        packages = [
            line.replace("package:", "").strip()
            for line in output.splitlines()
            if line.strip()
        ]
        exclude_list = exclude or []
        count = 0

        for pkg in packages:
            if pkg in exclude_list:
                continue
            self.run_command(f"am force-stop {pkg}")
            count += 1

        log.debug(f"Closed {count} applications.")
        return count

    def get_current_app(self) -> str | None:
        """Gets the package name of the currently focused application."""
        focused = self.get_focused_app()
        if focused and "package" in focused:
            return focused["package"]
        return None

    # --- USER INPUT ACTIONS ---

    def tap(
        self,
        coords: tuple[int, int] | list[int] | int,
        y: int | None = None,
        times: int = 1,
    ) -> bool:
        """Sends tap events to coordinates on screen.

        Accepts either a tuple/list (x, y) or separate x, y integers.
        """
        if isinstance(coords, (tuple, list)):
            target_x, target_y = coords[0], coords[1]
        elif y is not None:
            target_x, target_y = coords, y
        else:
            log.warning(f"Invalid tap coordinates: coords={coords}, y={y}")
            return False

        for _ in range(times):
            self.run_command(f"input tap {target_x} {target_y}")
            if times > 1:
                time.sleep(0.1)
        return True

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 300,
    ) -> bool:
        """Performs a touch swipe gesture from start to end coordinates."""
        res = self.run_command(
            f"input swipe {start_x} {start_y} {end_x} {end_y} {duration}"
        )
        return res is not None

    def type_text(self, text: str, enter: bool = False) -> bool:
        """Types text into the focused input field, escaping shell characters."""
        # Escape characters that could break ADB shell input
        escaped_text = text.replace("\\", "\\\\")
        escaped_text = escaped_text.replace(" ", "%s")
        escaped_text = escaped_text.replace("'", "\\'")
        escaped_text = escaped_text.replace('"', '\\"')
        escaped_text = escaped_text.replace("&", "\\&")
        escaped_text = escaped_text.replace("<", "\\<")
        escaped_text = escaped_text.replace(">", "\\>")
        escaped_text = escaped_text.replace(";", "\\;")
        escaped_text = escaped_text.replace("|", "\\|")

        res = self.run_command(f"input text '{escaped_text}'")
        if enter:
            self.press_enter()
        return res is not None

    def go_home(self) -> None:
        """Simulates Home button press."""
        self.run_command("input keyevent 3")

    def go_back(self) -> None:
        """Simulates Back button press."""
        self.run_command("input keyevent 4")

    def press_enter(self) -> None:
        """Simulates Enter key press."""
        self.run_command("input keyevent 66")

    def press_esc(self) -> None:
        """Simulates Back / Escape key press."""
        self.run_command("input keyevent 4")

    # --- SCREENSHOT & STREAMING (Pure ADB, No PyAV) ---

    def capture_screenshot(self) -> bytes | None:
        """Captures a PNG screenshot from the device using pure 'screencap -p'."""
        if not self.is_connected():
            if not self.connect():
                return None
        try:
            raw_png = self._device.shell("screencap -p", decode=False)
            if raw_png:
                self._latest_frame = raw_png
                return raw_png
        except Exception as e:
            log.error(f"Screenshot capture failed: {e}")
        return None

    def start_stream(self) -> None:
        """Starts stream mode (tracks frames from screenshot captures)."""
        self._is_streaming = True

    def stop_stream(self) -> None:
        """Stops stream mode."""
        self._is_streaming = False

    def get_latest_frame(self) -> bytes | None:
        """Retrieves the most recent frame bytes or captures a fresh screenshot."""
        if self._latest_frame is None:
            return self.capture_screenshot()
        return self._latest_frame

    # --- APK INSTALLATION ---

    def install_apk(self, apk_path: str | Path, update: bool = True) -> bool:
        """Pushes and installs an APK file via adb."""
        path_obj = Path(apk_path)
        if not path_obj.exists():
            log.warning(f"APK not found: {apk_path}")
            return False

        adb = str(self.system_config.adb_bin_path)
        cmd = [adb, "-s", f"{self.host}:{self.port}", "install"]
        if update:
            cmd.append("-r")
        cmd.append(str(path_obj))

        import subprocess

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.returncode == 0


__all__ = [
    "AdbDevice",
]
