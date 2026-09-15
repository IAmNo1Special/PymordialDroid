"""Scrcpy host runner managing display streaming, ghost mode, and physical screen controls."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import TYPE_CHECKING, Any

from pymordialdroid.config import SystemConfig, get_default_pin, resolve_system_config
from pymordialdroid.window import WindowLayoutConfig, get_viewer_window_title

if TYPE_CHECKING:
    from pymordialdroid.devices.adb_device import AdbDevice

log = logging.getLogger("pymordialdroid")


class ScrcpyDevice:
    """Manages scrcpy desktop display streaming, ghost mode, and hardware controls.

    Operates as the physical device host/display runner in PymordialDroid.
    """

    name: str = "scrcpy"
    version: str = "0.1.0"

    def __init__(
        self,
        bridge_adb: AdbDevice,
        ip: str = "127.0.0.1",
        port: int = 5555,
        device_name: str = "AndroidDevice",
        pin: str | None = None,
        system_config: SystemConfig | None = None,
        layout_config: WindowLayoutConfig | None = None,
    ) -> None:
        self._bridge_adb = bridge_adb
        self.ip = ip
        self.port = port
        self.device_name = device_name
        self.pin = pin
        self.system_config = system_config or resolve_system_config()
        self.layout_config = layout_config or WindowLayoutConfig()

        self._process: subprocess.Popen | None = None
        self._ghost_mode: bool = False

    def initialize(self, config: Any = None) -> None:
        """Initializes the scrcpy plugin."""
        pass

    def shutdown(self) -> None:
        """Terminates active scrcpy process."""
        self.close()

    def set_bridge_device(self, bridge: AdbDevice) -> None:
        """Injects/replaces the ADB bridge (plugin wiring)."""
        self._bridge_adb = bridge

    def _ensure_adb_endpoint(self) -> None:
        """Registers the endpoint with the CLI adb server for scrcpy binaries."""
        self._bridge_adb.ensure_cli_endpoint()

    def open(
        self,
        rank: int = 0,
        ghost: bool = False,
        extra_args: list[str] | None = None,
    ) -> bool:
        """Launches a scrcpy display stream window."""
        if self.is_running():
            return True

        args = extra_args or []
        if ghost:
            args.append("--turn-screen-off")
            self._ghost_mode = True
        else:
            self._ghost_mode = False

        self._ensure_adb_endpoint()

        env = os.environ.copy()
        env["ADB"] = str(self.system_config.adb_bin_path)

        pos_x, pos_y = self.layout_config.get_position(rank)
        window_title = get_viewer_window_title(rank, self.device_name)

        cmd = [
            str(self.system_config.scrcpy_bin_path),
            "--serial",
            f"{self.ip}:{self.port}",
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
        cmd.extend(args)

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=self.system_config.scrcpy_bin_path.parent,
                shell=False,
                env=env,
            )
            return True
        except Exception as e:
            log.error(f"Failed to launch scrcpy: {e}")
            return False

    def close(self) -> bool:
        """Terminates the active scrcpy process."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            return True
        return False

    def is_running(self) -> bool:
        """Checks if the scrcpy process is actively executing."""
        return self._process is not None and self._process.poll() is None

    # --- PHYSICAL SCREEN CONTROLS ---

    def toggle_power(self) -> None:
        """Wakes the physical screen via power button event."""
        self._bridge_adb.run_command("input keyevent 26")

    def unlock_device(self, pin: str | None = None) -> None:
        """Wakes screen and inputs PIN if locked."""
        resolved_pin = pin or self.pin or get_default_pin()

        # 1. Wake
        self._bridge_adb.run_command("input keyevent 26")
        time.sleep(0.5)

        # 2. Dismiss swipe
        self._bridge_adb.run_command("input keyevent 82")
        time.sleep(0.5)

        # 3. Enter PIN
        # (numeric PIN needs no shell quoting)
        self._bridge_adb.run_command(f"input text {resolved_pin}")
        time.sleep(0.2)

        # 4. Enter
        self._bridge_adb.run_command("input keyevent 66")


__all__ = [
    "ScrcpyDevice",
]
