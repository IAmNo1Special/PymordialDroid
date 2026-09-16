"""Non-root multi-touch injection via scrcpy-server's control socket.

Why this exists
---------------
``input motionevent`` (``motionevent_injector.py``) works unrooted but is
single-pointer only: stock ``input`` has no pointer-id argument, so true
two-thumb play (joystick + camera, the way real players hold a phone) is
impossible through it. The sendevent backend (``touch_injector.py``) has
real multi-touch slots but SELinux denies shell writes to
``/dev/input/event*`` on non-rooted retail hardware.

scrcpy-server is the third path. Its ``INJECT_TOUCH_EVENT`` control
message carries an explicit 64-bit pointer id, and the server-side
``PointersState`` synthesizes the proper ``ACTION_POINTER_DOWN/UP``
masking — full multi-touch, no root, no SELinux involvement (the server
runs as shell and calls ``InputManager.injectInputEvent``, the same
privileged path ``input`` uses). Latency is <1ms per packet over the
localhost TCP tunnel, so 60Hz+ touch streams are realistic.

Field verification (2026-09-16, Malcom's on-machine agent, Galaxy S24
Ultra / Android 16 / scrcpy 4.1, Revomon Novus in foreground)
------------------------------------------------------------
A dual-thumb scenario was injected live over the control socket and
passed 100%: pointer 0 DOWN on the joystick (280,702), drag to (280,550)
and hold; pointer 1 DOWN on the camera area (1400,500) while pointer 0
was still down; 25 interpolated MOVEs rotating the camera 200px over
~1.5s; independent UP for each pointer. Zero permission issues, zero
SELinux blocks, zero crashes.

Protocol (scrcpy 4.1, control-only mode)
----------------------------------------
1. Push the server blob once (``cleanup=false`` keeps the jar on-device)::
      adb push <scrcpy-server> /data/local/tmp/scrcpy-server.jar
2. Start a headless control server (no video, no audio)::
      adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process /
        com.genymobile.scrcpy.Server 4.1 scid=<31-bit hex>
        log_level=info video=false audio=false control=true
        send_dummy_byte=true send_device_meta=false raw_stream=false
        tunnel_forward=true cleanup=false
   ``scid`` MUST be < 0x80000000: the server parses it with Java's
   ``Integer.parseInt(scid, 16)`` (signed 32-bit) and throws
   ``NumberFormatException`` otherwise.
3. ``adb forward tcp:<port> localabstract:scrcpy_<scid8>``. The forward
   accepts connections *before* the server is ready, so with
   ``send_dummy_byte=true`` the client connects and blocks on ``recv(1)``
   for the ``0x00`` dummy byte — that byte is the real "server is ready"
   handshake.
4. Touch packets — ``INJECT_TOUCH_EVENT``, 32 bytes big-endian::
      struct.pack('>BBQiiHHHii', 2, action, pointer_id, x, y,
                  width, height, 0xffff, 0, 0)
   ``action``: 0=DOWN, 1=UP, 2=MOVE. With ``video=false`` the server
   injects raw (x, y) display pixels — the same coordinate space as
   ``input tap`` (e.g. 2340x1080 landscape for Novus on the S24 Ultra).
   ``width``/``height`` still ride in the packet; pressure is 0xFFFF
   (1.0f); the trailing ints are action_button/buttons (0 for touch).
5. Teardown: close the socket, terminate the server process,
   ``adb forward --remove tcp:<port>``.

What could not be verified without hardware
-------------------------------------------
- 60Hz packet timing under sustained load (only short bursts were driven).
- Server behavior when the target app is not in the foreground.
- Jar-push idempotency across reconnects (we always re-push; cheap).
- Sharing one scrcpy-server session with the video live stream
  (``ScrcpyLiveStream`` runs with ``control=false``). The follow-up is to
  run the live stream with ``control=true`` and hand its socket to this
  client; the first version here runs a dedicated control daemon with its
  own scid instead — documented, not half-built.
- There is no CANCEL action in the verified protocol; stuck-pointer
  recovery is UP-per-pointer (``cancel_all``), stated as such.
"""

from __future__ import annotations

import logging
import random
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger("pymordialdroid")

# --- protocol constants (scrcpy 4.1, field-verified 2026-09-16) ---

SERVER_JAR_DEVICE_PATH = "/data/local/tmp/scrcpy-server.jar"
SERVER_CLASS = "com.genymobile.scrcpy.Server"
DEFAULT_SERVER_VERSION = "4.1"

MSG_TYPE_INJECT_TOUCH_EVENT = 2
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2

_TOUCH_PACKET_FMT = ">BBQiiHHHii"  # 1+1+8+4+4+2+2+2+4+4 = 32 bytes
_TOUCH_PACKET_SIZE = struct.calcsize(_TOUCH_PACKET_FMT)
_PRESSURE_FULL = 0xFFFF  # 1.0f as u16 fixed-point

_MAX_SCID = 0x80000000  # Java Integer.parseInt is signed 32-bit

Runner = Callable[..., Any]  # subprocess.run-like: argv -> has .returncode
PopenFactory = Callable[..., Any]  # subprocess.Popen-like
SocketFactory = Callable[[], Any]  # () -> connected socket-like


def validate_scid(scid: int) -> int:
    """Validates a server id for ``Integer.parseInt(scid, 16)`` (signed 32-bit)."""
    if not isinstance(scid, int) or not 0 <= scid < _MAX_SCID:
        raise ValueError(f"scid must be a 31-bit int (< 0x80000000), got {scid!r}")
    return scid


def build_touch_packet(
    action: int,
    pointer_id: int,
    x: float,
    y: float,
    width: int,
    height: int,
) -> bytes:
    """Builds one 32-byte ``INJECT_TOUCH_EVENT`` control message.

    ``(x, y)`` are raw display pixels (same space as ``input tap``);
    ``width``/``height`` are the current display size in the same space.
    """
    if action not in (ACTION_DOWN, ACTION_UP, ACTION_MOVE):
        raise ValueError(f"unknown touch action: {action!r}")
    if pointer_id < 0:
        raise ValueError(f"pointer_id must be >= 0, got {pointer_id!r}")
    return struct.pack(
        _TOUCH_PACKET_FMT,
        MSG_TYPE_INJECT_TOUCH_EVENT,
        action,
        int(pointer_id),
        int(x),
        int(y),
        int(width),
        int(height),
        _PRESSURE_FULL,
        0,  # action_button (touch)
        0,  # buttons (touch)
    )


def _find_server_blob(
    adb_bin: str | Path, scrcpy_bin: str | Path | None = None
) -> Path | None:
    """Locates the bundled ``scrcpy-server`` blob on the host.

    Mirrors the live-stream convention (blob next to the scrcpy binary),
    falling back to next to the adb binary (bundled layout:
    ``bin/scrcpy/scrcpy-server`` beside ``adb.exe``). Kept private: the
    public ``pymordialdroid.live_stream.find_server_blob`` has a different
    signature.
    """
    candidates: list[Path] = []
    if scrcpy_bin is not None:
        candidates.append(Path(scrcpy_bin).parent / "scrcpy-server")
    candidates.append(Path(adb_bin).parent / "scrcpy-server")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class ScrcpyControlClient:
    """Lifecycle manager for a control-only scrcpy-server touch daemon.

    Owns: jar push, server process, ``adb forward`` tunnel, and the
    persistent control socket. Usable as a context manager or via explicit
    :meth:`start` / :meth:`stop`. All socket I/O is serialized with a lock
    so joystick and camera threads can share one client.

    ``runner`` / ``popen_factory`` / ``socket_factory`` are injectable for
    unit tests; they default to the real subprocess/socket calls.
    """

    def __init__(
        self,
        adb_bin: str | Path,
        serial: str,
        server_blob: str | Path,
        *,
        screen_width: int | None = None,
        screen_height: int | None = None,
        server_version: str = DEFAULT_SERVER_VERSION,
        log_level: str = "info",
        runner: Runner | None = None,
        popen_factory: PopenFactory | None = None,
        socket_factory: SocketFactory | None = None,
    ) -> None:
        self.adb_bin = str(adb_bin)
        self.serial = serial
        self.server_blob = Path(server_blob)
        self.server_version = server_version
        self.log_level = log_level

        self._runner: Runner = runner or subprocess.run
        self._popen: PopenFactory = popen_factory or subprocess.Popen
        self._socket_factory = socket_factory

        self._screen_width = screen_width
        self._screen_height = screen_height

        self._scid = validate_scid(random.getrandbits(31))
        self._local_port = 0
        self._sock: Any = None
        self._server_proc: Any = None
        self._lock = threading.Lock()
        self._active_pointers: set[int] = set()
        self._last_pos: dict[int, tuple[float, float]] = {}

    # --- properties ---

    @property
    def is_running(self) -> bool:
        """True when the daemon socket is open and the server is alive."""
        with self._lock:
            if self._sock is None:
                return False
            proc = self._server_proc
            return proc is None or proc.poll() is None

    @property
    def active_pointers(self) -> set[int]:
        """Pointer ids currently held down (per this client's bookkeeping)."""
        with self._lock:
            return set(self._active_pointers)

    @property
    def screen_size(self) -> tuple[int, int] | None:
        with self._lock:
            if self._screen_width is None or self._screen_height is None:
                return None
            return self._screen_width, self._screen_height

    def set_screen_size(self, width: int, height: int) -> None:
        """Sets the display size carried in touch packets (uint16 each)."""
        if not 0 < width <= 0xFFFF or not 0 < height <= 0xFFFF:
            raise ValueError(f"screen size out of uint16 range: {width}x{height}")
        with self._lock:
            self._screen_width = int(width)
            self._screen_height = int(height)

    # --- lifecycle ---

    def __enter__(self) -> ScrcpyControlClient:
        if not self.start():
            raise RuntimeError("scrcpy control daemon failed to start")
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def start(self, timeout: float = 20.0) -> bool:
        """Starts the control daemon: push, forward, server, dummy-byte handshake.

        Idempotent — returns True immediately if already running. Never
        raises: every failure is logged and torn down, returning False.
        """
        with self._lock:
            if self._sock is not None:
                return True
        try:
            if not self._push_server():
                return False
            if not self._open_tunnel():
                return False
            if not self._launch_server():
                self._remove_tunnel()
                return False
            if not self._connect(timeout):
                self._cleanup_process()
                self._remove_tunnel()
                return False
            log.info(
                f"[scrcpy-control] daemon up on {self.serial} "
                f"(scid={self._scid:08x}, port={self._local_port})"
            )
            return True
        except Exception as e:
            log.error(f"[scrcpy-control] start failed: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        """Stops the daemon, releasing stuck pointers first (best-effort).

        Idempotent; never raises.
        """
        self.cancel_all()  # best-effort UP for tracked pointers
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        self._cleanup_process()
        self._remove_tunnel()

    # --- touch injection ---

    def touch_down(self, pointer_id: int, x: float, y: float) -> bool:
        """Sends ACTION_DOWN for ``pointer_id`` at display pixels (x, y)."""
        if self._send(ACTION_DOWN, pointer_id, x, y):
            with self._lock:
                self._active_pointers.add(pointer_id)
            return True
        return False

    def touch_move(self, pointer_id: int, x: float, y: float) -> bool:
        """Sends ACTION_MOVE for an already-down ``pointer_id``."""
        with self._lock:
            tracked = pointer_id in self._active_pointers
        if not tracked:
            log.warning(
                f"[scrcpy-control] touch_move for inactive pointer {pointer_id}; "
                "call touch_down first"
            )
            return False
        return self._send(ACTION_MOVE, pointer_id, x, y)

    def touch_up(self, pointer_id: int, x: float, y: float) -> bool:
        """Sends ACTION_UP for ``pointer_id`` at (x, y); untracks the pointer."""
        ok = self._send(ACTION_UP, pointer_id, x, y)
        with self._lock:
            self._active_pointers.discard(pointer_id)
        return ok

    def cancel_all(self) -> None:
        """Best-effort UP for every tracked pointer (stuck-pointer recovery).

        The verified protocol has no CANCEL action, so recovery is
        UP-per-pointer at each pointer's last known position. Never raises.
        """
        with self._lock:
            pointers = sorted(self._active_pointers)
            positions = dict(self._last_pos)
        for pid in pointers:
            x, y = positions.get(pid, (0.0, 0.0))
            try:
                self.touch_up(pid, x, y)
            except Exception as e:
                log.debug(f"[scrcpy-control] cancel_all UP failed: {e}")

    # --- internals ---

    def _adb(self, *args: str, **kwargs: Any) -> Any:
        return self._runner([self.adb_bin, "-s", self.serial, *args], **kwargs)

    def _push_server(self) -> bool:
        """Pushes the server jar (always re-push: cheap, guarantees version match)."""
        try:
            proc = self._adb(
                "push",
                str(self.server_blob),
                SERVER_JAR_DEVICE_PATH,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as e:
            log.error(f"[scrcpy-control] jar push failed: {e}")
            return False
        if proc.returncode != 0:
            log.error(
                "[scrcpy-control] jar push failed: "
                f"{getattr(proc, 'stderr', '') or getattr(proc, 'stdout', '')}"
            )
            return False
        return True

    def _pick_local_port(self) -> int:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])
        finally:
            probe.close()

    def _socket_name(self) -> str:
        return f"scrcpy_{self._scid:08x}"

    def _open_tunnel(self) -> bool:
        self._local_port = self._pick_local_port()
        try:
            proc = self._adb(
                "forward",
                f"tcp:{self._local_port}",
                f"localabstract:{self._socket_name()}",
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            log.error(f"[scrcpy-control] adb forward failed: {e}")
            return False
        if proc.returncode != 0:
            log.error("[scrcpy-control] adb forward failed")
            return False
        return True

    def _remove_tunnel(self) -> None:
        if not self._local_port:
            return
        try:
            self._adb(
                "forward",
                "--remove",
                f"tcp:{self._local_port}",
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            log.debug(f"[scrcpy-control] forward remove failed: {e}")
        finally:
            self._local_port = 0

    def _server_argv(self) -> list[str]:
        return [
            self.adb_bin,
            "-s",
            self.serial,
            "shell",
            f"CLASSPATH={SERVER_JAR_DEVICE_PATH}",
            "app_process",
            "/",
            SERVER_CLASS,
            self.server_version,
            f"scid={self._scid:08x}",
            f"log_level={self.log_level}",
            "video=false",
            "audio=false",
            "control=true",
            "send_dummy_byte=true",
            "send_device_meta=false",
            "raw_stream=false",
            "tunnel_forward=true",
            "cleanup=false",
        ]

    def _launch_server(self) -> bool:
        try:
            self._server_proc = self._popen(
                self._server_argv(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            log.error(f"[scrcpy-control] server launch failed: {e}")
            self._server_proc = None
            return False

    def _cleanup_process(self) -> None:
        proc, self._server_proc = self._server_proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _default_socket(self) -> Any:
        sock = socket.create_connection(("127.0.0.1", self._local_port), timeout=2)
        sock.settimeout(2)
        return sock

    def _connect(self, timeout: float) -> bool:
        """Connects and waits for the 0x00 dummy byte (the ready handshake).

        ``adb forward`` accepts connections before the server is listening,
        so refused/reset attempts are retried until the deadline.
        """
        factory = self._socket_factory
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                sock = factory() if factory else self._default_socket()
            except Exception as e:
                last_err = e
                time.sleep(0.2)
                continue
            try:
                dummy = sock.recv(1)
            except Exception as e:
                last_err = e
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.2)
                continue
            if dummy == b"\x00":
                with self._lock:
                    self._sock = sock
                return True
            last_err = RuntimeError(f"unexpected handshake byte: {dummy!r}")
            try:
                sock.close()
            except Exception:
                pass
            time.sleep(0.2)
        log.error(f"[scrcpy-control] no dummy byte within {timeout}s: {last_err}")
        return False

    def _send(self, action: int, pointer_id: int, x: float, y: float) -> bool:
        with self._lock:
            sock = self._sock
            w, h = self._screen_width, self._screen_height
        if sock is None:
            log.debug("[scrcpy-control] send with no connection")
            return False
        if w is None or h is None:
            log.error("[scrcpy-control] screen size unknown; call set_screen_size first")
            return False
        try:
            packet = build_touch_packet(action, pointer_id, x, y, w, h)
        except ValueError as e:
            log.error(f"[scrcpy-control] bad touch packet: {e}")
            return False
        with self._lock:
            try:
                sock.sendall(packet)
            except Exception as e:
                log.debug(f"[scrcpy-control] send failed: {e}")
                return False
            self._last_pos[pointer_id] = (float(x), float(y))
        return True

    _last_pos: dict[int, tuple[float, float]]  # initialized in __init__


class ScrcpyControlInjector:
    """Multi-touch injector with the shared backend surface, over scrcpy-control.

    Same public API as :class:`MotionEventInjector` / :class:`TouchInjector`
    (``touch_down`` / ``touch_move`` / ``touch_up`` / ``touch_cancel`` /
    ``touch_hold`` / ``held_touch`` / ``precise_drag`` / ``available`` /
    ``active_slots``) with ``slot`` mapped 1:1 to scrcpy ``pointer_id`` —
    real multi-touch on non-rooted hardware.

    The daemon starts lazily on first use (:meth:`ensure_started`); the
    ``available`` probe is cheap (server blob must exist on the host).
    Coordinates are display/input-space pixels, identical to ``input tap``.
    """

    def __init__(
        self,
        adb_bin: str | Path,
        serial: str,
        server_blob: str | Path,
        *,
        screen_width: int | None = None,
        screen_height: int | None = None,
        server_version: str = DEFAULT_SERVER_VERSION,
        client: ScrcpyControlClient | None = None,
    ) -> None:
        self._client = client or ScrcpyControlClient(
            adb_bin,
            serial,
            server_blob,
            screen_width=screen_width,
            screen_height=screen_height,
            server_version=server_version,
        )
        self._warned_slot = False

    # --- availability ---

    @property
    def available(self) -> bool:
        """True when the daemon *could* start (host has the server blob)."""
        return self._client.server_blob.is_file()

    @property
    def daemon_running(self) -> bool:
        """True when the control daemon socket is up."""
        return self._client.is_running

    def ensure_started(self, timeout: float = 20.0) -> bool:
        """Starts the daemon if needed (lazy); False when it cannot start."""
        if self._client.is_running:
            return True
        if not self.available:
            log.warning(
                "[scrcpy-control] server blob missing: "
                f"{self._client.server_blob} — multi-touch unavailable"
            )
            return False
        return self._client.start(timeout=timeout)

    def stop(self) -> None:
        """Stops the daemon (releases tracked pointers first)."""
        self._client.stop()

    def set_screen_size(self, width: int, height: int) -> None:
        self._client.set_screen_size(width, height)

    # --- public API (slot == pointer_id) ---

    def _ready(self, slot: int) -> bool:
        if slot < 0:
            if not self._warned_slot:
                self._warned_slot = True
                log.warning(f"[scrcpy-control] invalid slot {slot}")
            return False
        return self.ensure_started()

    def touch_down(self, x: float, y: float, slot: int = 0) -> bool:
        """Presses pointer ``slot`` down at display pixels (x, y)."""
        if not self._ready(slot):
            return False
        return self._client.touch_down(slot, x, y)

    def touch_move(self, x: float, y: float, slot: int = 0) -> bool:
        """Moves the already-down pointer ``slot`` to (x, y)."""
        if not self._ready(slot):
            return False
        return self._client.touch_move(slot, x, y)

    def touch_up(self, slot: int = 0) -> bool:
        """Lifts pointer ``slot`` (at its last known position)."""
        if not self._ready(slot):
            return False
        x, y = self._client._last_pos.get(slot, (0.0, 0.0))
        return self._client.touch_up(slot, x, y)

    def touch_cancel(self, slot: int = 0) -> bool:
        """Releases pointer ``slot`` (stuck-gesture recovery; UP-based)."""
        if not self._ready(slot):
            return False
        x, y = self._client._last_pos.get(slot, (0.0, 0.0))
        ok = self._client.touch_up(slot, x, y)
        return ok

    def cancel_all(self) -> None:
        """Releases every tracked pointer (best-effort; never raises)."""
        if self._client.is_running:
            self._client.cancel_all()

    def touch_hold(self, x: float, y: float, duration_ms: int, slot: int = 0) -> bool:
        """Holds pointer ``slot`` down for ``duration_ms``, then releases.

        The persistent socket keeps the contact down for the whole sleep —
        no UP is emitted in between (unlike ``input swipe`` long-press).
        """
        if not self._ready(slot):
            return False
        if not self._client.touch_down(slot, x, y):
            return False
        time.sleep(max(duration_ms, 0) / 1000.0)
        return self._client.touch_up(slot, x, y)

    @contextmanager
    def held_touch(
        self, x: float, y: float, slot: int = 0
    ) -> Iterator[ScrcpyControlInjector]:
        """Context manager holding pointer ``slot`` down for the block.

        The two-thumb primitive: hold the joystick on slot 0 while driving
        the camera on slot 1. Always releases on exit, even on exception.
        """
        if not self.touch_down(x, y, slot=slot):
            raise RuntimeError("touch injection unavailable; touch_down failed")
        try:
            yield self
        finally:
            self.touch_up(slot=slot)

    def precise_drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        steps: int = 24,
        step_delay_ms: int = 16,
        slot: int = 0,
    ) -> bool:
        """Drags (x1, y1) -> (x2, y2) emitting ``steps`` interpolated MOVEs.

        Packets go over the persistent socket (<1ms each), so the move
        stream is dense and precisely timed — the fine-control primitive.
        """
        if not self._ready(slot):
            return False
        if steps < 1:
            raise ValueError("steps must be >= 1")
        if not self._client.touch_down(slot, x1, y1):
            return False
        delay = max(step_delay_ms, 0) / 1000.0
        try:
            for i in range(1, steps + 1):
                fx = x1 + (x2 - x1) * i / steps
                fy = y1 + (y2 - y1) * i / steps
                time.sleep(delay)
                if not self._client.touch_move(slot, fx, fy):
                    return False
            return True
        finally:
            self._client.touch_up(slot, x2, y2)

    @property
    def active_slots(self) -> set[int]:
        """Slots currently held down (per this injector's bookkeeping)."""
        return self._client.active_pointers


__all__ = [
    "ACTION_DOWN",
    "ACTION_MOVE",
    "ACTION_UP",
    "MSG_TYPE_INJECT_TOUCH_EVENT",
    "ScrcpyControlClient",
    "ScrcpyControlInjector",
    "build_touch_packet",
    "validate_scid",
]
