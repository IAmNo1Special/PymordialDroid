"""Unit tests for ScrcpyDevice in pymordialdroid.devices."""

from unittest.mock import MagicMock, patch

from pymordialdroid.devices.scrcpy_device import ScrcpyDevice


def test_scrcpy_device_init():
    """Test ScrcpyDevice initialization."""
    device = ScrcpyDevice(ip="192.168.1.100", port=5555, device_name="TestDevice")
    assert device.name == "scrcpy"
    assert device.version == "0.1.0"
    assert device.ip == "192.168.1.100"
    assert device.port == 5555
    assert device.device_name == "TestDevice"
    assert not device.is_running()


def test_scrcpy_device_open_and_close():
    """Test launching and terminating scrcpy process."""
    device = ScrcpyDevice()

    with (
        patch("subprocess.run") as mock_run,
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        # Open
        res = device.open(rank=0, ghost=True)
        assert res is True
        assert device.is_running()
        assert device._ghost_mode is True

        # Check connect command called
        mock_run.assert_called_once()

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
    device = ScrcpyDevice()
    with patch.object(device, "close") as mock_close:
        device.shutdown()
        mock_close.assert_called_once()
