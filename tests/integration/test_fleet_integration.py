"""Integration tests for FleetCommander, Phone, and AndroidController orchestration."""

from pymordialdroid.android_controller import AndroidController
from pymordialdroid.device import Phone
from pymordialdroid.fleet import FleetCommander
from pymordialdroid.models import DeviceRecord


def test_phone_and_fleet_controller_integration(tmp_path, mocker):
    """Phone and FleetCommander provide seamless access to AndroidController."""
    rec = DeviceRecord(serial="192.168.1.50:5555", ip="192.168.1.50", name="Device1")
    phone = Phone(
        record=rec,
        signer=mocker.MagicMock(),
        adb_path=tmp_path / "adb.exe",
        scrcpy_path=tmp_path / "scrcpy.exe",
    )

    controller = phone.controller
    assert isinstance(controller, AndroidController)
    assert controller.ip == "192.168.1.50"
    assert controller.device_name == "Device1"

    fleet = FleetCommander()
    fleet.phones = [phone]
    assert fleet.get_controller(0) is controller
    assert fleet.get_controller("192.168.1.50") is controller
    assert fleet.get_controller(99) is None
