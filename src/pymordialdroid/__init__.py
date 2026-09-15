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
from pymordialdroid.device import Phone, parse_battery_level
from pymordialdroid.devices import (
    AdbDevice,
    AndroidUiDevice,
    ScrcpyDevice,
    TesseractDevice,
)
from pymordialdroid.discovery import (
    discover_usb_devices,
    get_arp_candidate_ips,
    get_inventory_subnet_bases,
    get_local_subnet_bases,
    query_device_name_via_adb,
    scan_hotspot_devices,
    scan_subnets_for_phones,
)
from pymordialdroid.fleet import FleetCommander
from pymordialdroid.live_stream import (
    ScrcpyLiveStream,
    build_server_argv,
    detect_server_version,
    find_server_blob,
    parse_bit_rate,
)
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
    "ScrcpyLiveStream",
    "SystemConfig",
    "TesseractDevice",
    "WindowLayoutConfig",
    "build_server_argv",
    "detect_server_version",
    "discover_usb_devices",
    "find_server_blob",
    "get_app_config",
    "get_arp_candidate_ips",
    "get_default_pin",
    "get_inventory_subnet_bases",
    "get_local_subnet_bases",
    "get_viewer_window_title",
    "main",
    "organize_windows_grid",
    "parse_battery_level",
    "parse_bit_rate",
    "query_device_name_via_adb",
    "resolve_system_config",
    "scan_hotspot_devices",
    "scan_subnets_for_phones",
]

__version__ = "0.1.0"
