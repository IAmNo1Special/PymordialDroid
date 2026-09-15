"""Unit tests for Phone in pymordialdroid.device."""

from unittest.mock import AsyncMock

from pymordialdroid.android_controller import AndroidController
from pymordialdroid.device import Phone, parse_battery_level


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


def test_parse_battery_level_clean():
    """Standard dumpsys output parses to percent."""
    assert parse_battery_level("  status: 3\n  level: 58\n") == "58%"


def test_parse_battery_level_rejects_noise():
    """Device warnings and log lines must not pollute the reading.

    Regression: A16 dumpsys intermittently emits
    'Failed to write while dumping service battery', which the old
    split(':') parser embedded into battery_level.
    """
    noisy = (
        "Failed to write while dumping service battery\n"
        "  status: 3\n"
        "  level: 96\n"
        "  Capacity level: -1\n"
        "09-12 Sending ACTION_BATTERY_CHANGED: level:70, status:3\n"
    )
    assert parse_battery_level(noisy) == "96%"
    assert parse_battery_level("  Capacity level: -1\n") is None
    assert parse_battery_level("") is None
    assert parse_battery_level("no battery here") is None


def test_phone_get_battery_ignores_device_noise(
    tmp_path, mock_signer, sample_device_record
):
    """get_battery keeps a clean value despite dumpsys warnings."""
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
    phone.shell = AsyncMock(  # type: ignore[method-assign]
        return_value="Failed to write while dumping service battery\n  level: 96\n"
    )
    import asyncio

    asyncio.run(phone.get_battery())
    assert phone.battery_level == "96%"
    assert "\n" not in phone.battery_level
