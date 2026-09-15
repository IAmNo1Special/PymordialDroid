"""Scrcpy host runner managing display streaming, ghost mode, and physical screen controls."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymordialdroid.config import (
    DATA_DIR,
    SystemConfig,
    get_default_pin,
    resolve_system_config,
)
from pymordialdroid.window import WindowLayoutConfig, get_viewer_window_title

if TYPE_CHECKING:
    from pymordialdroid.devices.adb_device import AdbDevice

log = logging.getLogger("pymordialdroid")


def _sanitize_filename(value: str) -> str:
    """Sanitizes a device name for use in output filenames."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return cleaned or "device"


class ScrcpyDevice:
    """Manages scrcpy desktop display streaming, ghost mode, and hardware controls.

    Operates as the physical device host/display runner in PymordialDroid.

    Beyond interactive viewers, this device owns headless helpers ordered by
    frame latency (fastest first):

    - live stream (direct scrcpy-server H.264 via :mod:`pymordialdroid.live_stream`,
      sub-100ms on USB/good WiFi),
    - recording (``--record`` MP4 evidence clips, no window), and
    - MKV file feed (``--no-window --record`` MKV tailed via OpenCV; higher
      latency, kept as a no-PyAV fallback).
    """

    name: str = "scrcpy"
    version: str = "0.1.0"

    def __init__(
        self,
        bridge_adb: AdbDevice,
        ip: str = "127.0.0.1",
        port: int = 5555,
        device_name: str = "AndroidDevice",
        pin: str | None = None,
        system_config: SystemConfig | None = None,
        layout_config: WindowLayoutConfig | None = None,
    ) -> None:
        self._bridge_adb = bridge_adb
        self.ip = ip
        self.port = port
        self.device_name = device_name
        self.pin = pin
        self.system_config = system_config or resolve_system_config()
        self.layout_config = layout_config or WindowLayoutConfig()

        self._process: subprocess.Popen | None = None
        self._ghost_mode: bool = False

        # Headless recording state (MP4 evidence clips, no window).
        self._record_process: subprocess.Popen | None = None
        self._record_path: Path | None = None

        # Headless live-feed state (delegates to live stream; kept for compat).
        self._latest_frame_bytes: bytes | None = None
        self._latest_frame_time: float = 0.0

        # Sub-100ms live stream state (direct scrcpy-server H.264).
        self._live_stream: Any = None

    def initialize(self, config: Any = None) -> None:
        """Initializes the scrcpy plugin."""
        pass

    def set_bridge_device(self, bridge: AdbDevice) -> None:
        """Injects/replaces the ADB bridge (plugin wiring)."""
        self._bridge_adb = bridge

    def shutdown(self) -> None:
        """Terminates viewer, live stream, and recording processes."""
        self.close()
        self.stop_live_stream()
        self.stop_recording()

    # --- INTERNAL HELPERS ---

    def _serial(self) -> str:
        """Returns the ADB serial for this device."""
        return f"{self.ip}:{self.port}"

    def _ensure_adb_endpoint(self) -> None:
        """Registers the endpoint with the CLI adb server for scrcpy binaries."""
        self._bridge_adb.ensure_cli_endpoint()

    def _scrcpy_env(self) -> dict[str, str]:
        """Environment ensuring scrcpy shells out to the bundled ADB."""
        env = os.environ.copy()
        env["ADB"] = str(self.system_config.adb_bin_path)
        return env

    @staticmethod
    def _terminate(proc: subprocess.Popen | None) -> None:
        """Terminates a child process, escalating to kill on timeout."""
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

    def open(
        self,
        rank: int = 0,
        ghost: bool = False,
        extra_args: list[str] | None = None,
        new_display: str | None = None,
        start_app: str | None = None,
        show_touches: bool = False,
    ) -> bool:
        """Launches a scrcpy display stream window.

        Args:
            rank: Grid position for window tiling.
            ghost: If True, passes ``--turn-screen-off`` (ignored when
                ``new_display`` is set — virtual displays leave the
                physical screen alone).
            extra_args: Raw extra scrcpy flags appended verbatim.
            new_display: Virtual display spec, e.g. ``"1920x1080"``,
                ``"1920x1080/420"``, or ``""`` for device defaults.
                Passed as ``--new-display=<spec>`` plus
                ``--no-vd-system-decorations``.
            start_app: Android package to launch on the new display,
                e.g. ``"com.android.settings"``. Passed as
                ``--start-app=<package>``. Most useful with ``new_display``.
            show_touches: If True, passes ``--show-touches`` so automation
                touches are visible (demos, failure analysis).
        """
        if self.is_running():
            return True

        args = list(extra_args or [])
        if new_display is not None:
            if ghost:
                log.warning(
                    "ghost=True ignored with new_display — virtual display "
                    "does not mirror the physical screen."
                )
            self._ghost_mode = False
            args.append(
                f"--new-display={new_display}" if new_display else "--new-display"
            )
            if not any(a.startswith("--no-vd-system-decorations") for a in args):
                args.append("--no-vd-system-decorations")
            if start_app:
                args.append(f"--start-app={start_app}")
        else:
            if ghost:
                args.append("--turn-screen-off")
                self._ghost_mode = True
            else:
                self._ghost_mode = False
            if start_app:
                args.append(f"--start-app={start_app}")
        if show_touches and "--show-touches" not in args and "-t" not in args:
            args.append("--show-touches")

        self._ensure_adb_endpoint()
        env = self._scrcpy_env()

        pos_x, pos_y = self.layout_config.get_position(rank)
        window_title = get_viewer_window_title(rank, self.device_name)

        cmd = [
            str(self.system_config.scrcpy_bin_path),
            "--serial",
            f"{self.ip}:{self.port}",
            "--max-size",
            "1024",
            "--video-bit-rate",
            "2M",
            "--max-fps",
            "30",
            "--window-title",
            window_title,
            "--window-x",
            str(pos_x),
            "--window-y",
            str(pos_y),
            "--window-width",
            str(self.layout_config.width),
            "--window-height",
            str(self.layout_config.height),
            "--no-audio",
            "--stay-awake",
        ]
        cmd.extend(args)

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=self.system_config.scrcpy_bin_path.parent,
                shell=False,
                env=env,
            )
            return True
        except Exception as e:
            log.error(f"Failed to launch scrcpy: {e}")
            return False

    def close(self) -> bool:
        """Terminates the active scrcpy viewer process."""
        if self._process is not None:
            self._terminate(self._process)
            self._process = None
            return True
        return False

    def is_running(self) -> bool:
        """Checks if the scrcpy viewer process is actively executing."""
        return self._process is not None and self._process.poll() is None

    def open_virtual_display(
        self,
        rank: int = 0,
        display: str = "1920x1080",
        start_app: str | None = None,
        extra_args: list[str] | None = None,
    ) -> bool:
        """Opens a viewer on an isolated virtual display (background automation).

        Uses ``--new-display=<display>`` so automation runs without mirroring
        or disturbing the physical screen — preferable to ghost mode
        (``--turn-screen-off``) for fleet background work. Requires Android 10+.

        Args:
            rank: Grid position for window tiling.
            display: Virtual display spec, e.g. ``"1920x1080"``,
                ``"1920x1080/420"``, or ``""`` for device defaults.
            start_app: Package launched directly on the virtual display.
            extra_args: Raw extra scrcpy flags appended verbatim.
        """
        return self.open(
            rank=rank,
            ghost=False,
            extra_args=extra_args,
            new_display=display,
            start_app=start_app,
        )

    # --- HEADLESS RECORDING (Feature 2) ---

    def start_recording(
        self,
        output_path: str | Path | None = None,
        show_touches: bool = False,
        time_limit: int | None = None,
        record_orientation: int | None = None,
        extra_args: list[str] | None = None,
    ) -> Path | None:
        """Starts headless MP4 evidence recording (no window).

        Args:
            output_path: Destination file. Defaults to
                ``~/.pymordialdroid/evidence/<device>_<epoch>.mp4``.
            show_touches: Adds ``--show-touches`` for visible automation trails.
            time_limit: Auto-stop after N seconds (``--time-limit``).
            record_orientation: Locks record orientation (``--record-orientation``).
            extra_args: Raw extra scrcpy flags appended verbatim.

        Returns:
            Output path on success, None on failure. Idempotent — returns the
            active path if already recording.
        """
        if self.is_recording():
            return self._record_path

        if output_path is None:
            evidence_dir = DATA_DIR / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            safe = _sanitize_filename(self.device_name or self.ip)
            output_path = evidence_dir / f"{safe}_{int(time.time())}.mp4"
        path = Path(output_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self._ensure_adb_endpoint()

        cmd = [
            str(self.system_config.scrcpy_bin_path),
            "--serial",
            self._serial(),
            "--no-window",
            "--no-audio",
            "--max-size",
            "1024",
            "--record",
            str(path),
            "--record-format",
            "mp4",
        ]
        if show_touches:
            cmd.append("--show-touches")
        if time_limit is not None:
            cmd.extend(["--time-limit", str(time_limit)])
        if record_orientation is not None:
            cmd.extend(["--record-orientation", str(record_orientation)])
        cmd.extend(extra_args or [])

        try:
            self._record_process = subprocess.Popen(
                cmd,
                cwd=self.system_config.scrcpy_bin_path.parent,
                shell=False,
                env=self._scrcpy_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._record_path = path
            log.info(f"Recording {self.device_name} -> {path}")
            return path
        except Exception as e:
            log.error(f"Failed to start scrcpy recording: {e}")
            self._record_process = None
            self._record_path = None
            return None

    def stop_recording(self) -> Path | None:
        """Stops headless recording, returning the output path (if any)."""
        if self._record_process is None:
            return None
        self._terminate(self._record_process)
        self._record_process = None
        path = self._record_path
        self._record_path = None
        if path is not None:
            log.info(f"Stopped recording {self.device_name} -> {path}")
        return path

    def is_recording(self) -> bool:
        """Checks if a headless recording is actively running."""
        return self._record_process is not None and self._record_process.poll() is None

    # --- HEADLESS LIVE FEED (Feature 1) ---
    #
    # NOTE: The original MKV-file-tailing implementation (scrcpy --no-window --record
    # to MKV) was fundamentally broken: scrcpy will not decode video frames without a
    # display surface, so the MKV file stays at 0 bytes. This method now delegates to
    # the working live-stream path (direct scrcpy-server H.264 via PyAV, Feature 3),
    # which achieves sub-100ms latency without a window or virtual display.

    def start_headless_feed(
        self,
        output_path: str | Path | None = None,
        max_size: int = 1024,
        max_fps: int = 30,
        bit_rate: str = "2M",
        new_display: str | None = None,
        start_app: str | None = None,
        extra_args: list[str] | None = None,
        timeout: float = 30.0,
    ) -> bool:
        """Starts a headless live stream (sub-100ms, no window, no MKV file).

        Delegates to :meth:`start_live_stream` which connects directly to the
        on-device scrcpy-server with ``raw_stream=true`` and decodes H.264 via
        PyAV. This is the only reliable headless frame source; the legacy MKV
        file feed was removed because scrcpy cannot decode frames without a
        display surface.

        Args:
            output_path: Ignored (kept for API compatibility).
            max_size: Longer edge cap in pixels (smaller decodes faster).
            max_fps: Frame rate cap.
            bit_rate: Video bit rate, e.g. ``"2M"``.
            new_display: Ignored (live stream doesn't use virtual displays).
            start_app: Optional package to launch with the stream.
            extra_args: Ignored.
            timeout: Seconds to wait for push/connect/first-frame (default 30s
                for WiFi reliability).

        Returns:
            True once the first frame has decoded.
        """
        # Ignore MKV-specific args; delegate to the working live stream.
        return self.start_live_stream(
            max_size=max_size,
            bit_rate=bit_rate,
            max_fps=max_fps,
            new_display=new_display,
            timeout=timeout,
        )

    def stop_headless_feed(self) -> bool:
        """Stops the headless live stream. Returns True if a stream was stopped."""
        return self.stop_live_stream()

    def is_feed_running(self) -> bool:
        """Checks if the headless live stream is actively running."""
        return self.is_live_running()

    # --- SUB-100MS LIVE STREAM (Feature: direct scrcpy-server H.264) ---

    def start_live_stream(
        self,
        max_size: int = 960,
        bit_rate: str | int = "2M",
        max_fps: float | None = 30,
        new_display: str | None = None,
        timeout: float = 20.0,
    ) -> bool:
        """Starts a direct H.264 live stream (no window, no container muxing).

        Connects to the on-device scrcpy-server with ``raw_stream=true`` and
        decodes with PyAV, keeping only the latest frame. Expected latency is
        under 100ms on USB or good WiFi at the defaults; verify with
        :meth:`get_live_stats`.

        Args:
            max_size: Longer edge cap in pixels (smaller decodes faster).
            bit_rate: e.g. ``"2M"`` or ``2000000`` (lower fits WiFi better).
            max_fps: Frame rate cap. The encoder only emits on content change.
            new_display: Optional virtual display spec (``"1920x1080"``).
            timeout: Seconds to wait for push/connect/first-frame.

        Returns:
            True once the first frame has decoded. Never raises.
        """
        if self.is_live_running():
            return True
        try:
            from pymordialdroid.live_stream import ScrcpyLiveStream, find_server_blob

            blob = find_server_blob(self.system_config.scrcpy_bin_path)
            if blob is None:
                log.error("scrcpy-server blob not found next to scrcpy binary.")
                return False
            stream = ScrcpyLiveStream(
                serial=self._serial(),
                adb_bin=str(self.system_config.adb_bin_path),
                server_blob=blob,
                max_size=max_size,
                bit_rate=bit_rate,
                max_fps=max_fps,
                stay_awake=True,
                new_display=new_display,
            )
            if stream.start(timeout=timeout):
                self._live_stream = stream
                log.info(f"Live stream running for {self.device_name}.")
                return True
            return False
        except Exception as e:
            log.error(f"Failed to start live stream: {e}")
            return False

    def stop_live_stream(self) -> bool:
        """Stops the live stream. Returns True if one was running."""
        stream, self._live_stream = self._live_stream, None
        if stream is None:
            return False
        try:
            stream.stop()
        except Exception as e:
            log.debug(f"Live stream stop failed: {e}")
        log.info(f"Stopped live stream {self.device_name}.")
        return True

    def is_live_running(self) -> bool:
        """Checks if the sub-100ms live stream is actively running."""
        stream = self._live_stream
        if stream is None:
            return False
        try:
            return bool(stream.is_running())
        except Exception:
            return False

    def get_live_stats(self) -> dict:
        """Returns latency measurements (age_ms, stale, fps, frames, size).

        ``stale`` means no frame arrived for >1.5s while the stream is up —
        normal on a static screen (the encoder only emits on change), so use
        ``running`` (socket/thread alive) to tell "idle" apart from "dead".
        """
        stream = self._live_stream
        if stream is None:
            return {"running": False, "has_frame": False, "stale": False}
        try:
            stats = stream.get_stats()
            return {
                "running": stats.running,
                "has_frame": stats.has_frame,
                "age_ms": stats.age_ms,
                "stale": stats.stale,
                "fps": round(stats.fps, 1),
                "frames_decoded": stats.frames_decoded,
                "bytes_received": stats.bytes_received,
                "width": stats.width,
                "height": stats.height,
            }
        except Exception:
            return {"running": False, "has_frame": False, "stale": False}

    def get_latest_frame(self) -> bytes | None:
        """Returns the latest frame as PNG bytes, fastest source first.

        Prefers the sub-100ms live stream, then the MKV file feed, then the
        cached frame. Never raises: returns the cached frame (possibly None)
        when nothing is readable. Callers (e.g. ``AdbDevice.capture_screenshot``
        via frame provider) should fall back to ``screencap -p`` on None.
        """
        frame = self.get_latest_frame_numpy()
        if frame is None:
            return self._latest_frame_bytes
        try:
            import cv2

            ok, buf = cv2.imencode(".png", frame)
            if ok:
                png = buf.tobytes()
                self._latest_frame_bytes = png
                self._latest_frame_time = time.time()
                return png
        except Exception as e:
            log.debug(f"Frame PNG encode failed: {e}")
        return self._latest_frame_bytes

    def get_latest_frame_numpy(self):  # type: ignore[no-untyped-def]
        """Returns the latest frame as a BGR numpy array from live stream."""
        live = self._live_stream
        if live is not None:
            try:
                frame = live.get_latest_numpy()
            except Exception:
                frame = None
            if frame is not None:
                return frame
        return None

    # --- PHYSICAL SCREEN CONTROLS ---

    def toggle_power(self) -> None:
        """Wakes the physical screen via power button event."""
        self._bridge_adb.run_command("input keyevent 26")

    def unlock_device(self, pin: str | None = None) -> None:
        """Wakes screen and inputs PIN if locked."""
        resolved_pin = pin or self.pin or get_default_pin()

        # 1. Wake
        self._bridge_adb.run_command("input keyevent 26")
        time.sleep(0.5)

        # 2. Dismiss swipe
        self._bridge_adb.run_command("input keyevent 82")
        time.sleep(0.5)

        # 3. Enter PIN (numeric PIN needs no shell quoting)
        self._bridge_adb.run_command(f"input text {resolved_pin}")
        time.sleep(0.2)

        # 4. Enter
        self._bridge_adb.run_command("input keyevent 66")


__all__ = [
    "ScrcpyDevice",
]
