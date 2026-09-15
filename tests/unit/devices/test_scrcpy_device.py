"""Unit tests for ScrcpyDevice in pymordialdroid.devices."""

from unittest.mock import MagicMock, patch

from pymordialdroid.devices.scrcpy_device import ScrcpyDevice


def test_scrcpy_device_init():
    """Test ScrcpyDevice initialization."""
    bridge = MagicMock()
    device = ScrcpyDevice(
        bridge_adb=bridge, ip="192.168.1.100", port=5555, device_name="TestDevice"
    )
    assert device.name == "scrcpy"
    assert device.version == "0.1.0"
    assert device.ip == "192.168.1.100"
    assert device.port == 5555
    assert device.device_name == "TestDevice"
    assert not device.is_running()


def test_scrcpy_device_open_and_close():
    """Test launching and terminating scrcpy process."""
    bridge = MagicMock()
    device = ScrcpyDevice(bridge_adb=bridge)

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # Open
        res = device.open(rank=0, ghost=True)
        assert res is True
        assert device.is_running()
        assert device._ghost_mode is True

        # Endpoint registration delegates to the ADB bridge
        bridge.ensure_cli_endpoint.assert_called_once()

        # Check scrcpy called with ghost flag
        call_args = mock_popen.call_args[0][0]
        assert "--turn-screen-off" in call_args
        assert "--no-audio" in call_args

        # Calling open again when running returns True immediately
        assert device.open() is True

        # Close
        closed = device.close()
        assert closed is True
        mock_proc.terminate.assert_called_once()
        assert not device.is_running()


def test_scrcpy_device_shutdown():
    """Test shutdown calls close."""
    device = ScrcpyDevice(bridge_adb=MagicMock())
    with patch.object(device, "close") as mock_close:
        device.shutdown()
        mock_close.assert_called_once()


def test_toggle_power_and_unlock_use_bridge():
    """Physical controls must go through AdbDevice, never raw adb.exe."""
    bridge = MagicMock()
    device = ScrcpyDevice(bridge_adb=bridge, pin="1234")

    with patch("subprocess.run") as mock_run:
        device.toggle_power()
        device.unlock_device()
        mock_run.assert_not_called()

    commands = [c.args[0] for c in bridge.run_command.call_args_list]
    assert "input keyevent 26" in commands
    assert "input keyevent 82" in commands
    assert "input text 1234" in commands
    assert "input keyevent 66" in commands


def test_set_bridge_device_replaces_bridge():
    """Plugin wiring can swap the bridge after construction."""
    old, new = MagicMock(), MagicMock()
    device = ScrcpyDevice(bridge_adb=old)
    device.set_bridge_device(new)
    device.toggle_power()
    new.run_command.assert_called_once_with("input keyevent 26")
    old.run_command.assert_not_called()
