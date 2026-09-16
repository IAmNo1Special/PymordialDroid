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
from pymordialdroid.devices.motionevent_injector import MotionEventInjector
from pymordialdroid.devices.scrcpy_control import ScrcpyControlInjector
from pymordialdroid.devices.scrcpy_control import (
    _find_server_blob as _find_scrcpy_server_blob,
)
from pymordialdroid.devices.touch_injector import TouchInjector, detect_input_size

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
        touch_input_width: int | None = None,
        touch_input_height: int | None = None,
        scrcpy_control: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.system_config = system_config or resolve_system_config()
        self.signer = signer or self.system_config.get_signer()

        self._device: AdbDeviceTcp | None = None
        self._latest_frame: bytes | None = None
        self._is_streaming: bool = False
        self._frame_provider: Any = None

        # Low-level touch injection. Coordinates passed to the touch_* API use
        # the same input space as tap()/swipe() (the display's current input
        # space, e.g. 2340x1080 landscape for Revomon Novus). When None, the
        # input space is auto-detected (dumpsys -> wm size) — only the
        # sendevent backend needs this; `input motionevent` and scrcpy-control
        # take display pixels directly (scrcpy-control still needs the size
        # for its packet header, resolved the same way).
        self._touch_input_width = touch_input_width
        self._touch_input_height = touch_input_height
        self._touch_injector: TouchInjector | None = None
        self._motionevent_injector: MotionEventInjector | None = None
        self._scrcpy_injector: ScrcpyControlInjector | None = None
        self._scrcpy_control_enabled = scrcpy_control  # eager daemon on 1st use
        self._cli_endpoint_ensured = False
        self._sp_backend: Any = None  # single-pointer backend, cached
        self._sp_backend_probed = False

    def initialize(self, config: Any = None) -> None:
        """Initializes the ADB device plugin."""
        pass

    def shutdown(self) -> None:
        """Disconnects and cleans up resources."""
        self.disconnect()

    # --- CONNECTION MANAGEMENT ---

    def _try_auto_heal_tcpip(self) -> bool:
        """Attempts to auto-heal synchronous ADB connection via CLI binary."""
        import subprocess

        adb_bin = str(self.system_config.adb_bin_path)
        log.info(f"Attempting auto-heal for ADB device {self.host}:{self.port}...")
        try:
            if self.port != 5555:
                endpoint = f"{self.host}:{self.port}"
                subprocess.run(
                    [adb_bin, "connect", endpoint],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                subprocess.run(
                    [adb_bin, "-s", endpoint, "tcpip", "5555"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )

            subprocess.run(
                [adb_bin, "connect", f"{self.host}:5555"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            self.port = 5555
            self._device = AdbDeviceTcp(self.host, 5555, default_transport_timeout_s=9)
            self._device.connect(rsa_keys=[self.signer], auth_timeout_s=5)
            log.info(f"Auto-healed ADB connection to {self.host}:5555")
            return True
        except Exception as e:
            log.warning(f"AdbDevice auto-heal failed: {e}")
            self._device = None
            return False

    def connect(self, auto_heal: bool = True) -> bool:
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
            if auto_heal and self._try_auto_heal_tcpip():
                return True
            return False

    def ensure_cli_endpoint(self) -> bool:
        """Registers this endpoint with the CLI adb server (`adb connect`).

        Required before launching ``scrcpy`` binaries / live-stream helpers,
        which shell out to ``adb.exe`` and cannot see pure-Python
        :class:`AdbDeviceTcp` sockets. Best-effort: never raises.
        """
        import subprocess

        try:
            subprocess.run(
                [
                    str(self.system_config.adb_bin_path),
                    "connect",
                    f"{self.host}:{self.port}",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except Exception:
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

    @staticmethod
    def parse_package_list(output: str) -> list[str]:
        """Extracts package names from `pm list packages` output.

        Only ``package:``-prefixed lines are kept; device warnings (e.g.
        Samsung multi-user ``SecurityException`` preambles) are ignored so
        they can never match a keyword or be force-stopped.
        """
        packages = []
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("package:"):
                name = stripped[len("package:") :].strip()
                if name:
                    packages.append(name)
        return packages

    @staticmethod
    def rank_package(keyword: str, packages: list[str]) -> str | None:
        """Ranks packages for a keyword: exact > component > substring.

        1. Case-insensitive exact match.
        2. Keyword equals a dot-separated component (``settings`` matches
           ``com.android.settings`` but not ``com.sec.usbsettings``);
           ties break toward the shortest full name.
        3. Plain substring match; ties break toward the shortest full name.
        """
        kw = keyword.lower()
        for pkg in packages:
            if pkg.lower() == kw:
                return pkg
        component = [p for p in packages if kw in p.lower().split(".")]
        if component:
            return min(component, key=len)
        substring = [p for p in packages if kw in p.lower()]
        if substring:
            return min(substring, key=len)
        return None

    def find_package_by_keyword(self, keyword: str) -> str | None:
        """Finds an installed package matching a keyword using 'pm list packages'."""
        output = self.run_command("pm list packages", decode=True)
        if not output or not isinstance(output, str):
            return None

        packages = self.parse_package_list(output)

        # 1. Exact match (preserves historical fast path)
        if keyword in packages:
            return keyword

        return self.rank_package(keyword, packages)

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

        packages = self.parse_package_list(output)
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

    # --- LOW-LEVEL TOUCH INJECTION (backend chain) ---
    #
    # Priority: scrcpy-control (full multi-touch + single-pointer, no root;
    # preferred once its daemon is up) -> `input motionevent` (single-pointer,
    # works on non-rooted retail devices, cheap probe) -> sendevent (rooted /
    # permissive devices; real multi-touch slots) -> legacy `input swipe`
    # fallbacks where semantically possible. Bare down/move/up stay honest:
    # False is returned when no backend can inject — never faked.
    #
    # Probe-cost policy: the scrcpy daemon (jar push + server spawn + adb
    # forward + handshake) is heavyweight, so it is NOT started eagerly.
    # Single-pointer traffic uses the cheap motionevent probe; the daemon
    # starts lazily on the first multi-pointer request (slot > 0) or when
    # explicitly enabled (``scrcpy_control=True`` / ``start_multitouch()``).
    # Once the daemon is up it serves every slot — it is the lowest-latency,
    # highest-fidelity path available.

    def _touch(self) -> TouchInjector:
        """Lazily creates the sendevent touch injector for this device."""
        if self._touch_injector is None:
            self._touch_injector = TouchInjector(
                self.run_command,
                input_width=self._touch_input_width,
                input_height=self._touch_input_height,
            )
        return self._touch_injector

    def _motionevent(self) -> MotionEventInjector:
        """Lazily creates the `input motionevent` injector for this device."""
        if self._motionevent_injector is None:
            self._motionevent_injector = MotionEventInjector(self.run_command)
        return self._motionevent_injector

    def _scrcpy_control(self) -> ScrcpyControlInjector | None:
        """Lazily creates the scrcpy-control multi-touch injector.

        Returns None when the server blob cannot be found on the host (the
        daemon can never start). Registers the TCP endpoint with the CLI adb
        server once, since the daemon shells out to the adb binary.
        """
        if self._scrcpy_injector is None:
            try:
                adb_bin = str(self.system_config.adb_bin_path)
                scrcpy_bin = getattr(self.system_config, "scrcpy_bin_path", None)
                blob = _find_scrcpy_server_blob(
                    adb_bin, str(scrcpy_bin) if scrcpy_bin is not None else None
                )
            except Exception as e:
                log.debug(f"scrcpy-control: blob lookup failed: {e}")
                return None
            if blob is None:
                log.debug("scrcpy-control: server blob not found on host")
                return None
            if not self._cli_endpoint_ensured:
                self._cli_endpoint_ensured = True
                self.ensure_cli_endpoint()
            w, h = self._touch_input_width, self._touch_input_height
            if w is None or h is None:
                try:
                    detected = detect_input_size(self.run_command)
                except Exception:
                    detected = None
                if detected is not None:
                    w, h = detected
            self._scrcpy_injector = ScrcpyControlInjector(
                adb_bin,
                f"{self.host}:{self.port}",
                blob,
                screen_width=w,
                screen_height=h,
            )
        return self._scrcpy_injector

    def start_multitouch(self, timeout: float = 20.0) -> bool:
        """Explicitly starts the scrcpy-control multi-touch daemon.

        After this returns True, all touch traffic (including single-pointer)
        routes through the daemon. Returns False when the daemon cannot
        start (no server blob, adb unreachable, handshake timeout).
        """
        injector = self._scrcpy_control()
        if injector is None:
            return False
        return injector.ensure_started(timeout=timeout)

    def _single_pointer_backend(self) -> Any:
        """Cheap cached chain for slot 0: motionevent -> sendevent."""
        if not self._sp_backend_probed:
            self._sp_backend_probed = True
            backend: Any = None
            motionevent = self._motionevent()
            if motionevent.available:
                backend = motionevent
                log.info("touch backend: input motionevent")
            else:
                sendevent = self._touch()
                if sendevent.available:
                    backend = sendevent
                    log.info("touch backend: sendevent")
            self._sp_backend = backend
        return self._sp_backend

    def _touch_backend(self, slot: int = 0) -> Any:
        """Selects the best touch backend for the given slot.

        Daemon already up -> scrcpy-control serves everything. Multi-pointer
        (slot > 0) or explicitly enabled -> lazy daemon start, then sendevent
        for real multi-touch slots, else None (honest). Single-pointer ->
        the cheap cached motionevent/sendevent chain.
        """
        scrcpy = self._scrcpy_control()
        if scrcpy is not None:
            if scrcpy.daemon_running:
                return scrcpy
            if slot > 0 or self._scrcpy_control_enabled:
                if scrcpy.ensure_started():
                    log.info("touch backend: scrcpy-control")
                    return scrcpy
                log.warning(
                    "scrcpy-control daemon failed to start; "
                    "falling back to remaining backends"
                )
        if slot > 0:
            sendevent = self._touch()
            if sendevent.available:
                log.info("touch backend: sendevent (multi-touch)")
                return sendevent
            return None
        return self._single_pointer_backend()

    @property
    def touch_available(self) -> bool:
        """True when any low-level touch backend can inject on the device.

        Cheap: checks the single-pointer chain only. The scrcpy-control
        daemon is NOT started by this property (use ``start_multitouch()``
        or a slot>0 call to bring it up).
        """
        return self._touch_backend(0) is not None

    def touch_down(self, x: float, y: float, slot: int = 0) -> bool:
        """Presses a contact down at (x, y) on the given multi-touch slot.

        Coordinates use the same input space as :meth:`tap`/:meth:`swipe`.
        Returns False when no touch backend is available (no fallback — a
        bare down has no ``input`` equivalent). Multi-pointer slots (slot>0)
        lazily start the scrcpy-control daemon; when it cannot start,
        sendevent (rooted) is tried, else False is returned honestly.
        """
        backend = self._touch_backend(slot)
        if backend is None:
            return False
        return backend.touch_down(x, y, slot=slot)

    def touch_move(self, x: float, y: float, slot: int = 0) -> bool:
        """Moves an already-down contact to (x, y) on the given slot."""
        backend = self._touch_backend(slot)
        if backend is None:
            return False
        return backend.touch_move(x, y, slot=slot)

    def touch_up(self, slot: int = 0) -> bool:
        """Lifts the contact on the given slot."""
        backend = self._touch_backend(slot)
        if backend is None:
            return False
        return backend.touch_up(slot=slot)

    def touch_cancel(self, slot: int = 0) -> bool:
        """Cancels the active gesture on the given slot (stuck-gesture recovery).

        Returns False when no touch backend is available.
        """
        backend = self._touch_backend(slot)
        if backend is None:
            return False
        return backend.touch_cancel(slot=slot)

    def touch_hold(self, x: float, y: float, duration_ms: int, slot: int = 0) -> bool:
        """Holds a contact down at (x, y) for ``duration_ms``, then releases.

        Falls back to an ``input swipe`` long-press (same start/end point)
        when no touch backend is available.
        """
        backend = self._touch_backend(slot)
        if backend is not None:
            return backend.touch_hold(x, y, duration_ms, slot=slot)
        log.warning(
            "touch injection unavailable; falling back to input swipe long-press"
        )
        return self.swipe(int(x), int(y), int(x), int(y), duration=duration_ms)

    def held_touch(self, x: float, y: float, slot: int = 0):  # type: ignore[no-untyped-def]
        """Context manager holding a contact down for the block's duration.

        Enables two-thumb play: hold the joystick on slot 0 while issuing
        ``touch_move`` / ``precise_drag`` on slot 1 (scrcpy-control daemon
        when available, else sendevent on rooted devices — motionevent is
        single-pointer). Always releases on exit.
        """
        backend = self._touch_backend(slot)
        if backend is None:
            raise RuntimeError("touch injection unavailable; no usable backend")
        return backend.held_touch(x, y, slot=slot)

    def precise_drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        steps: int = 24,
        step_delay_ms: int = 16,
        slot: int = 0,
    ) -> bool:
        """Drags (x1, y1) -> (x2, y2) emitting ``steps`` interpolated moves.

        The fine-control primitive: unlike :meth:`swipe` (one coarse kernel
        swipe), the dense move-event stream lets games that gate on drag
        distance/velocity register small, precise drags. Falls back to
        :meth:`swipe` when no touch backend is available.
        """
        backend = self._touch_backend(slot)
        if backend is not None:
            return backend.precise_drag(
                x1, y1, x2, y2, steps=steps, step_delay_ms=step_delay_ms, slot=slot
            )
        log.warning("touch injection unavailable; falling back to input swipe")
        return self.swipe(
            int(x1), int(y1), int(x2), int(y2), duration=steps * step_delay_ms
        )

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

    def set_frame_provider(self, provider: Any | None) -> None:
        """Sets an optional headless-feed frame provider.

        The provider is a zero-arg callable returning PNG bytes (or None).
        Typically ``ScrcpyDevice.get_latest_frame``. When set,
        :meth:`capture_screenshot` tries it first for a high-FPS frame and
        falls back to ``screencap -p`` on None/exception.
        """
        self._frame_provider = provider

    def clear_frame_provider(self) -> None:
        """Removes the headless-feed frame provider."""
        self._frame_provider = None

    def capture_screenshot(self) -> bytes | None:
        """Captures a PNG screenshot, preferring the scrcpy headless feed.

        Tries the frame provider first (fast path); falls back to pure
        'screencap -p' so behavior never regresses when no feed is running.
        """
        if self._frame_provider is not None:
            try:
                frame = self._frame_provider()
            except Exception as e:
                log.debug(f"Frame provider failed, falling back to screencap: {e}")
                frame = None
            if frame:
                self._latest_frame = frame
                return frame
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
