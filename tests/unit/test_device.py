"""Unit tests for Phone in pymordialdroid.device."""

from unittest.mock import AsyncMock, MagicMock

from pymordialdroid.android_app import AndroidApp
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


def test_phone_controller_same_port_reuses_instance(
    tmp_path, mock_signer, sample_device_record
):
    """Same port returns the same controller (no rebuild, no shutdown)."""
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
    assert phone.controller is phone.controller


def test_phone_controller_port_change_shuts_down_old_and_carries_apps(
    tmp_path, mock_signer, sample_device_record
):
    """A port change (e.g. after auto-heal) retires the old controller.

    Regression: the old controller was silently replaced — its live
    stream and recording kept running with no handle left to stop them,
    and apps registered on it vanished from the new controller while
    still pointing at the dead one.
    """
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()
    sample_device_record.port = 37821
    sample_device_record.serial = "192.168.1.50:37821"
    phone = Phone(
        record=sample_device_record,
        signer=mock_signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )
    old = phone.controller
    app = AndroidApp(app_name="DemoApp", package_name="com.demo")
    old.add_app(app)

    stream = MagicMock()
    stream.is_running.return_value = True
    old.scrcpy._live_stream = stream
    rec_proc = MagicMock()
    rec_proc.poll.return_value = None
    old.scrcpy._record_process = rec_proc
    old.scrcpy._record_path = tmp_path / "evidence.mp4"
    assert old.scrcpy.is_live_running() is True
    assert old.scrcpy.is_recording() is True

    # Auto-heal moves the record to port 5555 (device._try_auto_heal_tcpip).
    phone.record.port = 5555
    phone.record.serial = "192.168.1.50:5555"
    new = phone.controller

    assert new is not old
    assert new.port == 5555
    # Old resources were actually stopped, not just dropped.
    stream.stop.assert_called_once()
    rec_proc.terminate.assert_called_once()
    # Registered apps carried over and re-pointed at the new controller.
    assert new.list_apps() == ["DemoApp"]
    assert app.pymordial_controller is new


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
