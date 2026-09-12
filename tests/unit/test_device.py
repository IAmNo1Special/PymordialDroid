"""Unit tests for Phone in pymordialdroid.device."""

from pymordialdroid.android_controller import AndroidController
from pymordialdroid.device import Phone


def test_phone_init_and_controller(tmp_path, mock_signer, sample_device_record):
    """Test Phone initialization and controller access."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    phone = Phone(
        record=sample_device_record,
        signer=mock_signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    assert phone.record.ip == "192.168.1.50"
    assert phone.record.port == 5555
    assert phone.status == "Offline"
    assert phone.controller is not None
    assert isinstance(phone.controller, AndroidController)
    assert phone.controller.ip == "192.168.1.50"
    assert phone.controller.device_name == "TestGalaxyS24"
