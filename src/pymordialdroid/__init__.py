"""PymordialDroid: Android device automation and fleet management using Scrcpy and ADB."""

from pymordialdroid.android_app import AndroidApp
from pymordialdroid.android_controller import AndroidController
from pymordialdroid.cli import main
from pymordialdroid.config import (
    DATA_DIR,
    SystemConfig,
    get_app_config,
    get_default_pin,
    resolve_system_config,
)
from pymordialdroid.device import Phone
from pymordialdroid.devices import (
    AdbDevice,
    AndroidUiDevice,
    ScrcpyDevice,
    TesseractDevice,
)
from pymordialdroid.discovery import discover_usb_devices, scan_hotspot_devices
from pymordialdroid.fleet import FleetCommander
from pymordialdroid.models import DeviceRecord
from pymordialdroid.tui import FleetTUI
from pymordialdroid.utils.extract_strategies import DefaultExtractStrategy
from pymordialdroid.window import (
    WindowLayoutConfig,
    get_viewer_window_title,
    organize_windows_grid,
)

__all__ = [
    "DATA_DIR",
    "AdbDevice",
    "AndroidApp",
    "AndroidController",
    "AndroidUiDevice",
    "DefaultExtractStrategy",
    "DeviceRecord",
    "FleetCommander",
    "FleetTUI",
    "Phone",
    "ScrcpyDevice",
    "SystemConfig",
    "TesseractDevice",
    "WindowLayoutConfig",
    "discover_usb_devices",
    "get_app_config",
    "get_default_pin",
    "get_viewer_window_title",
    "main",
    "organize_windows_grid",
    "resolve_system_config",
    "scan_hotspot_devices",
]

__version__ = "0.1.0"
