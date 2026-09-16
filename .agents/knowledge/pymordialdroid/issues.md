---
type: Issue Tracker
title: PymordialDroid — Bugs, Issues & Fieldwork Findings
description: Living log of host-side automation gaps, touch injection resolutions, and perception caveats encountered during fieldwork.
resource: src/pymordialdroid/devices/
tags: [issues, touch-injection, motionevent, scrcpy-control, selinux, s24-ultra]
status: stable
generated:
  by: human:IAmNo1Special
  at: 2026-09-16T05:15:00Z
verified:
  - by: human:IAmNo1Special
    at: 2026-09-16T05:15:00Z
sources:
  - id: s24-ultra-fieldwork
    resource: device:SM-S928U
    title: Samsung Galaxy S24 Ultra Live Verification
---

# PymordialDroid — Bugs, Issues & Fieldwork Findings

Living log of host-side automation gaps encountered while testing game automation on physical hardware (Galaxy S24 Ultra, `SM-S928U`, over USB/TCP). Updated 2026-09-16.

---

## 1. Fine Camera Deadzone (HIGH — Resolved)
* **Symptom:** Single coarse swipes <600 px in 2340x1080 space produced zero rotation because the game engine gated on minimum swipe velocity/distance.
* **Resolution (2026-09-16):** Resolved via `precise_drag` on `AdbDevice` which emits dense, interpolated move events (e.g. 24 steps with 16 ms spacing). 200 px drags now register reliably for sub-30° camera trims.

---

## 2. Touch Hold & Multi-Touch Injection (HIGH — Resolved)
* **Symptom:** Standard ADB `input swipe` always lifts the touch at completion, causing a stop-start stuttering gait during continuous locomotion.
* **SELinux Denial:** Non-rooted retail Samsung hardware forbids shell UID from writing directly to `/dev/input/event*` (`sendevent` gets `Permission denied`).
* **Single-Pointer Hold:** Implemented via `MotionEventInjector` (`input motionevent <DOWN|MOVE|UP>`). Allows smooth continuous walking for arbitrary durations on slot 0.
* **Multi-Touch Resolution:** Resolved via `scrcpy-control` (`ScrcpyControlInjector`, `devices/scrcpy_control.py`). Headless scrcpy server accepts binary multi-touch packets without root or SELinux restrictions, enabling simultaneous dual-thumb gameplay (joystick on slot 0 + camera drag on slot 1).

---

## 3. Built-In Anti-AFK Scheduler (MEDIUM)
* **Symptom:** Game server disconnects idle players after inactivity (up to 21 min; shorter when camera-only movements occur). Camera swipes do not reset the server idle timer.
* **Workaround:** Periodic virtual joystick micro-nudges (`swipe(280, 702 -> 280, 550, 400ms)`) every <180 s keep the session alive.
* **Needed:** Background worker on `AndroidController` to automate keep-alive micro-nudges when no user actions are executing.

---

## 4. Landscape / Portrait Coordinate Confusion (MEDIUM)
* **Symptom:** `wm size` reports `1080x2340` (portrait), while games run in `2340x1080` (landscape), and live streams output `960x442`.
* **Needed:** Dynamic orientation helpers: `to_device_coords(rx, ry)` and `from_live_frame(x, y)`.

---

## 5. Live Stream Actual Resolution Mismatch (MEDIUM — Worked Around)
* **Symptom:** With `start_live_stream(max_size=960)`, scrcpy rounds downscaled dimensions to even values, yielding `960x442` rather than `960x440`. Code hardcoding 440 suffers a 2 px vertical drift.
* **Workaround:** Dynamic inspection of frame dimensions: `frame.shape[:2]`.

---

## 6. Menu UI Touch Interception (LOW)
* **Symptom:** Unity UI canvas intercepts touches when menus remain open, leaving navigation input dead.
* **Requirement:** Strict discipline to close menus via Master Arrow re-tap before issuing locomotion commands.

---

## 7. `capture_screen()` Frame Provider Bug (MEDIUM)
* **Symptom:** When live stream is active, `capture_screen()` returns the 960x442 live frame instead of native 2340x1080 screenshot, breaking routines that expect full-resolution coordinates.
* **Workaround:** Bypass frame provider for high-resolution OCR using `controller.bridge.run_command("screencap -p", decode=False)`.
* **Needed:** Explicit `capture_fullres()` method on `AndroidController`.

---

See also: [Touch Injection Architecture](./touch_injection.md) and [Display Streaming](./display_streaming.md).
