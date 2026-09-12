"""Unit tests for FleetCommander in pymordialdroid.fleet."""

from pymordialdroid.android_controller import AndroidController
from pymordialdroid.device import Phone
from pymordialdroid.fleet import FleetCommander


def test_fleet_get_controller(tmp_path, mocker, sample_device_record):
    """Test retrieving controller by index or IP from FleetCommander."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    phone = Phone(
        record=sample_device_record,
        signer=mocker.MagicMock(),
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    fleet = FleetCommander()
    fleet.phones = [phone]

    controller = phone.controller
    assert isinstance(controller, AndroidController)

    assert fleet.get_controller(0) is controller
    assert fleet.get_controller("192.168.1.50") is controller
    assert fleet.get_controller("192.168.1.50:5555") is controller
    assert fleet.get_controller(99) is None
    assert fleet.get_controller("10.0.0.1") is None
