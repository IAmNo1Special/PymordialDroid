"""Low-level multi-touch injection for PymordialDroid.

Why this exists
---------------
``input swipe`` (used by :meth:`AdbDevice.swipe`) emits a single coarse
kernel swipe. Some Unity games (e.g. Revomon Novus) gate camera rotation on
minimum drag distance/velocity, so drags <600px do nothing and sub-30-degree
camera trims are impossible. ``input`` also cannot hold a touch down or drive
two contacts at once (joystick + camera, the way real players use two thumbs).

This module speaks the Linux multi-touch protocol (type-B "slot" protocol)
directly to the touchscreen's ``/dev/input/event*`` node via ``sendevent``:

- ``ABS_MT_SLOT`` selects the contact slot (multi-touch),
- ``ABS_MT_TRACKING_ID`` opens/closes a contact,
- ``ABS_MT_POSITION_X/Y`` move it,
- ``BTN_TOUCH`` tracks any-contact state,
- ``SYN_REPORT`` commits the frame.

Trade-off vs scrcpy control-channel injection
--------------------------------------------
scrcpy's ``INJECT_TOUCH_EVENT`` control messages would also work, but
PymordialDroid's live stream runs scrcpy-server with ``control=false``
(video-only, no control socket — see ``live_stream.py``). Adding control
would mean a second socket plus version-specific message framing (scrcpy 3.x
vs 4.x differ). ``sendevent`` works over the existing pure-Python adb_shell
connection with no extra processes, so it is the primary path.

What could not be verified without hardware
-------------------------------------------
- Whether the adb shell user can write to ``/dev/input/event*`` (usually in
  the ``input`` group on AOSP; Samsung may differ). The injector probes with
  a harmless ``SYN_REPORT`` and falls back to ``input`` commands on
  "Permission denied".
- The touchscreen's native axis orientation vs the display input space. The
  mapper auto-swaps axes when aspect ratios invert; the swap *direction* is a
  field-verification item (see ``validate_touch_injection.py``).
- Whether Unity honors dense move-event streams for fine camera control —
  the entire point of the field validation script.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

log = logging.getLogger("pymordialdroid")

# Linux input event types/codes, numeric (avoids sendevent name lookups).
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0
BTN_TOUCH = 330  # 0x14a
ABS_MT_SLOT = 47  # 0x2f
ABS_MT_TRACKING_ID = 57  # 0x39
ABS_MT_POSITION_X = 53  # 0x35
ABS_MT_POSITION_Y = 54  # 0x36
TRACKING_ID_LIFT = -1

RunCommand = Callable[..., "str | bytes | None"]


@dataclass
class TouchDeviceInfo:
    """A touchscreen input node discovered via ``getevent -p``."""

    node: str  # e.g. "/dev/input/event5"
    name: str  # e.g. "sec_touchscreen"
    x_min: int
    x_max: int
    y_min: int
    y_max: int

    @property
    def x_range(self) -> int:
        return self.x_max - self.x_min

    @property
    def y_range(self) -> int:
        return self.y_max - self.y_min


_ABS_LINE_RE = re.compile(
    r"ABS_MT_(SLOT|POSITION_X|POSITION_Y|TRACKING_ID)\s+\(0x[0-9a-fA-F]+\):"
    r"\s*value\s+-?\d+,\s*min\s+(-?\d+),\s*max\s+(-?\d+)"
)
_DEVICE_BLOCK_RE = re.compile(
    r"add device \d+:\s*(/dev/input/event\d+)(.*?)(?=^add device \d+:|\Z)",
    re.M | re.S,
)
_NAME_RE = re.compile(r'name:\s*"([^"]+)"')


def parse_touch_device(getevent_output: str) -> TouchDeviceInfo | None:
    """Parses ``getevent -p`` output, returning the best touchscreen candidate.

    A candidate must expose ``ABS_MT_SLOT`` + ``ABS_MT_POSITION_X/Y``
    (type-B multi-touch protocol). Preference goes to a device whose name
    contains "touch" (case-insensitive); otherwise the first candidate wins.
    Returns None when no multi-touch device is present.
    """
    candidates: list[tuple[str, str, dict[str, tuple[int, int]]]] = []
    for block in _DEVICE_BLOCK_RE.finditer(getevent_output):
        node, body = block.group(1), block.group(2)
        vals: dict[str, tuple[int, int]] = {}
        for m in _ABS_LINE_RE.finditer(body):
            vals[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        if "SLOT" in vals and "POSITION_X" in vals and "POSITION_Y" in vals:
            name_m = _NAME_RE.search(body)
            name = name_m.group(1) if name_m else ""
            candidates.append((node, name, vals))
    if not candidates:
        return None
    candidates.sort(key=lambda c: 0 if "touch" in c[1].lower() else 1)
    node, name, vals = candidates[0]
    return TouchDeviceInfo(
        node=node,
        name=name,
        x_min=vals["POSITION_X"][0],
        x_max=vals["POSITION_X"][1],
        y_min=vals["POSITION_Y"][0],
        y_max=vals["POSITION_Y"][1],
    )


def discover_touch_device(run_command: RunCommand) -> TouchDeviceInfo | None:
    """Runs ``getevent -p`` on the device and parses the touchscreen node."""
    try:
        output = run_command("getevent -p")
    except Exception as e:
        log.debug(f"getevent -p failed: {e}")
        return None
    if not isinstance(output, str) or not output.strip():
        return None
    return parse_touch_device(output)


def detect_input_size(run_command: RunCommand) -> tuple[int, int] | None:
    """Best-effort detection of the display input coordinate space.

    Prefers ``dumpsys display``'s ``app W x H`` (respects current rotation,
    so a landscape game on a portrait-native phone reports 2340x1080),
    falling back to ``wm size`` (physical, rotation-agnostic).
    """
    try:
        dumpsys = run_command(
            "dumpsys display | grep -E 'mBaseDisplayInfo|mDisplayInfo'"
        )
    except Exception:
        dumpsys = None
    if isinstance(dumpsys, str):
        m = re.search(r"app\s+(\d+)\s*x\s*(\d+)", dumpsys)
        if m:
            return int(m.group(1)), int(m.group(2))
    try:
        wm = run_command("wm size")
    except Exception:
        wm = None
    if isinstance(wm, str):
        m = re.search(r"(\d+)\s*x\s*(\d+)", wm)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


class TouchMapper:
    """Maps caller coordinates (display/input space) to touch-device units.

    The public touch API takes the same coordinates as ``input tap`` /
    ``input swipe`` (the display's current input space — e.g. 2340x1080
    landscape for Revomon Novus on the S24 Ultra). This mapper scales them
    into the touchscreen's native ``getevent -p`` range.

    If the input space and the device range have inverted aspect ratios
    (landscape input vs portrait device range or vice versa), axes are
    swapped automatically. The swap *direction* (which corner is origin)
    cannot be determined without hardware — if taps land mirrored, pass
    ``swap_axes`` explicitly or flip it after field verification.
    """

    def __init__(
        self,
        info: TouchDeviceInfo,
        input_width: int,
        input_height: int,
        swap_axes: bool | None = None,
    ) -> None:
        self.info = info
        self.input_width = input_width
        self.input_height = input_height
        if swap_axes is None:
            input_landscape = input_width >= input_height
            device_landscape = info.x_range >= info.y_range
            swap_axes = input_landscape != device_landscape
        self.swap_axes = swap_axes

    def to_device(self, x: float, y: float) -> tuple[int, int]:
        """Converts an input-space point to native touch-device units."""
        if self.swap_axes:
            x, y = y, x
            iw, ih = self.input_height, self.input_width
        else:
            iw, ih = self.input_width, self.input_height
        info = self.info
        dx = info.x_min + round(x / iw * info.x_range)
        dy = info.y_min + round(y / ih * info.y_range)
        dx = max(info.x_min, min(info.x_max, dx))
        dy = max(info.y_min, min(info.y_max, dy))
        return dx, dy


class TouchInjector:
    """Stateful low-level touch injector built on ``sendevent``.

    Owns slot bookkeeping (tracking IDs, active slots) so callers get a
    simple ``touch_down`` / ``touch_move`` / ``touch_up`` API with optional
    multi-touch via ``slot``. All events for one API call are chained into a
    single ``adb shell`` invocation (``&&``), keeping round trips to one per
    call — important over ADB TCP.
    """

    def __init__(
        self,
        run_command: RunCommand,
        input_width: int | None = None,
        input_height: int | None = None,
        swap_axes: bool | None = None,
    ) -> None:
        self._run = run_command
        self._input_width = input_width
        self._input_height = input_height
        self._swap_axes = swap_axes
        self._info: TouchDeviceInfo | None = None
        self._mapper: TouchMapper | None = None
        self._available: bool | None = None  # None = not probed yet
        self._warned = False
        self._tracking_id = 0
        self._active_slots: set[int] = set()

    # --- availability / discovery ---

    @property
    def available(self) -> bool:
        """True when sendevent injection is usable (probed lazily, cached)."""
        if self._available is None:
            self._available = self._probe()
        return self._available

    @property
    def device_info(self) -> TouchDeviceInfo | None:
        """The discovered touchscreen node (None until probed)."""
        _ = self.available
        return self._info

    def _warn_once(self, msg: str) -> None:
        if not self._warned:
            self._warned = True
            log.warning(f"TouchInjector: {msg}")

    def _probe(self) -> bool:
        info = discover_touch_device(self._run)
        if info is None:
            self._warn_once("no multi-touch input device found via getevent -p")
            return False
        # Permission probe: a bare SYN_REPORT is a harmless no-op frame.
        try:
            out = self._run(f"sendevent {info.node} 0 0 0")
        except Exception as e:
            self._warn_once(f"sendevent probe failed: {e}")
            return False
        if out is None:
            self._warn_once("sendevent probe got no response (transport failure?)")
            return False
        if isinstance(out, str) and "permission denied" in out.lower():
            self._warn_once(
                f"no write permission on {info.node} ({info.name}); "
                "falling back to input commands"
            )
            return False
        iw, ih = self._input_width, self._input_height
        if iw is None or ih is None:
            detected = detect_input_size(self._run)
            if detected is not None:
                iw, ih = detected
            else:
                # Last resort: assume caller space == device native range.
                iw, ih = max(info.x_range, 1), max(info.y_range, 1)
        self._info = info
        self._mapper = TouchMapper(info, iw, ih, swap_axes=self._swap_axes)
        log.info(
            f"TouchInjector: using {info.node} ({info.name or 'unnamed'}) "
            f"range x=[{info.x_min},{info.x_max}] y=[{info.y_min},{info.y_max}], "
            f"input space {iw}x{ih}, swap_axes={self._mapper.swap_axes}"
        )
        return True

    # --- event building ---

    def _seq(self, *events: tuple[int, int, int]) -> str:
        """Builds one shell command from (type, code, value) event tuples."""
        assert self._info is not None
        node = self._info.node
        return " && ".join(f"sendevent {node} {t} {c} {v}" for t, c, v in events)

    def _down_seq(self, slot: int, tracking_id: int, dx: int, dy: int) -> str:
        return self._seq(
            (EV_ABS, ABS_MT_SLOT, slot),
            (EV_ABS, ABS_MT_TRACKING_ID, tracking_id),
            (EV_ABS, ABS_MT_POSITION_X, dx),
            (EV_ABS, ABS_MT_POSITION_Y, dy),
            (EV_KEY, BTN_TOUCH, 1),
            (EV_SYN, SYN_REPORT, 0),
        )

    def _move_seq(self, slot: int, dx: int, dy: int) -> str:
        return self._seq(
            (EV_ABS, ABS_MT_SLOT, slot),
            (EV_ABS, ABS_MT_POSITION_X, dx),
            (EV_ABS, ABS_MT_POSITION_Y, dy),
            (EV_SYN, SYN_REPORT, 0),
        )

    def _up_seq(self, slot: int, last_contact: bool) -> str:
        events = [
            (EV_ABS, ABS_MT_SLOT, slot),
            (EV_ABS, ABS_MT_TRACKING_ID, TRACKING_ID_LIFT),
        ]
        if last_contact:
            events.append((EV_KEY, BTN_TOUCH, 0))
        events.append((EV_SYN, SYN_REPORT, 0))
        return self._seq(*events)

    def _exec(self, command: str) -> bool:
        try:
            return self._run(command) is not None
        except Exception as e:
            log.debug(f"sendevent command failed: {e}")
            return False

    # --- public API ---

    def touch_down(self, x: float, y: float, slot: int = 0) -> bool:
        """Presses a contact down at input-space (x, y) on the given slot."""
        if not self.available or self._mapper is None:
            return False
        self._tracking_id += 1
        dx, dy = self._mapper.to_device(x, y)
        ok = self._exec(self._down_seq(slot, self._tracking_id, dx, dy))
        if ok:
            self._active_slots.add(slot)
        return ok

    def touch_move(self, x: float, y: float, slot: int = 0) -> bool:
        """Moves an already-down contact to input-space (x, y)."""
        if not self.available or self._mapper is None:
            return False
        if slot not in self._active_slots:
            log.warning(f"touch_move on inactive slot {slot}; call touch_down first")
            return False
        dx, dy = self._mapper.to_device(x, y)
        return self._exec(self._move_seq(slot, dx, dy))

    def touch_up(self, slot: int = 0) -> bool:
        """Lifts the contact on the given slot."""
        if not self.available:
            return False
        self._active_slots.discard(slot)
        last_contact = not self._active_slots
        return self._exec(self._up_seq(slot, last_contact))

    def touch_hold(self, x: float, y: float, duration_ms: int, slot: int = 0) -> bool:
        """Holds a contact down for ``duration_ms`` then releases it.

        Single shell invocation: down, ``sleep``, up. The contact stays
        down for the whole sleep because no UP event is emitted in between.
        """
        if not self.available or self._mapper is None:
            return False
        self._tracking_id += 1
        dx, dy = self._mapper.to_device(x, y)
        secs = max(duration_ms, 0) / 1000.0
        command = (
            self._down_seq(slot, self._tracking_id, dx, dy)
            + f" && sleep {secs:.3f} && "
            + self._up_seq(slot, last_contact=True)
        )
        return self._exec(command)

    @contextmanager
    def held_touch(self, x: float, y: float, slot: int = 0) -> Iterator[TouchInjector]:
        """Context manager holding a contact down for the block's duration.

        Use for two-thumb play: hold the joystick with one ``held_touch``
        while issuing ``touch_move`` / ``precise_drag`` on another slot.
        The contact is always released on block exit, even on exception.
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
        """Drags from (x1, y1) to (x2, y2) emitting ``steps`` move events.

        This is the fine-control primitive: unlike ``input swipe`` (one
        coarse kernel swipe), the dense move-event stream lets games that
        gate on drag distance/velocity (e.g. Novus's camera) register small,
        precise drags. Single shell invocation.
        """
        if not self.available or self._mapper is None:
            return False
        if steps < 1:
            raise ValueError("steps must be >= 1")
        self._tracking_id += 1
        dx1, dy1 = self._mapper.to_device(x1, y1)
        parts = [self._down_seq(slot, self._tracking_id, dx1, dy1)]
        delay = max(step_delay_ms, 0) / 1000.0
        for i in range(1, steps + 1):
            fx = x1 + (x2 - x1) * i / steps
            fy = y1 + (y2 - y1) * i / steps
            dx, dy = self._mapper.to_device(fx, fy)
            parts.append(f"sleep {delay:.3f}")
            parts.append(self._move_seq(slot, dx, dy))
        parts.append(self._up_seq(slot, last_contact=True))
        return self._exec(" && ".join(parts))

    @property
    def active_slots(self) -> set[int]:
        """Slots currently held down (per this injector's bookkeeping)."""
        return set(self._active_slots)


__all__ = [
    "TouchDeviceInfo",
    "TouchInjector",
    "TouchMapper",
    "detect_input_size",
    "discover_touch_device",
    "parse_touch_device",
]
