"""Unit tests for scrcpy 1+2+3 extensions: feed, recording, virtual display."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.scrcpy_device import ScrcpyDevice


def _make_scrcpy(tmp_path, name="TestDevice"):
    from pymordialdroid.config import SystemConfig

    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()
    cfg = SystemConfig(scrcpy_bin_path=fake_scrcpy, adb_bin_path=fake_adb)
    bridge = MagicMock()
    return ScrcpyDevice(
        bridge_adb=bridge,
        ip="192.168.1.50",
        port=5555,
        device_name=name,
        system_config=cfg,
    )


def test_open_virtual_display_flags(tmp_path):
    device = _make_scrcpy(tmp_path)
    with (
        patch("subprocess.run"),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        assert device.open_virtual_display(rank=1, display="1920x1080", start_app="com.example") is True
        cmd = mock_popen.call_args[0][0]
        assert "--new-display=1920x1080" in cmd
        assert "--no-vd-system-decorations" in cmd
        assert "--start-app=com.example" in cmd
        assert "--turn-screen-off" not in cmd


def test_open_ghost_ignored_with_new_display(tmp_path):
    device = _make_scrcpy(tmp_path)
    with (
        patch("subprocess.run"),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        assert device.open(rank=0, ghost=True, new_display="1920x1080") is True
        cmd = mock_popen.call_args[0][0]
        assert "--new-display=1920x1080" in cmd
        assert "--turn-screen-off" not in cmd


def test_recording_lifecycle(tmp_path):
    device = _make_scrcpy(tmp_path)
    out = tmp_path / "clip.mp4"
    with (
        patch("subprocess.run"),
        patch("subprocess.Popen") as mock_popen,
    ):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        path = device.start_recording(output_path=out, show_touches=True, time_limit=10)
        assert path == out
        assert device.is_recording() is True
        cmd = mock_popen.call_args[0][0]
        assert "--no-window" in cmd
        assert "--record" in cmd
        assert "--show-touches" in cmd
        assert "--time-limit" in cmd
        # Idempotent second start returns same path without new Popen.
        assert device.start_recording(output_path=out) == out
        assert mock_popen.call_count == 1
        stopped = device.stop_recording()
        assert stopped == out
        assert device.is_recording() is False
        assert device.stop_recording() is None


def test_headless_feed_lifecycle(tmp_path, mocker):
    device = _make_scrcpy(tmp_path)
    feed = tmp_path / "feed.mkv"
    # start_headless_feed now delegates to start_live_stream
    mocker.patch.object(device, "start_live_stream", return_value=True)
    mocker.patch.object(device, "is_live_running", side_effect=[True, False, False])
    mocker.patch.object(device, "stop_live_stream", side_effect=[True, False])

    assert device.start_headless_feed(output_path=feed) is True
    assert device.is_feed_running() is True
    device.start_live_stream.assert_called_once()
    # No live stream yet -> frame is None, never raises.
    assert device.get_latest_frame() is None
    assert device.stop_headless_feed() is True
    assert device.is_feed_running() is False
    assert device.stop_headless_feed() is False


def test_get_latest_frame_uses_live_stream(tmp_path, mocker):
    device = _make_scrcpy(tmp_path)
    fake_frame = MagicMock()
    mock_live = MagicMock()
    mock_live.get_latest_numpy.return_value = fake_frame
    device._live_stream = mock_live

    mock_cv2 = MagicMock()
    mock_cv2.imencode.return_value = (True, MagicMock(tobytes=lambda: b"PNG"))
    import sys

    with patch.dict(sys.modules, {"cv2": mock_cv2}):
        assert device.get_latest_frame() == b"PNG"
    mock_live.get_latest_numpy.assert_called_once()


def test_adb_capture_prefers_frame_provider(mocker):
    adb = AdbDevice.__new__(AdbDevice)
    adb._latest_frame = None
    adb._frame_provider = lambda: b"FEEDPNG"
    adb.is_connected = lambda: True  # type: ignore[method-assign]
    assert adb.capture_screenshot() == b"FEEDPNG"
    adb._frame_provider = lambda: None
    adb._device = MagicMock()
    adb._device.shell.return_value = b"SCREENCAP"
    assert adb.capture_screenshot() == b"SCREENCAP"


def test_controller_feed_wires_provider(mocker):
    from pymordialdroid.android_controller import AndroidController

    controller = AndroidController(ip="192.168.1.50", port=5555)
    mocker.patch.object(controller.scrcpy, "is_feed_running", return_value=True)
    mocker.patch.object(controller.scrcpy, "get_latest_frame", return_value=b"FEED")
    mocker.patch.object(controller.bridge, "capture_screenshot", return_value=b"ADB")
    assert controller.capture_screen() == b"FEED"

    mocker.patch.object(controller.scrcpy, "start_headless_feed", return_value=True)
    spy = mocker.patch.object(controller.bridge, "set_frame_provider")
    assert controller.start_headless_feed() is True
    spy.assert_called_once_with(controller.scrcpy.get_latest_frame)


def test_controller_recording_and_virtual_display(mocker):
    from pymordialdroid.android_controller import AndroidController

    controller = AndroidController()
    mocker.patch.object(controller.scrcpy, "start_recording", return_value=Path("a.mp4"))
    assert controller.start_recording() == Path("a.mp4")
    mocker.patch.object(controller.scrcpy, "open_virtual_display", return_value=True)
    assert controller.open_virtual_display() is True
    mock_open = mocker.patch.object(controller.scrcpy, "open", return_value=True)
    controller.open_viewer(rank=2, new_display="1920x1080", start_app="com.x")
    mock_open.assert_called_once_with(
        rank=2, ghost=False, new_display="1920x1080", start_app="com.x", show_touches=False
    )


def test_cli_record_feed_parsers():
    from pymordialdroid.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["record", "start", "--show-touches"])
    assert args.command == "record" and args.action == "start"
    assert args.show_touches is True
    args = parser.parse_args(["record", "evidence", "--duration", "30"])
    assert args.duration == 30
    args = parser.parse_args(["feed", "start", "--new-display", "1920x1080"])
    assert args.action == "start" and args.new_display == "1920x1080"
    # Live backend is the default; MKV file-tail is opt-in.
    args = parser.parse_args(["feed", "start"])
    assert args.backend == "live" and args.max_size == 960
    args = parser.parse_args(["feed", "start", "--backend", "mkv"])
    assert args.backend == "mkv"


# --- Sub-100ms live stream (direct scrcpy-server H.264) ---


def _encode_test_h264(width=320, height=240, frames=5):
    """Encodes synthetic frames, returning raw Annex-B bytes."""
    from fractions import Fraction

    import numpy as np

    av = pytest.importorskip("av")
    enc = av.CodecContext.create("h264", "w")
    enc.width = width
    enc.height = height
    enc.pix_fmt = "yuv420p"
    enc.framerate = Fraction(30, 1)
    enc.time_base = Fraction(1, 30)
    raw = b""
    for i in range(frames):
        arr = np.full((height, width, 3), (i * 40) % 256, dtype=np.uint8)
        for packet in enc.encode(av.VideoFrame.from_ndarray(arr, format="bgr24")):
            raw += bytes(packet)
    for packet in enc.encode(None):
        raw += bytes(packet)
    assert len(raw) > 0
    return raw


def test_parse_bit_rate():
    from pymordialdroid.live_stream import parse_bit_rate

    assert parse_bit_rate("2M") == 2_000_000
    assert parse_bit_rate("500K") == 500_000
    assert parse_bit_rate("8000000") == 8_000_000
    assert parse_bit_rate(1_000_000) == 1_000_000
    with pytest.raises(ValueError):
        parse_bit_rate("fast")


def test_build_server_argv_exact():
    """Server argv must match the scrcpy 4.1 wire protocol exactly."""
    from pymordialdroid.live_stream import build_server_argv

    argv = build_server_argv(
        "adb",
        "192.168.1.50:5555",
        "4.1",
        0x12345678,
        max_size=960,
        bit_rate=2_000_000,
        max_fps=30,
    )
    assert argv == [
        "adb",
        "-s",
        "192.168.1.50:5555",
        "shell",
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        "4.1",
        "scid=12345678",
        "log_level=error",
        "audio=false",
        "control=false",
        "tunnel_forward=true",
        "raw_stream=true",
        "max_size=960",
        "video_bit_rate=2000000",
        "max_fps=30",
        "stay_awake=true",
    ]


def test_build_server_argv_virtual_display():
    from pymordialdroid.live_stream import build_server_argv

    argv = build_server_argv(
        "adb", "s", "4.1", 1, new_display="1920x1080", stay_awake=False
    )
    assert "new_display=1920x1080" in argv
    assert "vd_system_decorations=false" in argv
    assert "stay_awake=true" not in argv
    assert "control=false" in argv


def test_h264_parser_roundtrip_chunked():
    """Incremental Annex-B parsing must survive arbitrary chunk splits."""
    from pymordialdroid.live_stream import H264StreamParser

    raw = _encode_test_h264()
    parser = H264StreamParser()
    try:
        frames = []
        step = 977  # deliberately unaligned to NAL boundaries
        for i in range(0, len(raw), step):
            frames.extend(parser.feed(raw[i : i + step]))
        assert len(frames) >= 1
        assert frames[-1].shape == (240, 320, 3)
    finally:
        parser.close()


def test_live_reader_over_socketpair(tmp_path):
    """End-to-end reader: socket bytes in, BGR frame + stats out."""
    import socket
    import threading

    from pymordialdroid.live_stream import ScrcpyLiveStream

    raw = _encode_test_h264()
    blob = tmp_path / "scrcpy-server"
    blob.touch()
    stream = ScrcpyLiveStream(
        serial="test:5555", adb_bin="adb", server_blob=blob
    )
    srv, cli = socket.socketpair()
    stream._sock = cli
    stream._pending_chunk = raw[:1000]
    rest = raw[1000:]
    reader = threading.Thread(target=stream._reader, daemon=True)
    reader.start()
    try:
        srv.sendall(rest)
        assert stream._first_frame.wait(timeout=10)
        frame = stream.get_latest_numpy()
        assert frame is not None and frame.shape == (240, 320, 3)
        assert stream.get_latest_png() is not None
        stats = stream.get_stats()
        assert stats.has_frame and stats.frames_decoded >= 1
        assert stats.width == 320 and stats.height == 240
        assert stats.age_ms is not None and stats.age_ms < 60_000
    finally:
        stream._stop_event.set()
        try:
            srv.close()
        except Exception:
            pass
        try:
            cli.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        reader.join(timeout=5)


def test_controller_live_stream_wiring(mocker):
    from pymordialdroid.android_controller import AndroidController

    controller = AndroidController(ip="192.168.1.50", port=5555)
    mocker.patch.object(controller.scrcpy, "start_live_stream", return_value=True)
    mocker.patch.object(controller.scrcpy, "is_feed_running", return_value=False)
    spy = mocker.patch.object(controller.bridge, "set_frame_provider")
    assert controller.start_live_stream(max_size=960) is True
    controller.scrcpy.start_live_stream.assert_called_once_with(
        max_size=960, bit_rate="2M", max_fps=30, new_display=None, timeout=20.0
    )
    spy.assert_called_once_with(controller.scrcpy.get_latest_frame)

    # Live frames win over ADB screencap.
    mocker.patch.object(controller.scrcpy, "get_latest_frame", return_value=b"LIVE")
    mocker.patch.object(controller.bridge, "capture_screenshot", return_value=b"ADB")
    assert controller.capture_screen() == b"LIVE"

    # Stopping live with no MKV feed clears the provider.
    mocker.patch.object(controller.scrcpy, "is_live_running", return_value=False)
    mocker.patch.object(controller.scrcpy, "stop_live_stream", return_value=True)
    clear = mocker.patch.object(controller.bridge, "clear_frame_provider")
    assert controller.stop_live_stream() is True
    clear.assert_called_once()


def test_live_stats_stale_flag():
    """Stale frames (static screen) must be distinguishable from dead stream."""
    import time

    import numpy as np

    from pymordialdroid.live_stream import ScrcpyLiveStream

    stream = ScrcpyLiveStream(serial="x:5555", adb_bin="adb", server_blob="blob")
    assert stream.get_stats().stale is False

    stream._state.latest = np.zeros((10, 10, 3), dtype=np.uint8)
    stream._state.latest_at = time.monotonic()
    fresh = stream.get_stats()
    assert fresh.has_frame is True and fresh.stale is False

    stream._state.latest_at = time.monotonic() - 5.0
    assert stream.get_stats().stale is True


def test_fleet_start_live_streams_concurrent(mocker):
    """Fleet stream starts must run in parallel, honoring targets."""
    import time

    from pymordialdroid.fleet import FleetCommander

    fleet = FleetCommander()
    phones = []
    for i in range(4):
        phone = mocker.MagicMock()
        phone.record.name = f"P{i}"
        phone.record.ip = f"10.0.0.{i + 1}"

        def _slow_start(*args, **kwargs):  # type: ignore[no-untyped-def]
            time.sleep(0.5)
            return True

        phone.start_live_stream.side_effect = _slow_start
        phones.append(phone)
    fleet.phones = phones

    start = time.monotonic()
    assert fleet.start_live_streams() == 4
    elapsed = time.monotonic() - start
    assert elapsed < 1.6, f"starts ran sequentially ({elapsed:.1f}s)"

    # Targeting restricts to the chosen subset.
    fleet2 = FleetCommander()
    fleet2.phones = phones
    assert fleet2.start_live_streams(targets=[phones[0]]) == 1
    assert fleet2.stop_live_streams(targets=[phones[0]]) <= 1
