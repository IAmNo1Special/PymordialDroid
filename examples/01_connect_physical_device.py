"""Example: Connecting to a physical Android phone via USB and wireless ADB.

This script demonstrates three connection patterns:
1. Auto-discovering USB devices and enabling wireless TCP/IP mode.
2. Establishing an explicit ADB connection with AdbDevice.
3. Using AndroidController with lazy auto-connect on first command.

Requirements:
- A physical Android device with USB debugging enabled.
- ADB binary available (bundled in pymordialdroid/bin/scrcpy/ or on PATH).
"""

from pymordialdroid import (
    AdbDevice,
    AndroidController,
    DeviceRecord,
    discover_usb_devices,
    resolve_system_config,
)


def example_usb_discovery() -> list[DeviceRecord]:
    """Discover USB-attached devices and enable persistent wireless ADB."""
    config = resolve_system_config()
    print("Scanning USB devices...")

    records = discover_usb_devices(
        adb_path=config.adb_bin_path,
        prompt_for_names=True,
    )

    if not records:
        print("No USB devices found. Ensure USB debugging is enabled.")
        return []

    for rec in records:
        print(f"Discovered: {rec.name} @ {rec.ip}:{rec.port} (serial={rec.serial})")

    return records


def example_explicit_adb_connection(ip: str, port: int = 5555) -> AdbDevice | None:
    """Connect to a device using the low-level AdbDevice bridge."""
    device = AdbDevice(host=ip, port=port)

    print(f"Connecting to {ip}:{port}...")
    connected = device.connect()
    if not connected:
        print("Connection failed.")
        return None

    print("Connected.")

    ping = device.run_command("echo ping")
    print(f"Shell test: {ping}")

    screenshot = device.capture_screenshot()
    if screenshot:
        print(f"Screenshot captured: {len(screenshot)} bytes")
    else:
        print("Screenshot failed.")

    device.disconnect()
    print("Disconnected.")
    return device


def example_controller_lazy_connect(
    ip: str, port: int = 5555
) -> AndroidController | None:
    """Use AndroidController, which auto-connects on the first command."""
    controller = AndroidController(ip=ip, port=port, device_name="DemoPhone")

    print("Tap triggers lazy ADB connect...")
    ok = controller.tap(100, 200)
    print(f"Tap result: {ok}")

    screenshot = controller.capture_screen()
    if screenshot:
        print(f"Screen captured via controller: {len(screenshot)} bytes")
    else:
        print("Screen capture failed.")

    return controller


def example_wireless_pairing_flow() -> None:
    """Show the recommended wireless ADB pairing workflow."""
    config = resolve_system_config()
    adb = config.adb_bin_path

    device_ip = "192.168.1.50"
    device_port = 5555

    print(f"Pairing with {device_ip}:{device_port}...")

    import subprocess

    subprocess.run(
        [str(adb), "connect", f"{device_ip}:{device_port}"],
        check=False,
    )

    controller = AndroidController(ip=device_ip, port=device_port)
    controller.go_home()
    print("Wireless connection verified.")


if __name__ == "__main__":
    records = example_usb_discovery()

    if records:
        rec = records[0]
        example_explicit_adb_connection(rec.ip, rec.port)
        example_controller_lazy_connect(rec.ip, rec.port)
    else:
        print("Plug in a device and re-run to test the full flow.")

    example_wireless_pairing_flow()
