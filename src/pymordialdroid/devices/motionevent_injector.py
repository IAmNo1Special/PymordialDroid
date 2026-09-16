"""Single-pointer touch injection via `input motionevent` (no root needed).

Why this exists
---------------
The sendevent backend (``touch_injector.py``) speaks the Linux multi-touch
protocol directly to ``/dev/input/event*``, but on non-rooted retail
hardware SELinux denies the shell user write access there — field-verified
on the Galaxy S24 Ultra (One UI 6.1+, Android 16): ``sendevent`` fails with
"Permission denied".

``input motionevent`` is the escape hatch. The ``input`` shell command is
itself a Java program running as the shell user that calls
``InputManager.injectInputEvent`` — the same privileged path — so it works
with SELinux enforcing and no root. Field-verified on the S24 Ultra::

    input motionevent DOWN 280 702 && input motionevent MOVE 280 550 \\
        && sleep 5 && input motionevent UP 280 550

produces smooth, stutter-free continuous joystick walking for the whole
hold, and 200px interpolated camera drags rotate properly (no deadzone).

Research notes (2026-09-16; no hardware in this environment)
------------------------------------------------------------
- Syntax: ``input motionevent <DOWN|MOVE|UP|CANCEL> <x> <y>``. The action
  set is documented by community tooling (android-testing-skills,
  nowjordanhappy/adb-multitouch, memphizzz/androiddraw). CANCEL exists in
  the grammar; its exact on-device behavior is unverified — it is exposed
  here only as a stuck-pointer recovery primitive.
- SINGLE-POINTER ONLY. Per adb-multitouch's README: "`adb shell input` is
  single-touch only because its CLI doesn't expose more — not because of a
  permission limit." True multi-touch (ACTION_POINTER_DOWN/UP) needs a
  custom injector — that role is now filled by ``scrcpy_control.py``
  (scrcpy-server's INJECT_TOUCH_EVENT, field-verified 2026-09-16), which
  ``AdbDevice`` starts lazily for slot > 0. ``slot > 0`` here still logs a
  warning and returns False (never faked).
- A gesture stream SURVIVES ACROSS PROCESSES: each ``input motionevent``
  invocation joins the ongoing gesture. That is why DOWN && sleep && UP —
  whether chained in one shell call or issued as separate calls — works.
- Coordinates are ABSOLUTE DISPLAY/INPUT-SPACE PIXELS — the same space as
  ``input tap`` / ``input swipe``. No TouchMapper scaling is needed:
  (280, 702) worked verbatim in the field. Events go to the focused window.
- Injection is ASYNCHRONOUS: exit 0 means "enqueued", not "the app acted".
- AOSP history: ancient AOSP carried a ``motionevent`` stub printing
  "not yet supported"; the real implementation is recent (observed on
  Android 15+ devices, field-verified on 16; absent from older AOSP whose
  ``input`` only had text/keyevent/tap/swipe/draganddrop/press/roll). The
  runtime probe below is authoritative and does not depend on OS version.
- Caveat (adb-multitouch): an INTERRUPTED gesture leaves the finger down.
  ``held_touch()`` releases on normal exceptions, but a SIGKILL-style kill
  needs manual recovery — ``touch_up()`` or ``touch_cancel()``.
- Probe subtlety: ``input`` writes usage/errors to STDERR, which
  adb_shell's ``shell()`` (ADB v1 service) does NOT capture. Every probe
  and injection command here appends ``2>&1`` so the device shell merges
  stderr into stdout. Without it, "Unknown command" vs "Invalid arguments"
  are indistinguishable (both look like empty output) — and the old
  sendevent permission probe had the same blind spot (fixed to ``2>&1``
  in ``touch_injector.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

log = logging.getLogger("pymordialdroid")

RunCommand = Callable[..., "str | bytes | None"]

# Actions accepted by `input motionevent` (CANCEL: recovery only, unverified).
_ACTION_DOWN = "DOWN"
_ACTION_MOVE = "MOVE"
_ACTION_UP = "UP"
_ACTION_CANCEL = "CANCEL"


def _fmt(v: float) -> str:
    """Formats a coordinate for the shell: ints stay ints (280, not 280.0)."""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


class MotionEventInjector:
    """Stateful single-pointer touch injector built on ``input motionevent``.

    Implements the same public surface as :class:`TouchInjector`
    (``touch_down`` / ``touch_move`` / ``touch_up`` / ``touch_cancel`` /
    ``touch_hold`` / ``held_touch`` / ``precise_drag`` / ``available`` /
    ``active_slots``) so :class:`AdbDevice` can select between backends.
    Multi-touch slots are NOT supported by the underlying command —
    ``slot > 0`` logs a warning and returns False (never faked).

    Coordinates are display/input-space pixels, identical to ``input tap``.
    """

    def __init__(self, run_command: RunCommand) -> None:
        self._run = run_command
        self._available: bool | None = None  # None = not probed yet
        self._warned_slot = False
        self._active = False  # bookkeeping: is our pointer down?
        self._last_x = 0.0
        self._last_y = 0.0

    # --- availability ---

    @property
    def available(self) -> bool:
        """True when the device's ``input`` supports ``motionevent``."""
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        """Detects ``motionevent`` support via its no-arg error output.

        ``input motionevent`` with no args prints to stderr (merged via
        ``2>&1``):
        - supported:   "Error: Invalid arguments for command: motionevent"
        - unsupported: "Error: Unknown command: motionevent"
        - ancient:     "Error: motionevent not yet supported."
        """
        try:
            out = self._run("input motionevent 2>&1")
        except Exception as e:
            log.debug(f"motionevent probe failed: {e}")
            return False
        if not out or not isinstance(out, str):
            return False
        low = out.lower()
        if "unknown command" in low or "not yet supported" in low:
            log.info("input motionevent not supported on this device")
            return False
        if "invalid arguments" in low:
            log.info("input motionevent supported on this device")
            return True
        # Fallback: some OEMs reword usage; if the usage text lists the
        # command at all, it exists (unsupported devices omit it).
        if "motionevent" in low:
            log.info("input motionevent appears supported (usage lists it)")
            return True
        log.debug(f"unrecognized motionevent probe output: {out[:200]!r}")
        return False

    # --- command building / execution ---

    def _me(self, action: str, x: float, y: float) -> str:
        """Builds one `input motionevent` invocation (stderr merged)."""
        return f"input motionevent {action} {_fmt(x)} {_fmt(y)} 2>&1"

    def _exec(self, command: str) -> bool:
        """Runs a shell command; any "error" in output counts as failure.

        Success is silent (``input`` prints nothing on a clean inject), so
        any error text means the injection did not happen.
        """
        try:
            out = self._run(command)
        except Exception as e:
            log.debug(f"motionevent command failed: {e}")
            return False
        if out is None:
            return False
        if isinstance(out, str) and "error" in out.lower():
            log.debug(f"motionevent command reported error: {out.strip()[:160]}")
            return False
        return True

    def _check_slot(self, slot: int) -> bool:
        if slot != 0:
            if not self._warned_slot:
                self._warned_slot = True
                log.warning(
                    "input motionevent is single-pointer only; "
                    f"slot={slot} unsupported (use the sendevent backend "
                    "on rooted devices for multi-touch)"
                )
            return False
        return True

    # --- public API ---

    def touch_down(self, x: float, y: float, slot: int = 0) -> bool:
        """Presses the pointer down at input-space (x, y)."""
        if not self.available or not self._check_slot(slot):
            return False
        ok = self._exec(self._me(_ACTION_DOWN, x, y))
        if ok:
            self._active = True
            self._last_x, self._last_y = float(x), float(y)
        return ok

    def touch_move(self, x: float, y: float, slot: int = 0) -> bool:
        """Moves the already-down pointer to input-space (x, y)."""
        if not self.available or not self._check_slot(slot):
            return False
        if not self._active:
            log.warning("touch_move with no active pointer; call touch_down first")
            return False
        ok = self._exec(self._me(_ACTION_MOVE, x, y))
        if ok:
            self._last_x, self._last_y = float(x), float(y)
        return ok

    def touch_up(self, slot: int = 0) -> bool:
        """Lifts the pointer (at its last known position).

        Always issues the UP, even if bookkeeping thinks nothing is down —
        this doubles as recovery for a pointer stuck down by an interrupted
        gesture (e.g. the process died mid-hold).
        """
        if not self.available or not self._check_slot(slot):
            return False
        ok = self._exec(self._me(_ACTION_UP, self._last_x, self._last_y))
        self._active = False
        return ok

    def touch_cancel(self, slot: int = 0) -> bool:
        """Sends ACTION_CANCEL for the pointer (stuck-gesture recovery).

        Device behavior of CANCEL is unverified; prefer ``touch_up`` for
        normal release and use this when a gesture seems wedged.
        """
        if not self.available or not self._check_slot(slot):
            return False
        ok = self._exec(self._me(_ACTION_CANCEL, self._last_x, self._last_y))
        self._active = False
        return ok

    def touch_hold(self, x: float, y: float, duration_ms: int, slot: int = 0) -> bool:
        """Holds the pointer down for ``duration_ms``, then releases.

        Single shell invocation: DOWN, ``sleep``, UP. The contact stays
        down for the whole sleep because the gesture stream survives across
        the chained commands — no UP is emitted in between.
        """
        if not self.available or not self._check_slot(slot):
            return False
        secs = max(duration_ms, 0) / 1000.0
        command = (
            f"{self._me(_ACTION_DOWN, x, y)}"
            f" && sleep {secs:.3f} && "
            f"{self._me(_ACTION_UP, x, y)}"
        )
        ok = self._exec(command)
        self._active = False
        if ok:
            self._last_x, self._last_y = float(x), float(y)
        return ok

    @contextmanager
    def held_touch(
        self, x: float, y: float, slot: int = 0
    ) -> Iterator[MotionEventInjector]:
        """Context manager holding the pointer down for the block's duration.

        The pointer is always released on block exit, even on exception.
        (A SIGKILL-style kill still needs manual ``touch_up`` recovery.)
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

        The fine-control primitive: unlike ``input swipe`` (one coarse
        kernel swipe), the dense MOVE stream lets games that gate on drag
        distance/velocity register small, precise drags. Single shell
        invocation; ``&&`` chaining means a failed DOWN aborts the rest
        instead of leaving orphan MOVEs.
        """
        if not self.available or not self._check_slot(slot):
            return False
        if steps < 1:
            raise ValueError("steps must be >= 1")
        delay = max(step_delay_ms, 0) / 1000.0
        parts = [self._me(_ACTION_DOWN, x1, y1)]
        for i in range(1, steps + 1):
            fx = x1 + (x2 - x1) * i / steps
            fy = y1 + (y2 - y1) * i / steps
            parts.append(f"sleep {delay:.3f}")
            parts.append(self._me(_ACTION_MOVE, fx, fy))
        parts.append(self._me(_ACTION_UP, x2, y2))
        ok = self._exec(" && ".join(parts))
        self._active = False
        if ok:
            self._last_x, self._last_y = float(x2), float(y2)
        return ok

    @property
    def active_slots(self) -> set[int]:
        """Slots currently held down — always {0} or empty (single-pointer)."""
        return {0} if self._active else set()


__all__ = ["MotionEventInjector"]
