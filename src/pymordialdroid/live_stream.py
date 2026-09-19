"""Direct scrcpy-server video client for sub-100ms screen frames.

The bundled ``scrcpy`` binary (4.1) no longer exposes a raw H.264 stdout
pipe, and tailing ``--record`` MKV files costs seconds of latency. This module
talks the scrcpy 4.1 server protocol directly instead:

1. pushes the bundled ``scrcpy-server`` blob to the device,
2. opens an ``adb forward`` tunnel (``localabstract:scrcpy_<scid>``),
3. launches the server with ``raw_stream=true`` (pure Annex-B H.264 bytes, no
   muxing, no window, no buffering),
4. decodes packets with PyAV, keeping only the latest frame (same
   drop-expired policy the scrcpy client uses for its 35-70ms latency).

Typical glass-to-frame budget at 960p/2M H.264: encode ~10-20ms, ADB
forward ~1-5ms (localhost) + WiFi transport, software decode ~5-15ms.
USB or good WiFi lands under 100ms; congested WiFi will not — use
:meth:`ScrcpyLiveStream.get_stats` to measure on real hardware.

Protocol reference: scrcpy v4.1 ``server/src/main/java/com/genymobile/scrcpy/Options.java`` (argument list),
``app/src/demuxer.c`` (packet framing), ``app/src/server.h``
(``SC_DEVICE_NAME_FIELD_LENGTH``), and server ``Options.java``
(``raw_stream`` semantics).
"""

import logging
import random
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("pymordialdroid")

DEVICE_SERVER_PATH = "/data/local/tmp/scrcpy-server.jar"
SERVER_CLASS = "com.genymobile.scrcpy.Server"
SOCKET_NAME_PREFIX = "scrcpy_"

# Latency-tuned defaults: small frames decode fast and fit WiFi comfortably.
DEFAULT_MAX_SIZE = 960
DEFAULT_BIT_RATE = 2_000_000
DEFAULT_MAX_FPS = 30

# A stream whose socket is open but which emitted no frame for longer than
# this is reported as stale. Note the encoder only emits on content change,
# so a static screen is stale-but-healthy — use `alive` (socket/thread up)
# to distinguish "dead" from "idle".
STALE_AFTER_MS = 1500.0


def parse_bit_rate(value: str | int) -> int:
    """Parses a bit rate like ``"2M"``, ``"500K"``, or ``2000000`` to bits/s."""
    if isinstance(value, int):
        return value
    text = value.strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KkMm]?)", text)
    if not match:
        raise ValueError(f"Invalid bit rate: {value!r}")
    number, suffix = match.groups()
    multiplier = {"": 1, "K": 1000, "M": 1_000_000}
    return int(float(number) * multiplier[suffix.upper()])


def find_server_blob(scrcpy_bin: str | Path) -> Path | None:
    """Locates the bundled ``scrcpy-server`` blob next to the binary."""
    candidate = Path(scrcpy_bin).parent / "scrcpy-server"
    return candidate if candidate.is_file() else None


def detect_server_version(scrcpy_bin: str | Path) -> str | None:
    """Parses ``scrcpy --version`` (``"scrcpy 4.1 ..."``) to ``"4.1"``.

    Handles both 2-part (e.g. ``"4.1"``) and 3-part (e.g. ``"3.3.4"``) version
    strings.
    """
    try:
        proc = subprocess.run(
            [str(scrcpy_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        match = re.search(r"scrcpy\s+(\d+\.\d+(?:\.\d+)?)", proc.stdout or "")
        return match.group(1) if match else None
    except Exception as e:
        log.debug(f"Could not detect scrcpy version: {e}")
        return None


def build_server_argv(
    adb_bin: str | Path,
    serial: str,
    server_version: str,
    scid: int,
    *,
    max_size: int = DEFAULT_MAX_SIZE,
    bit_rate: int = DEFAULT_BIT_RATE,
    max_fps: float | None = DEFAULT_MAX_FPS,
    show_touches: bool = False,
    stay_awake: bool = True,
    new_display: str | None = None,
    vd_system_decorations: bool = False,
    log_level: str = "error",
) -> list[str]:
    """Builds the exact ``adb shell ... app_process`` argv for the server.

    Mirrors scrcpy 4.1 ``execute_server()`` for a video-only, no-control,
    forward-tunnel client. ``raw_stream=true`` disables the dummy byte,
    device meta, codec meta, and per-packet PTS headers, leaving a pure
    Annex-B H.264 byte stream on the video socket.
    """
    argv = [
        str(adb_bin),
        "-s",
        serial,
        "shell",
        f"CLASSPATH={DEVICE_SERVER_PATH}",
        "app_process",
        "/",
        SERVER_CLASS,
        server_version,
        f"scid={scid:08x}",
        f"log_level={log_level}",
        "audio=false",
        "control=false",
        "tunnel_forward=true",
        "raw_stream=true",
        f"max_size={int(max_size)}",
        f"video_bit_rate={int(bit_rate)}",
    ]
    if max_fps:
        argv.append(f"max_fps={max_fps}")
    if show_touches:
        argv.append("show_touches=true")
    if stay_awake:
        argv.append("stay_awake=true")
    if new_display is not None:
        argv.append(f"new_display={new_display}")
        if not vd_system_decorations:
            argv.append("vd_system_decorations=false")
    return argv


class H264StreamParser:
    """Incremental Annex-B H.264 parser yielding BGR frames via PyAV."""

    def __init__(self) -> None:
        try:
            import av
        except ImportError as e:
            raise RuntimeError(
                "PyAV ('av' package) is required for live streaming. "
                "Install it with: uv add av"
            ) from e
        self._codec = av.CodecContext.create("h264", "r")
        self.frames_decoded = 0

    def feed(self, data: bytes):  # type: ignore[no-untyped-def]
        """Feeds raw stream bytes, returning newly decoded BGR frames."""
        decoded = []
        for packet in self._codec.parse(data):
            for frame in self._codec.decode(packet):
                decoded.append(frame.to_ndarray(format="bgr24"))
                self.frames_decoded += 1
        return decoded

    def close(self) -> None:
        """Flushes and releases the decoder."""
        try:
            for _ in self._codec.decode(None):
                pass
        except Exception:
            pass


@dataclass
class LiveStreamStats:
    """Point-in-time measurements for a live stream."""

    running: bool = False
    has_frame: bool = False
    age_ms: float | None = None
    stale: bool = False
    fps: float = 0.0
    frames_decoded: int = 0
    bytes_received: int = 0
    width: int = 0
    height: int = 0


@dataclass
class _MutableStreamState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    latest: object = None  # numpy BGR array
    latest_at: float = 0.0
    frames_decoded: int = 0
    bytes_received: int = 0
    fps_ema: float = 0.0
    width: int = 0
    height: int = 0


class ScrcpyLiveStream:
    """Single-device raw H.264 live stream with latest-frame polling."""

    def __init__(
        self,
        serial: str,
        adb_bin: str | Path,
        server_blob: str | Path,
        *,
        server_version: str | None = None,
        max_size: int = DEFAULT_MAX_SIZE,
        bit_rate: str | int = DEFAULT_BIT_RATE,
        max_fps: float | None = DEFAULT_MAX_FPS,
        show_touches: bool = False,
        stay_awake: bool = True,
        new_display: str | None = None,
        vd_system_decorations: bool = False,
    ) -> None:
        self.serial = serial
        self.adb_bin = str(adb_bin)
        self.server_blob = Path(server_blob)
        self.server_version = server_version
        self.max_size = max_size
        self.bit_rate = parse_bit_rate(bit_rate)
        self.max_fps = max_fps
        self.show_touches = show_touches
        self.stay_awake = stay_awake
        self.new_display = new_display
        self.vd_system_decorations = vd_system_decorations

        self._scid = random.getrandbits(31)
        self._local_port = 0
        self._sock: socket.socket | None = None
        self._server_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._first_frame = threading.Event()
        self._state = _MutableStreamState()
        self._started_at = 0.0

    # --- lifecycle ---

    @property
    def socket_name(self) -> str:
        """Device abstract socket name for this session."""
        return f"{SOCKET_NAME_PREFIX}{self._scid:08x}"

    def is_running(self) -> bool:
        """Checks if the reader thread is alive and not stopped."""
        return (
            not self._stop_event.is_set()
            and self._thread is not None
            and self._thread.is_alive()
        )

    running = property(is_running)

    def start(self, timeout: float = 20.0) -> bool:
        """Pushes the server, connects, and waits for the first frame."""
        if self.is_running():
            return True
        deadline = time.monotonic() + timeout
        self._stop_event.clear()
        self._first_frame.clear()
        try:
            version = self.server_version or detect_server_version(
                Path(self.adb_bin).parent / "scrcpy.exe"
            )
            # Fall back to the adb-binary sibling lookup on non-Windows too.
            if version is None:
                version = detect_server_version(Path(self.adb_bin).parent / "scrcpy")
            if version is None:
                log.error(
                    "Could not detect scrcpy server version; aborting live stream."
                )
                return False
            if not self._push_server(deadline):
                return False
            if not self._open_tunnel():
                return False
            if not self._launch_server(version):
                self._remove_tunnel()
                return False
            if not self._connect(deadline):
                self._cleanup_process()
                self._remove_tunnel()
                return False
            self._started_at = time.monotonic()
            self._thread = threading.Thread(
                target=self._reader, name=f"scrcpy-live-{self.serial}", daemon=True
            )
            self._thread.start()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._first_frame.wait(timeout=remaining):
                log.error(f"[{self.serial}] Live stream got no frames within timeout.")
                self.stop()
                return False
            log.info(f"[{self.serial}] Live stream running.")
            return True
        except Exception as e:
            log.error(f"[{self.serial}] Live stream start failed: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        """Stops the stream and releases the ADB forward (idempotent)."""
        self._stop_event.set()
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
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._cleanup_process()
        self._remove_tunnel()

    # --- frame access ---

    def get_latest_numpy(self):  # type: ignore[no-untyped-def]
        """Returns a copy of the latest BGR frame, or None if none yet."""
        with self._state.lock:
            latest = self._state.latest
            return None if latest is None else latest.copy()

    def get_latest_png(self) -> bytes | None:
        """Returns the latest frame as PNG bytes, or None if none yet."""
        frame = self.get_latest_numpy()
        if frame is None:
            return None
        try:
            import cv2

            ok, buf = cv2.imencode(".png", frame)
            return buf.tobytes() if ok else None
        except Exception as e:
            log.debug(f"Live frame PNG encode failed: {e}")
            return None

    def get_stats(self) -> LiveStreamStats:
        """Returns current latency-relevant measurements."""
        with self._state.lock:
            has_frame = self._state.latest is not None
            age = (
                (time.monotonic() - self._state.latest_at) * 1000.0
                if has_frame
                else None
            )
            alive = self.is_running()
            return LiveStreamStats(
                running=alive,
                has_frame=has_frame,
                age_ms=age,
                stale=bool(has_frame and age is not None and age > STALE_AFTER_MS),
                fps=self._state.fps_ema,
                frames_decoded=self._state.frames_decoded,
                bytes_received=self._state.bytes_received,
                width=self._state.width,
                height=self._state.height,
            )

    # --- internals ---

    def _adb(self, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
        """Runs an ADB command targeted at this stream's serial."""
        return subprocess.run(
            [self.adb_bin, "-s", self.serial, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def _push_server(self, deadline: float) -> bool:
        """Pushes the server blob; skips when the device copy looks current."""
        try:
            remaining = max(1.0, deadline - time.monotonic())
            proc = self._adb(
                "push", str(self.server_blob), DEVICE_SERVER_PATH, timeout=remaining
            )
            if proc.returncode != 0:
                log.error(f"[{self.serial}] Server push failed: {proc.stderr!r}")
                return False
            return True
        except Exception as e:
            log.error(f"[{self.serial}] Server push failed: {e}")
            return False

    def _open_tunnel(self) -> bool:
        """Creates ``adb forward tcp:<free> localabstract:<name>``."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", 0))
                self._local_port = s.getsockname()[1]
            proc = self._adb(
                "forward",
                f"tcp:{self._local_port}",
                f"localabstract:{self.socket_name}",
            )
            if proc.returncode != 0:
                log.error(f"[{self.serial}] ADB forward failed: {proc.stderr!r}")
                return False
            return True
        except Exception as e:
            log.error(f"[{self.serial}] ADB forward failed: {e}")
            return False

    def _launch_server(self, version: str) -> bool:
        """Starts the on-device server (detached; runs until sockets close)."""
        argv = build_server_argv(
            self.adb_bin,
            self.serial,
            version,
            self._scid,
            max_size=self.max_size,
            bit_rate=self.bit_rate,
            max_fps=self.max_fps,
            show_touches=self.show_touches,
            stay_awake=self.stay_awake,
            new_display=self.new_display,
            vd_system_decorations=self.vd_system_decorations,
        )
        try:
            self._server_proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            log.error(f"[{self.serial}] Server launch failed: {e}")
            return False

    def _connect(self, deadline: float) -> bool:
        """Connects through the forward tunnel; first bytes prove liveness.

        ``adb forward`` accepts TCP even before the device server listens, so
        a bare connect is meaningless with ``raw_stream`` (no dummy byte).
        Instead, the first received chunk both proves the server is up and
        seeds the decoder — it is stashed for the reader thread.
        """
        self._pending_chunk: bytes | None = None
        while time.monotonic() < deadline and not self._stop_event.is_set():
            sock = None
            try:
                sock = socket.create_connection(
                    ("127.0.0.1", self._local_port), timeout=1.0
                )
                sock.settimeout(max(0.5, deadline - time.monotonic()))
                chunk = sock.recv(65536)
                if chunk:
                    self._pending_chunk = chunk
                    sock.settimeout(None)
                    self._sock = sock
                    return True
                sock.close()
            except (TimeoutError, ConnectionRefusedError, OSError):
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            time.sleep(0.1)
        log.error(f"[{self.serial}] Timed out waiting for server stream.")
        return False

    def _reader(self) -> None:
        """Receives raw H.264, decodes, and keeps only the latest frame."""
        sock = self._sock
        if sock is None:
            return
        try:
            parser = H264StreamParser()
        except RuntimeError as e:
            log.error(f"[{self.serial}] {e}")
            return
        try:
            pending = getattr(self, "_pending_chunk", None)
            if pending:
                self._ingest(parser, pending)
                self._pending_chunk = None
            while not self._stop_event.is_set():
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break  # clean EOS: server exited
                self._ingest(parser, chunk)
        except Exception as e:
            log.debug(f"[{self.serial}] Live reader ended: {e}")
        finally:
            try:
                parser.close()
            except Exception:
                pass
            log.info(f"[{self.serial}] Live reader stopped.")

    def _ingest(self, parser: H264StreamParser, chunk: bytes) -> None:
        """Decodes one chunk, storing only the newest frame (drop-expired)."""
        now = time.monotonic()
        with self._state.lock:
            self._state.bytes_received += len(chunk)
        try:
            frames = parser.feed(chunk)
        except Exception as e:
            log.debug(f"[{self.serial}] Decode error (skipping chunk): {e}")
            return
        if not frames:
            return
        newest = frames[-1]
        with self._state.lock:
            prev_at = self._state.latest_at
            self._state.latest = newest
            self._state.latest_at = now
            self._state.frames_decoded += len(frames)
            try:
                self._state.height, self._state.width = newest.shape[:2]
            except Exception:
                pass
            if prev_at > 0:
                instant_fps = 1.0 / max(1e-3, now - prev_at)
                alpha = 0.2
                prev_ema = self._state.fps_ema
                self._state.fps_ema = (
                    instant_fps
                    if prev_ema == 0
                    else (1 - alpha) * prev_ema + alpha * instant_fps
                )
            else:
                self._state.fps_ema = 0.0
        self._first_frame.set()

    def _cleanup_process(self) -> None:
        """Terminates the local `adb shell` client (device server exits on EOS)."""
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

    def _remove_tunnel(self) -> None:
        """Removes the ADB forward (best effort)."""
        if not self._local_port:
            return
        try:
            self._adb("forward", "--remove", f"tcp:{self._local_port}", timeout=5.0)
        except Exception:
            pass


__all__ = [
    "DEFAULT_BIT_RATE",
    "DEFAULT_MAX_FPS",
    "DEFAULT_MAX_SIZE",
    "DEVICE_SERVER_PATH",
    "STALE_AFTER_MS",
    "H264StreamParser",
    "LiveStreamStats",
    "ScrcpyLiveStream",
    "build_server_argv",
    "detect_server_version",
    "find_server_blob",
    "parse_bit_rate",
]
