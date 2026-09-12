"""Example: Managing a multi-device fleet with FleetCommander.

This script demonstrates:
1. Loading a persisted fleet inventory.
2. Saving newly discovered devices.
3. Accessing per-device AndroidController instances.
4. Launching scrcpy viewers for the whole fleet.
5. Running the heartbeat monitor and mission runner in the background.
6. Performing fleet-wide actions such as installing an APK on all devices.
7. Cleaning up viewers and connections on shutdown.

Requirements:
- At least one device recorded in ~/.pymordialdroid/fleet_inventory.json.
- scrcpy and adb binaries available (bundled or on PATH).
"""

import asyncio
from pathlib import Path

from pymordialdroid import DeviceRecord, FleetCommander


def example_inventory_management(commander: FleetCommander) -> None:
    """Load inventory, inspect devices, and save new records."""
    phones = commander.load_inventory()
    print(f"Loaded {len(phones)} device(s) from inventory.")

    if not phones:
        print("Inventory is empty. Initialize via USB discovery first.")
        return

    for idx, phone in enumerate(phones):
        print(
            f"  [{idx}] {phone.record.name} @ {phone.record.ip}:{phone.record.port}"
            f"  serial={phone.record.serial}"
        )

    new_records = [
        DeviceRecord(
            serial="demo_serial_123",
            ip="192.168.1.60",
            port=5555,
            name="DemoPhone",
        ),
    ]
    commander.save_inventory(new_records)
    print(f"Saved {len(new_records)} new record(s).")


def example_per_device_control(commander: FleetCommander) -> None:
    """Retrieve controllers by index or IP and perform actions."""
    phones = commander.load_inventory()
    if not phones:
        return

    controller_by_index = commander.get_controller(0)
    if controller_by_index:
        print(f"Controller[0]: {controller_by_index.device_name}")
        controller_by_index.go_home()

    first_ip = phones[0].record.ip
    controller_by_ip = commander.get_controller(first_ip)
    if controller_by_ip:
        print(f"Controller for {first_ip}: {controller_by_ip.device_name}")


def example_viewer_lifecycle(commander: FleetCommander) -> None:
    """Launch and organize scrcpy viewers for the fleet."""
    phones = commander.load_inventory()
    if not phones:
        return

    launched = commander.batch_launch_viewers(ghost=False)
    print(f"Launched {launched} viewer(s).")

    organized = commander.organize_viewers_grid()
    print(f"Organized {organized} window(s) into a grid.")


async def example_async_fleet_workers(commander: FleetCommander) -> None:
    """Run heartbeat monitoring and a fleet-wide APK install concurrently."""
    phones = commander.load_inventory()
    if not phones:
        return

    heartbeat_task = asyncio.create_task(commander.heartbeat_monitor())
    mission_task = asyncio.create_task(commander.mission_runner())

    await asyncio.sleep(5)

    apk_path = "app-debug.apk"
    path = Path(apk_path)
    if path.exists():
        print(f"Installing {apk_path} on all devices...")
        await commander.install_apk_on_all(path, update=True)
    else:
        print(f"Skip install: {apk_path} not found.")

    commander.running = False
    await asyncio.gather(heartbeat_task, mission_task, return_exceptions=True)
    print("Background workers stopped.")


async def example_full_fleet_flow() -> None:
    """End-to-end fleet lifecycle."""
    commander = FleetCommander()

    example_inventory_management(commander)
    example_per_device_control(commander)
    example_viewer_lifecycle(commander)
    await example_async_fleet_workers(commander)

    commander.kill_viewers()
    await commander.shutdown()
    print("Fleet shutdown complete.")


if __name__ == "__main__":
    asyncio.run(example_full_fleet_flow())
