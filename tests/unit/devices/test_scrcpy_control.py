"""Unit tests for the scrcpy-control multi-touch backend (scrcpy_control.py).

Covers: the exact 32-byte INJECT_TOUCH_EVENT packet layout, scid
bit-width validation, daemon lifecycle (push/forward/launch/dummy-byte
handshake/teardown) with faked subprocess+socket, pointer tracking and
stuck-pointer recovery, the ScrcpyControlInjector touch surface
(slot == pointer_id, lazy daemon start), and the AdbDevice backend
selection policy (lazy daemon on slot>0, explicit enable, fallbacks).
"""

import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.scrcpy_control import (
    ACTION_DOWN,
    ACTION_MOVE,
    ACTION_UP,
    MSG_TYPE_INJECT_TOUCH_EVENT,
    ScrcpyControlClient,
    ScrcpyControlInjector,
    build_touch_packet,
    validate_scid,
)

# --- packet layout ---


def test_packet_is_32_bytes_big_endian():
    pkt = build_touch_packet(ACTION_DOWN, 0, 280, 702, 2340, 1080)
    assert len(pkt) == 32
    fields = struct.unpack(">BBQiiHHHii", pkt)
    assert fields == (2, 0, 0, 280, 702, 2340, 1080, 0xFFFF, 0, 0)


def test_packet_field_positions():
    # pointer 1, MOVE, distinct coords — every field must land correctly
    pkt = build_touch_packet(ACTION_MOVE, 1, 1400, 500, 2340, 1080)
    msg_type, action, pointer_id, x, y, w, h, pressure, ab, buttons = struct.unpack(
        ">BBQiiHHHii", pkt
    )
    assert msg_type == MSG_TYPE_INJECT_TOUCH_EVENT == 2
    assert action == ACTION_MOVE == 2
    assert pointer_id == 1
    assert (x, y) == (1400, 500)
    assert (w, h) == (2340, 1080)
    assert pressure == 0xFFFF
    assert (ab, buttons) == (0, 0)


def test_packet_up_action():
    pkt = build_touch_packet(ACTION_UP, 0, 310, 600, 2340, 1080)
    assert struct.unpack(">BBQiiHHHii", pkt)[1] == 1


def test_packet_rejects_bad_action_and_pointer():
    with pytest.raises(ValueError):
        build_touch_packet(9, 0, 0, 0, 2340, 1080)
    with pytest.raises(ValueError):
        build_touch_packet(ACTION_DOWN, -1, 0, 0, 2340, 1080)


# --- scid validation ---


def test_scid_accepts_31_bit():
    assert validate_scid(0) == 0
    assert validate_scid(0x7FFFFFFF) == 0x7FFFFFFF


def test_scid_rejects_32nd_bit_and_negative():
    with pytest.raises(ValueError):
        validate_scid(0x80000000)  # Java Integer.parseInt would throw
    with pytest.raises(ValueError):
        validate_scid(-1)


# --- fakes ---


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Records argv lists; all adb calls succeed."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return FakeCompleted()


class FakeProc:
    def __init__(self, argv):
        self.argv = argv
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


class FakePopenFactory:
    def __init__(self):
        self.procs = []

    def __call__(self, argv, **kwargs):
        proc = FakeProc(argv)
        self.procs.append(proc)
        return proc


class FakeSocket:
    def __init__(self, handshake=b"\x00"):
        self.handshake = handshake
        self.sent = []
        self.closed = False
        self.shutdown_called = False

    def recv(self, n):
        data, self.handshake = self.handshake[:n], self.handshake[n:]
        return data

    def sendall(self, data):
        self.sent.append(bytes(data))

    def shutdown(self, how):
        self.shutdown_called = True

    def close(self):
        self.closed = True

    def settimeout(self, t):
        pass


def make_client(tmp_path, handshake=b"\x00", screen=(2340, 1080)):
    blob = tmp_path / "scrcpy-server"
    blob.write_bytes(b"fake-jar")
    runner = FakeRunner()
    popen = FakePopenFactory()
    sock = FakeSocket(handshake=handshake)
    client = ScrcpyControlClient(
        adb_bin="/fake/adb",
        serial="127.0.0.1:5555",
        server_blob=blob,
        screen_width=screen[0],
        screen_height=screen[1],
        runner=runner,
        popen_factory=popen,
        socket_factory=lambda: sock,
    )
    return client, runner, popen, sock


# --- client lifecycle ---


def test_start_runs_push_forward_launch_and_handshake(tmp_path):
    client, runner, popen, sock = make_client(tmp_path)
    assert client.start(timeout=5) is True
    assert client.is_running is True

    argvs = [" ".join(c) for c in runner.calls]
    assert any("push" in a and "scrcpy-server.jar" in a for a in argvs)
    fwd = next(a for a in argvs if "forward" in a and "localabstract" in a)
    assert f"localabstract:scrcpy_{client._scid:08x}" in fwd

    srv = popen.procs[0].argv
    srv_s = " ".join(srv)
    for token in (
        "app_process",
        "com.genymobile.scrcpy.Server",
        f"scid={client._scid:08x}",
        "video=false",
        "audio=false",
        "control=true",
        "send_dummy_byte=true",
        "cleanup=false",
    ):
        assert token in srv_s, token


def test_start_is_idempotent(tmp_path):
    client, runner, popen, _ = make_client(tmp_path)
    assert client.start(timeout=5) is True
    n_calls = len(runner.calls)
    assert client.start(timeout=5) is True
    assert len(runner.calls) == n_calls  # no relaunch
    assert len(popen.procs) == 1


def test_start_fails_without_dummy_byte(tmp_path):
    client, _, _, _ = make_client(tmp_path, handshake=b"")
    # socket_factory always returns the same drained socket; recv -> b""
    assert client.start(timeout=0.5) is False
    assert client.is_running is False


def test_start_fails_when_socket_refused(tmp_path):
    blob = tmp_path / "scrcpy-server"
    blob.write_bytes(b"x")

    def boom():
        raise ConnectionRefusedError("nope")

    client = ScrcpyControlClient(
        "/fake/adb",
        "127.0.0.1:5555",
        blob,
        screen_width=2340,
        screen_height=1080,
        runner=FakeRunner(),
        popen_factory=FakePopenFactory(),
        socket_factory=boom,
    )
    assert client.start(timeout=0.5) is False


def test_stop_teardown_order(tmp_path):
    client, runner, popen, sock = make_client(tmp_path)
    assert client.start(timeout=5) is True
    port = client._local_port
    client.stop()
    assert sock.closed is True
    assert popen.procs[0].terminated is True
    argvs = [" ".join(c) for c in runner.calls]
    assert any("forward" in a and "--remove" in a and str(port) in a for a in argvs)
    assert client.is_running is False
    client.stop()  # idempotent, never raises


def test_context_manager(tmp_path):
    client, _, _, _ = make_client(tmp_path)
    with client as c:
        assert c.is_running is True
    assert client.is_running is False


def test_scid_is_31_bit_on_every_client(tmp_path):
    for _ in range(50):
        client, _, _, _ = make_client(tmp_path)
        assert 0 <= client._scid < 0x80000000


# --- touch injection over the socket ---


def test_touch_down_move_up_packets(tmp_path):
    client, _, _, sock = make_client(tmp_path)
    assert client.start(timeout=5) is True

    assert client.touch_down(0, 280, 702) is True
    assert client.touch_move(0, 280, 550) is True
    assert client.touch_up(0, 280, 550) is True

    assert len(sock.sent) == 3
    assert sock.sent[0] == build_touch_packet(0, 0, 280, 702, 2340, 1080)
    assert sock.sent[1] == build_touch_packet(2, 0, 280, 550, 2340, 1080)
    assert sock.sent[2] == build_touch_packet(1, 0, 280, 550, 2340, 1080)
    assert client.active_pointers == set()


def test_touch_move_requires_active_pointer(tmp_path):
    client, _, _, sock = make_client(tmp_path)
    assert client.start(timeout=5) is True
    assert client.touch_move(1, 5, 5) is False
    assert sock.sent == []


def test_touch_up_untracks_pointer(tmp_path):
    client, _, _, _ = make_client(tmp_path)
    assert client.start(timeout=5) is True
    client.touch_down(1, 1400, 500)
    assert client.active_pointers == {1}
    client.touch_up(1, 1400, 500)
    assert client.active_pointers == set()


def test_cancel_all_releases_every_tracked_pointer(tmp_path):
    client, _, _, sock = make_client(tmp_path)
    assert client.start(timeout=5) is True
    client.touch_down(0, 280, 702)
    client.touch_move(0, 280, 550)
    client.touch_down(1, 1400, 500)
    n_before = len(sock.sent)
    client.cancel_all()
    ups = sock.sent[n_before:]
    assert len(ups) == 2
    # UP at each pointer's last known position
    assert ups[0] == build_touch_packet(1, 0, 280, 550, 2340, 1080)
    assert ups[1] == build_touch_packet(1, 1, 1400, 500, 2340, 1080)
    assert client.active_pointers == set()


def test_send_without_screen_size_fails_honestly(tmp_path):
    client, _, _, sock = make_client(tmp_path, screen=(None, None))
    client.set_screen_size  # silence linters; size intentionally unset
    assert client.start(timeout=5) is True
    assert client.touch_down(0, 1, 1) is False
    assert sock.sent == []


def test_set_screen_size_validation(tmp_path):
    client, _, _, _ = make_client(tmp_path)
    with pytest.raises(ValueError):
        client.set_screen_size(0, 1080)
    with pytest.raises(ValueError):
        client.set_screen_size(70000, 1080)


# --- injector surface (slot == pointer_id) ---


class FakeClient:
    """Stand-in for ScrcpyControlClient recording touch calls."""

    def __init__(self, blob_path, screen=(2340, 1080)):
        self.server_blob = Path(blob_path)
        self.calls = []
        self._running = False
        self._pointers = set()
        self._last_pos = {}
        self._screen = screen
        self.started = 0

    @property
    def is_running(self):
        return self._running

    @property
    def active_pointers(self):
        return set(self._pointers)

    def ensure_started(self):
        return True

    def start(self, timeout=20.0):
        self.started += 1
        self._running = True
        return True

    def stop(self):
        self._running = False

    def set_screen_size(self, w, h):
        self._screen = (w, h)

    def touch_down(self, pid, x, y):
        self.calls.append(("down", pid, x, y))
        self._pointers.add(pid)
        self._last_pos[pid] = (x, y)
        return True

    def touch_move(self, pid, x, y):
        if pid not in self._pointers:
            return False
        self.calls.append(("move", pid, x, y))
        self._last_pos[pid] = (x, y)
        return True

    def touch_up(self, pid, x, y):
        self.calls.append(("up", pid, x, y))
        self._pointers.discard(pid)
        return True

    def cancel_all(self):
        for pid in sorted(self._pointers):
            x, y = self._last_pos.get(pid, (0.0, 0.0))
            self.touch_up(pid, x, y)


def make_injector(tmp_path, screen=(2340, 1080)):
    blob = tmp_path / "scrcpy-server"
    blob.write_bytes(b"fake-jar")
    fake = FakeClient(blob, screen)
    inj = ScrcpyControlInjector(
        "/fake/adb",
        "127.0.0.1:5555",
        blob,
        client=fake,
        screen_width=screen[0],
        screen_height=screen[1],
    )
    return inj, fake


def test_injector_lazy_starts_daemon_on_first_touch(tmp_path):
    inj, fake = make_injector(tmp_path)
    assert fake.started == 0
    assert inj.touch_down(280, 702, slot=1) is True
    assert fake.started == 1
    assert fake.calls == [("down", 1, 280, 702)]  # slot -> pointer_id
    inj.touch_move(300, 700, slot=1)
    assert fake.started == 1  # started once
    assert ("move", 1, 300, 700) in fake.calls


def test_injector_available_requires_blob(tmp_path):
    inj, _ = make_injector(tmp_path)
    assert inj.available is True
    missing = tmp_path / "nope" / "scrcpy-server"
    inj2 = ScrcpyControlInjector(
        "/fake/adb",
        "s",
        missing,
        client=FakeClient(missing),
    )
    assert inj2.available is False
    assert inj2.ensure_started() is False
    assert inj2.touch_down(1, 1, slot=0) is False


def test_injector_precise_drag_emits_down_moves_up(tmp_path):
    inj, fake = make_injector(tmp_path)
    assert inj.precise_drag(0, 0, 100, 0, steps=4, step_delay_ms=0, slot=1) is True
    kinds = [c[0] for c in fake.calls]
    assert kinds[0] == "down" and kinds[-1] == "up"
    assert kinds.count("move") == 4
    assert fake.calls[0][1] == 1  # pointer 1 throughout
    assert all(c[1] == 1 for c in fake.calls)
    last_move = [c for c in fake.calls if c[0] == "move"][-1]
    assert (last_move[2], last_move[3]) == (100, 0)


def test_injector_touch_hold(tmp_path):
    inj, fake = make_injector(tmp_path)
    assert inj.touch_hold(280, 702, duration_ms=10, slot=0) is True
    assert [c[0] for c in fake.calls] == ["down", "up"]


def test_injector_held_touch_releases_on_exception(tmp_path):
    inj, fake = make_injector(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        with inj.held_touch(280, 702, slot=0):
            raise RuntimeError("boom")
    assert [c[0] for c in fake.calls] == ["down", "up"]
    assert inj.active_slots == set()


def test_injector_active_slots(tmp_path):
    inj, fake = make_injector(tmp_path)
    inj.touch_down(1, 1, slot=0)
    inj.touch_down(2, 2, slot=1)
    assert inj.active_slots == {0, 1}
    inj.touch_up(slot=0)
    assert inj.active_slots == {1}


def test_injector_negative_slot_rejected(tmp_path):
    inj, fake = make_injector(tmp_path)
    assert inj.touch_down(1, 1, slot=-1) is False
    assert fake.calls == []


# --- AdbDevice backend selection ---


def _adb_with(script, **kwargs):
    adb = AdbDevice(
        host="127.0.0.1",
        port=5555,
        system_config=MagicMock(),
        signer=MagicMock(),
        **kwargs,
    )
    calls = []

    def run(cmd, decode=True):
        calls.append(cmd)
        for prefix, resp in script.items():
            if cmd.startswith(prefix):
                return resp() if callable(resp) else resp
        return ""

    adb.run_command = run  # type: ignore[method-assign]
    return adb, calls


GETEVENT_TOUCH = """\
add device 2: /dev/input/event5
  name:     "sec_touchscreen"
  events:
    EV_ABS (0x03):
      ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_X     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
      ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
"""


class FakeScrcpyInjector:
    """Records selection; daemon starts on demand."""

    def __init__(self, start_ok=True):
        self.start_ok = start_ok
        self.ensure_started_calls = 0
        self._running = False
        self.touches = []

    @property
    def daemon_running(self):
        return self._running

    def ensure_started(self, timeout=20.0):
        self.ensure_started_calls += 1
        self._running = bool(self.start_ok)
        return self._running

    def _touch(self, kind, *a):
        self.touches.append((kind,) + a)
        return True

    def touch_down(self, x, y, slot=0):
        return self._touch("down", x, y, slot)

    def touch_move(self, x, y, slot=0):
        return self._touch("move", x, y, slot)

    def touch_up(self, slot=0):
        return self._touch("up", slot)

    def touch_cancel(self, slot=0):
        return self._touch("cancel", slot)

    def touch_hold(self, x, y, duration_ms, slot=0):
        return self._touch("hold", x, y, duration_ms, slot)

    def precise_drag(self, x1, y1, x2, y2, steps=24, step_delay_ms=16, slot=0):
        return self._touch("drag", x1, y1, x2, y2, slot)


def _adb_with_fake_scrcpy(script, fake, **kwargs):
    adb, calls = _adb_with(script, **kwargs)
    adb._scrcpy_control = lambda: fake  # type: ignore[method-assign]
    return adb, calls


def test_slot1_lazily_starts_daemon():
    fake = FakeScrcpyInjector(start_ok=True)
    adb, _ = _adb_with_fake_scrcpy({"getevent -p": GETEVENT_TOUCH}, fake)
    assert fake.ensure_started_calls == 0
    assert adb.touch_down(280, 702, slot=1) is True
    assert fake.ensure_started_calls == 1
    assert ("down", 280, 702, 1) in fake.touches


def test_daemon_once_up_serves_single_pointer_too():
    fake = FakeScrcpyInjector(start_ok=True)
    adb, calls = _adb_with_fake_scrcpy(
        {
            "input motionevent 2>&1": "Error: Invalid arguments for command: motionevent",
            "input motionevent": "",
        },
        fake,
    )
    assert adb.touch_down(280, 702, slot=1) is True  # daemon starts here
    assert adb.touch_down(100, 100, slot=0) is True  # daemon serves slot 0
    assert ("down", 100, 100, 0) in fake.touches
    # motionevent never probed: daemon won before the cheap chain ran
    assert not any(c.startswith("input motionevent") for c in calls)


def test_touch_available_does_not_start_daemon():
    fake = FakeScrcpyInjector(start_ok=True)
    adb, _ = _adb_with_fake_scrcpy(
        {
            "input motionevent 2>&1": "Error: Invalid arguments for command: motionevent",
            "input motionevent": "",
        },
        fake,
    )
    assert adb.touch_available is True
    assert fake.ensure_started_calls == 0


def test_daemon_failure_falls_back_to_sendevent_for_multitouch():
    fake = FakeScrcpyInjector(start_ok=False)
    adb, calls = _adb_with_fake_scrcpy(
        {"getevent -p": GETEVENT_TOUCH, "sendevent": ""}, fake
    )
    assert adb.touch_down(280, 702, slot=1) is True
    assert any(c.startswith("sendevent") for c in calls)


def test_daemon_and_sendevent_failure_is_honest():
    fake = FakeScrcpyInjector(start_ok=False)
    adb, _ = _adb_with_fake_scrcpy({}, fake)  # no motionevent, no touchscreen
    assert adb.touch_down(280, 702, slot=1) is False
    assert adb.touch_available is False


def test_explicit_enable_starts_daemon_for_slot0():
    fake = FakeScrcpyInjector(start_ok=True)
    adb, calls = _adb_with_fake_scrcpy(
        {
            "input motionevent 2>&1": "Error: Invalid arguments for command: motionevent",
            "input motionevent": "",
        },
        fake,
        scrcpy_control=True,
    )
    assert adb.touch_down(100, 100, slot=0) is True
    assert fake.ensure_started_calls == 1
    assert ("down", 100, 100, 0) in fake.touches
    assert not any(c.startswith("input motionevent") for c in calls)


def test_no_blob_means_no_scrcpy_attempts():
    adb, calls = _adb_with(
        {
            "input motionevent 2>&1": "Error: Invalid arguments for command: motionevent",
            "input motionevent": "",
        }
    )
    # MagicMock system_config -> junk blob path -> _scrcpy_control() is None
    assert adb._scrcpy_control() is None
    assert adb.touch_down(280, 702, slot=0) is True
    assert "input motionevent DOWN 280 702 2>&1" in calls


def test_start_multitouch_public_api():
    fake = FakeScrcpyInjector(start_ok=True)
    adb, _ = _adb_with_fake_scrcpy({}, fake)
    assert adb.start_multitouch() is True
    assert fake.ensure_started_calls == 1
    # daemon now up: slot 0 routes through it
    assert adb.touch_down(5, 5) is True
    assert ("down", 5, 5, 0) in fake.touches


def test_start_multitouch_false_without_blob():
    adb, _ = _adb_with({})
    assert adb._scrcpy_control() is None
    assert adb.start_multitouch() is False


def test_real_blob_lookup_with_tmp_file(tmp_path, monkeypatch):
    blob = tmp_path / "scrcpy-server"
    blob.write_bytes(b"x")
    import pymordialdroid.devices.adb_device as adb_mod

    monkeypatch.setattr(
        adb_mod, "_find_scrcpy_server_blob", lambda adb_bin, scrcpy_bin=None: blob
    )
    adb = AdbDevice(
        host="127.0.0.1",
        port=5555,
        system_config=MagicMock(),
        signer=MagicMock(),
        touch_input_width=2340,
        touch_input_height=1080,
    )
    adb.run_command = lambda cmd, decode=True: ""  # type: ignore[method-assign]
    inj = adb._scrcpy_control()
    assert inj is not None
    assert inj._client.server_blob == blob
    assert inj._client.screen_size == (2340, 1080)
