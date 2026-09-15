"""Unit tests for automated ADB error handling and auto-healing."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from adb_shell.exceptions import InvalidCommandError

from pymordialdroid.device import Phone
from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.fleet import FleetCommander
from pymordialdroid.models import DeviceRecord


@pytest.fixture
def ephemeral_device_record():
    return DeviceRecord(
        serial="172.20.8.50:42759",
        ip="172.20.8.50",
        port=42759,
        name="Galaxy S24 Test",
    )


def test_phone_auto_heal_stls_error(tmp_path, mocker, ephemeral_device_record):
    """Test that Phone.connect() catches STLS InvalidCommandError and auto-heals to port 5555."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    mock_tcp = mocker.patch("pymordialdroid.device.AdbDeviceTcpAsync")
    mock_inst = mock_tcp.return_value
    stls_error = InvalidCommandError("Unknown command: 1397511251 = 'b'STLS''")
    mock_inst.connect = AsyncMock(side_effect=[stls_error, True])
    mock_inst.close = AsyncMock()

    signer = mocker.MagicMock()
    phone = Phone(
        record=ephemeral_device_record,
        signer=signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    # Mock subprocess.run inside _run_adb
    mock_run = mocker.patch("subprocess.run")
    mock_run.return_value = mocker.MagicMock(
        returncode=0,
        stdout="List of devices attached\n",
    )

    connected = asyncio.run(phone.connect(auto_heal=True))

    assert connected is True
    assert phone.record.port == 5555
    assert phone.record.serial == "172.20.8.50:5555"
    assert phone.status == "Online"
    assert phone.last_action == "Auto-Healed (Port 5555)"


def test_phone_auto_heal_mdns_candidate(tmp_path, mocker, ephemeral_device_record):
    """Test that Phone._try_auto_heal_tcpip finds attached mDNS device and enables tcpip 5555."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    mock_tcp = mocker.patch("pymordialdroid.device.AdbDeviceTcpAsync")
    mock_inst = mock_tcp.return_value
    mock_inst.connect = AsyncMock(side_effect=[ConnectionRefusedError("Port closed"), True])
    mock_inst.close = AsyncMock()

    signer = mocker.MagicMock()
    phone = Phone(
        record=ephemeral_device_record,
        signer=signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    mdns_output = (
        "List of devices attached\n"
        "adb-R5CX51P0AMK-2QBiR4._adb-tls-connect._tcp device product:e3qsqw\n"
    )
    ip_output = "172.20.0.0/16 dev wlan0 proto kernel scope link src 172.20.8.50\n"

    def fake_subprocess_run(cmd, **kwargs):
        cmd_str = " ".join(str(c) for c in cmd)
        res = mocker.MagicMock()
        res.returncode = 0
        if "devices -l" in cmd_str:
            res.stdout = mdns_output
        elif "shell ip route" in cmd_str:
            res.stdout = ip_output
        else:
            res.stdout = ""
        return res

    mocker.patch("subprocess.run", side_effect=fake_subprocess_run)

    connected = asyncio.run(phone.connect(auto_heal=True))

    assert connected is True
    assert phone.record.port == 5555
    assert phone.status == "Online"


def test_phone_connect_no_auto_heal(tmp_path, mocker, ephemeral_device_record):
    """Test that auto_heal=False does not trigger auto-healing and returns False."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    signer = mocker.MagicMock()
    phone = Phone(
        record=ephemeral_device_record,
        signer=signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    phone.device.connect = AsyncMock(side_effect=ConnectionRefusedError("Refused"))
    mock_heal = mocker.patch.object(phone, "_try_auto_heal_tcpip")

    connected = asyncio.run(phone.connect(auto_heal=False))

    assert connected is False
    assert phone.status == "Connection Failed"
    mock_heal.assert_not_called()


def test_fleet_heartbeat_persists_on_auto_heal(tmp_path, mocker, ephemeral_device_record):
    """Test that FleetCommander.heartbeat_monitor saves inventory when a device auto-heals."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()

    signer = mocker.MagicMock()
    phone = Phone(
        record=ephemeral_device_record,
        signer=signer,
        adb_path=fake_adb,
        scrcpy_path=fake_scrcpy,
    )

    fc = FleetCommander()
    fc.phones = [phone]
    fc.save_inventory = mocker.MagicMock()

    # Simulate phone.connect healing to 5555
    async def fake_connect(auto_heal=True):
        phone.record.port = 5555
        phone.record.serial = f"{phone.record.ip}:5555"
        phone._connected = True
        return True

    mocker.patch.object(phone, "connect", side_effect=fake_connect)

    async def runner():
        async def stop_after_cycle():
            await asyncio.sleep(0.05)
            fc.running = False

        await asyncio.gather(
            fc.heartbeat_monitor(),
            stop_after_cycle(),
        )

    asyncio.run(runner())

    assert fc.save_inventory.called
    assert phone.record.port == 5555


def test_adb_device_sync_auto_heal(tmp_path, mocker):
    """Test that AdbDevice.connect() auto-heals dynamic ports synchronously."""
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()

    mock_cfg = mocker.MagicMock()
    mock_cfg.adb_bin_path = fake_adb
    mock_signer = mocker.MagicMock()

    adb_dev = AdbDevice(
        host="172.20.8.50",
        port=42759,
        signer=mock_signer,
        system_config=mock_cfg,
    )

    mocker.patch("subprocess.run", return_value=mocker.MagicMock(returncode=0))

    # Mock AdbDeviceTcp
    mock_tcp = mocker.patch("pymordialdroid.devices.adb_device.AdbDeviceTcp")
    tcp_instance = mock_tcp.return_value
    tcp_instance.available = False
    # First connect fails, second succeeds after heal
    tcp_instance.connect.side_effect = [
        InvalidCommandError("STLS"),
        None,
    ]

    ok = adb_dev.connect(auto_heal=True)

    assert ok is True
    assert adb_dev.port == 5555
