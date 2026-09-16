"""Primary Android device controller implementing Pymordial's PymordialController contract."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pymordial.core.blueprints.extract_strategy import PymordialExtractStrategy
from pymordial.core.controller import PymordialController
from pymordial.core.plugin import PymordialPlugin
from pymordial.core.registry import PluginRegistry
from pymordial.ui.element import PymordialElement

from pymordialdroid.android_app import AndroidApp
from pymordialdroid.config import SystemConfig, resolve_system_config
from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.scrcpy_device import ScrcpyDevice
from pymordialdroid.devices.tesseract_device import TesseractDevice
from pymordialdroid.devices.ui_device import AndroidUiDevice

log = logging.getLogger("pymordialdroid")


class AndroidController(PymordialController):
    """Main controller coordinating ADB bridge, Scrcpy viewer, UI vision, and OCR for an Android device."""

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 5555,
        device_name: str = "AndroidDevice",
        pin: str | None = None,
        apps: list[AndroidApp] | None = None,
        system_config: SystemConfig | None = None,
        touch_input_width: int | None = None,
        touch_input_height: int | None = None,
    ) -> None:
        super().__init__(apps=apps)  # type: ignore[arg-type]
        self.ip = ip
        self.port = port
        self.device_name = device_name
        self.pin = pin
        self.system_config = system_config or resolve_system_config()

        self.registry = PluginRegistry()

        # 1. Resolve ADB Device
        self.adb: AdbDevice = self._resolve_plugin(
            "adb",
            lambda: AdbDevice(
                host=self.ip,
                port=self.port,
                system_config=self.system_config,
                touch_input_width=touch_input_width,
                touch_input_height=touch_input_height,
            ),
        )
        self.bridge: AdbDevice = self.adb

        # 2. Resolve OCR Device
        self.ocr: TesseractDevice = self._resolve_plugin(
            "ocr",
            lambda: TesseractDevice(system_config=self.system_config),
        )

        # 3. Resolve UI Device
        def configure_ui(plugin: PymordialPlugin) -> None:
            if hasattr(plugin, "set_bridge_device"):
                plugin.set_bridge_device(self.adb)
            if hasattr(plugin, "set_ocr_device"):
                plugin.set_ocr_device(self.ocr)

        self.ui: AndroidUiDevice = self._resolve_plugin(
            "ui",
            lambda: AndroidUiDevice(bridge_device=self.adb, ocr_device=self.ocr),
            configure_found_plugin=configure_ui,
        )

        # 4. Resolve Scrcpy Device
        def configure_scrcpy(plugin: PymordialPlugin) -> None:
            if hasattr(plugin, "set_bridge_device"):
                plugin.set_bridge_device(self.adb)

        self.scrcpy: ScrcpyDevice = self._resolve_plugin(
            "scrcpy",
            lambda: ScrcpyDevice(
                bridge_adb=self.adb,
                ip=self.ip,
                port=self.port,
                device_name=self.device_name,
                pin=self.pin,
                system_config=self.system_config,
            ),
            configure_found_plugin=configure_scrcpy,
        )

        # Link controller to all registered apps
        if apps:
            for app in apps:
                app.pymordial_controller = self

    def _resolve_plugin(
        self,
        name: str,
        default_factory: Callable[[], PymordialPlugin],
        configure_found_plugin: Callable[[PymordialPlugin], None] | None = None,
    ) -> Any:
        """Resolves a plugin from the registry or creates a default."""
        try:
            plugin = self.registry.get(name)
            if configure_found_plugin:
                configure_found_plugin(plugin)
            return plugin
        except KeyError:
            default_plugin = default_factory()
            self.registry.register(default_plugin)
            return default_plugin

    def add_app(self, app: AndroidApp) -> None:
        """Registers an application with this controller."""
        super().add_app(app)
        app.pymordial_controller = self

    # --- PymordialController Abstract Implementations ---

    def capture_screen(self) -> bytes | None:
        """Captures the current screen, preferring live stream, then feed."""
        try:
            frame = self.scrcpy.get_latest_frame()
            if frame:
                return frame
        except Exception:
            pass
        return self.bridge.capture_screenshot()

    def click_coord(self, coords: tuple[int, int], times: int = 1) -> bool:
        """Clicks specific coordinates on the device screen."""
        return self.bridge.tap(coords, times=times)

    def tap(self, x: int, y: int, times: int = 1) -> bool:
        """Taps screen coordinates (x, y)."""
        return self.bridge.tap(coords=x, y=y, times=times)

    def swipe(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: int = 300,
    ) -> bool:
        """Performs a touch swipe gesture."""
        return self.bridge.swipe(
            start_x=start_x,
            start_y=start_y,
            end_x=end_x,
            end_y=end_y,
            duration=duration,
        )

    # --- Low-level touch injection (sendevent; thin delegation) ---

    @property
    def touch_available(self) -> bool:
        """True when low-level sendevent injection is usable on the device."""
        return self.bridge.touch_available

    def touch_down(self, x: float, y: float, slot: int = 0) -> bool:
        """Presses a contact down at (x, y) on the given multi-touch slot."""
        return self.bridge.touch_down(x, y, slot=slot)

    def touch_move(self, x: float, y: float, slot: int = 0) -> bool:
        """Moves an already-down contact to (x, y) on the given slot."""
        return self.bridge.touch_move(x, y, slot=slot)

    def touch_up(self, slot: int = 0) -> bool:
        """Lifts the contact on the given slot."""
        return self.bridge.touch_up(slot=slot)

    def touch_hold(self, x: float, y: float, duration_ms: int, slot: int = 0) -> bool:
        """Holds a contact down at (x, y) for ``duration_ms``, then releases."""
        return self.bridge.touch_hold(x, y, duration_ms, slot=slot)

    def held_touch(self, x: float, y: float, slot: int = 0):  # type: ignore[no-untyped-def]
        """Context manager holding a contact down for the block's duration."""
        return self.bridge.held_touch(x, y, slot=slot)

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
        """Drags (x1, y1) -> (x2, y2) emitting ``steps`` interpolated moves."""
        return self.bridge.precise_drag(
            x1, y1, x2, y2, steps=steps, step_delay_ms=step_delay_ms, slot=slot
        )

    def find_element(
        self,
        pymordial_element: PymordialElement,
        pymordial_screenshot: bytes | None = None,
        max_tries: int = 1,
    ) -> tuple[int, int] | None:
        """Finds the coordinates of a UI element on screen."""
        return self.ui.where_element(
            pymordial_element,
            screenshot=pymordial_screenshot,
            max_tries=max_tries,
        )

    def is_element_visible(
        self,
        pymordial_element: PymordialElement,
        pymordial_screenshot: bytes | None = None,
        max_tries: int | None = None,
    ) -> bool:
        """Checks if a UI element is visible on screen."""
        tries = max_tries if max_tries is not None else 1
        return (
            self.find_element(pymordial_element, pymordial_screenshot, max_tries=tries)
            is not None
        )

    def click_element(
        self,
        pymordial_element: PymordialElement,
        times: int = 1,
        screenshot_img_bytes: bytes | None = None,
        max_tries: int = 1,
    ) -> bool:
        """Finds and clicks a UI element on screen."""
        coords = self.find_element(
            pymordial_element,
            pymordial_screenshot=screenshot_img_bytes,
            max_tries=max_tries,
        )
        if coords is not None:
            return self.click_coord(coords, times=times)
        return False

    def click_elements(
        self,
        pymordial_elements: list[PymordialElement],
        screenshot_img_bytes: bytes | None = None,
        max_tries: int = 1,
    ) -> bool:
        """Clicks the first matching element from a list."""
        for element in pymordial_elements:
            if self.click_element(
                element,
                screenshot_img_bytes=screenshot_img_bytes,
                max_tries=max_tries,
            ):
                return True
        return False

    def open_app(
        self,
        app_name: str | AndroidApp,
        package_name: str | None = None,
        timeout: int = 60,
        wait_time: int = 2,
    ) -> bool:
        """Opens an Android application on the device."""
        if isinstance(app_name, AndroidApp):
            pkg = package_name or getattr(app_name, "package_name", None)
            display_name = getattr(
                app_name, "name", getattr(app_name, "app_name", str(app_name))
            )
        else:
            pkg = package_name
            display_name = app_name

        return self.bridge.open_app(
            package_name=pkg or "",
            app_name=display_name,
            timeout=timeout,
            wait_time=wait_time,
        )

    def close_app(
        self,
        package_name: str | AndroidApp | None = None,
        app_name: str | None = None,
        timeout: int = 30,
        wait_time: int = 1,
    ) -> bool:
        """Closes an Android application on the device."""
        if isinstance(package_name, AndroidApp):
            pkg = getattr(package_name, "package_name", None)
            name = getattr(
                package_name,
                "name",
                getattr(package_name, "app_name", None),
            )
        else:
            pkg = package_name
            name = app_name

        return self.bridge.close_app(
            package_name=pkg,
            app_name=name,
            timeout=timeout,
            wait_time=wait_time,
        )

    def close_all_apps(self, exclude: list[str] | None = None) -> int:
        """Force stops all third-party apps."""
        return self.bridge.close_all_apps(exclude=exclude)

    def get_current_app(self) -> str | None:
        """Returns currently focused app package name."""
        return self.bridge.get_current_app()

    def read_text(
        self,
        image_path: Path | bytes | str,
        case_sensitive: bool = False,
        strategy: PymordialExtractStrategy | None = None,
    ) -> list[str]:
        """Reads text lines from image using OCR."""
        return self.ui.read_text(
            image_path, case_sensitive=case_sensitive, strategy=strategy
        )

    def check_text(
        self,
        text_to_find: str,
        image_path: Path | bytes | str,
        case_sensitive: bool = False,
        strategy: PymordialExtractStrategy | None = None,
    ) -> bool:
        """Checks if text exists in image using OCR."""
        return self.ui.check_text(
            text_to_find,
            image_path,
            case_sensitive=case_sensitive,
            strategy=strategy,
        )

    # --- Convenience Device Operations ---

    def go_home(self) -> None:
        """Presses device home button."""
        self.bridge.go_home()

    def go_back(self) -> None:
        """Presses device back button."""
        self.bridge.go_back()

    def press_enter(self) -> None:
        """Presses Enter key."""
        self.bridge.press_enter()

    def press_esc(self) -> None:
        """Presses Escape/Back key."""
        self.bridge.press_esc()

    def run_command(self, command: str, decode: bool = True) -> str | bytes | None:
        """Executes raw shell command on device."""
        return self.bridge.run_command(command, decode=decode)

    def type_text(self, text: str, enter: bool = False) -> bool:
        """Types text on the device."""
        return self.bridge.type_text(text, enter=enter)

    def open_viewer(
        self,
        rank: int = 0,
        ghost: bool = False,
        new_display: str | None = None,
        start_app: str | None = None,
        show_touches: bool = False,
    ) -> bool:
        """Opens scrcpy viewer window (optionally on a virtual display)."""
        return self.scrcpy.open(
            rank=rank,
            ghost=ghost,
            new_display=new_display,
            start_app=start_app,
            show_touches=show_touches,
        )

    def open_virtual_display(
        self,
        rank: int = 0,
        display: str = "1920x1080",
        start_app: str | None = None,
    ) -> bool:
        """Opens a viewer on an isolated virtual display (Android 10+)."""
        return self.scrcpy.open_virtual_display(
            rank=rank, display=display, start_app=start_app
        )

    def close_viewer(self) -> bool:
        """Closes scrcpy viewer window."""
        return self.scrcpy.close()

    # --- Headless recording (Feature 2) ---

    def start_recording(
        self,
        output_path: str | Path | None = None,
        show_touches: bool = False,
        time_limit: int | None = None,
        record_orientation: int | None = None,
    ) -> Path | None:
        """Starts headless MP4 evidence recording (no window)."""
        return self.scrcpy.start_recording(
            output_path=output_path,
            show_touches=show_touches,
            time_limit=time_limit,
            record_orientation=record_orientation,
        )

    def stop_recording(self) -> Path | None:
        """Stops headless recording, returning the output path (if any)."""
        return self.scrcpy.stop_recording()

    def is_recording(self) -> bool:
        """Checks if headless recording is running."""
        return self.scrcpy.is_recording()

    # --- Headless live feed (Feature 1) ---

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
        """Starts a headless live stream and routes screenshots through it.

        Delegates to the live stream (direct scrcpy-server H.264 via PyAV).
        The legacy MKV file feed was removed because scrcpy cannot decode
        frames without a display surface when ``--no-window`` is used.
        """
        ok = self.scrcpy.start_headless_feed(
            output_path=output_path,
            max_size=max_size,
            max_fps=max_fps,
            bit_rate=bit_rate,
            new_display=new_display,
            start_app=start_app,
            timeout=timeout,
        )
        if ok and hasattr(self.bridge, "set_frame_provider"):
            try:
                self.bridge.set_frame_provider(self.scrcpy.get_latest_frame)
            except Exception:
                pass
        return ok

    def stop_headless_feed(self) -> bool:
        """Stops the headless feed and restores plain screencap captures."""
        if hasattr(self.bridge, "clear_frame_provider"):
            try:
                # Only clear if the live stream is not still providing frames.
                if not self.scrcpy.is_live_running():
                    self.bridge.clear_frame_provider()
            except Exception:
                pass
        return self.scrcpy.stop_headless_feed()

    def get_latest_frame(self) -> bytes | None:
        """Returns the latest headless-feed frame (or None if unavailable)."""
        try:
            return self.scrcpy.get_latest_frame()
        except Exception:
            return None

    # --- Sub-100ms live stream ---

    def start_live_stream(
        self,
        max_size: int = 960,
        bit_rate: str | int = "2M",
        max_fps: float | None = 30,
        new_display: str | None = None,
        timeout: float = 20.0,
    ) -> bool:
        """Starts a direct H.264 live stream and routes screenshots through it."""
        ok = self.scrcpy.start_live_stream(
            max_size=max_size,
            bit_rate=bit_rate,
            max_fps=max_fps,
            new_display=new_display,
            timeout=timeout,
        )
        if ok and hasattr(self.bridge, "set_frame_provider"):
            try:
                self.bridge.set_frame_provider(self.scrcpy.get_latest_frame)
            except Exception:
                pass
        return ok

    def stop_live_stream(self) -> bool:
        """Stops the live stream and restores plain screencap captures."""
        if hasattr(self.bridge, "clear_frame_provider"):
            try:
                # Only clear if no other feed is still providing frames.
                if not self.scrcpy.is_feed_running():
                    self.bridge.clear_frame_provider()
            except Exception:
                pass
        return self.scrcpy.stop_live_stream()

    def is_live_running(self) -> bool:
        """Checks if the sub-100ms live stream is running."""
        return self.scrcpy.is_live_running()

    def get_live_stats(self) -> dict:
        """Returns live-stream latency measurements (age_ms, fps, ...)."""
        return self.scrcpy.get_live_stats()

    def unlock_device(self, pin: str | None = None) -> None:
        """Unlocks device screen with PIN."""
        self.scrcpy.unlock_device(pin=pin)


__all__ = [
    "AndroidController",
]
