# PymordialDroid — Bugs, Issues & Shortcomings (from Revomon Novus fieldwork)

> Living log of host-side automation gaps encountered while playing Revomon Novus on Galaxy S24 Ultra (R5CX51P0AMK over USB). Updated 2026-09-15.

## 1. No fine camera control (HIGH — blocks pixel-perfect aiming)

* **Symptom:** horizontal camera drags <600 px in 2340x1080 landscape space produce zero rotation (verified: 6× 200 px/300 ms, 600 px/800 ms, 600 px/300 ms → bit-identical frames). Only ≥800 px flicks rotate (~120–150° each). Sub-30° trims for slingshot crosshair alignment are impossible.
* **Root cause:** `AdbDevice.swipe()` → `input swipe x1 y1 x2 y2 duration` emits a single kernel swipe; Unity camera appears to gate on minimum drag distance/velocity.
* **Needed:** low-level touch injection (`sendevent`/`getevent` replay or scrcpy HID) exposing down/move/hold/up primitives + configurable move-event density, so small precise drags register.

## 2. No touch-hold / continuous-press primitive (HIGH — movement stutters)

* **Symptom:** joystick travel requires repeated `swipe(...,1500ms)` + 1 s settle; every release stops the avatar → stop-start gait, ~1 action per 2–3 s. Cannot hold forward while adjusting camera (real players use two thumbs).
* **Needed:** `touch_down(x,y)` / `touch_move(x,y)` / `touch_hold(ms)` / `touch_up()` API on `AdbDevice` + `AndroidController`, ideally multi-touch (left stick + right camera + Jump tap concurrently).

## 3. No built-in anti-AFK scheduler (MEDIUM)

* **Symptom:** Revomon logs out after 3–5 min unless left-stick moves; camera/menu input doesn't count. Every script must hand-roll `swipe(280,702→280,550,400ms)` keep-alives <60 s. We got logged out mid-session (02:40:42Z) during a camera-only phase because of this.
* **Needed:** optional `controller.enable_keepalive(stick_center, interval_s=45, profile='micro-nudge')` background task that pauses during live user swipes and resumes after.

## 4. Landscape/portrait coordinate confusion (MEDIUM)

* **Symptom:** `wm size` = 1080x2340 portrait, game renders 2340x1080 landscape, live feed = `960x442` (actual, not 960x440). Callers must hand-convert normalized ratios and live-frame detections (sx=2340/960=2.4375, sy=1080/442≈2.4434). Easy to tap mirrored/wrong spots.
* **Needed:** `AndroidController` display-orientation helpers: `to_device_coords(rx,ry)`, `from_live_frame(x,y)`, auto-detect current rotation via `dumpsys display`. **At minimum:** a `get_screen_resolution()` method that returns the actual frame dimensions from the live stream header, so vision code can dynamically scale rather than hardcoding 960x440.

## 5. `screencap -p` default is too slow for real-time play (LOW — worked around)

* **Symptom:** plain screenshot path costs ~1–2 s; unusable for navigation servoing.
* **Workaround verified 2026-09-15:** `start_live_stream(max_size=960, max_fps=30)` → 44–69 fps, age 11–40 ms; `capture_screen()` auto-routes via feed. **IMPORTANT:** live stream actual resolution is `960x442` (not 960x440). For state detection requiring OCR or pixel analysis, use `controller.bridge.run_command("screencap -p", decode=False)` for full 2340×1080 resolution. `capture_screen()` returns live-frame when stream is active, causing coordinate mismatches if callers assume 2340×1080. Keep this as the documented default for game automation; consider auto-starting feed on `AndroidController(ip, port)` when package is a known game.

## 6. Menu taps require manual keep-alive + close discipline (LOW)

* **Symptom:** Unity UI canvas swallows touches while menus open; forgetting to re-tap Master Arrow leaves input dead and AFK timer running.
* **Needed:** `open_menu_path([...])` helper that taps a button chain, screenshots-verify each level, auto-closes, and fires a keep-alive nudge.

## 7. `capture_screen()` returns wrong-resolution frame when live stream is active (MEDIUM — causes coordinate bugs)

* **Symptom:** When `start_live_stream()` is running, `controller.capture_screen()` returns the live-stream frame (960×442) instead of a full-resolution 2340×1080 screenshot. Code that assumes 2340×1080 (e.g., hardcoded anchor coordinates) silently taps wrong positions because it's analyzing a 960×442 frame with 2340×1080 coordinates.
* **Root cause:** `AndroidController.capture_screen()` prefers `self.scrcpy.get_latest_frame()` (960×442) before falling back to `self.bridge.capture_screenshot()` (2340×1080 via `screencap -p`). The frame provider is set via `set_frame_provider(self.scrcpy.get_latest_frame)` during `start_live_stream()`.
* **Workaround:** Bypass `capture_screen()` for vision analysis; call `controller.bridge.run_command("screencap -p", decode=False)` directly for full-res. Or stop the live stream before capturing full-res.
* **Needed:** A `get_frame_size()` method on `AndroidController` that returns the current frame dimensions from the active source, so vision code can dynamically scale. Or a `capture_fullres()` method that always does `screencap -p` regardless of live stream state.

## 8. No `screencap`-while-streaming helper (LOW)

* **Symptom:** To get a full-resolution screenshot while the live stream is active, callers must know the internal detail that `controller.bridge.run_command("screencap -p", decode=False)` bypasses the frame provider. This is undocumented and unintuitive.
* **Needed:** `AndroidController.capture_fullres()` method that always does `screencap -p` regardless of live stream state, returning 2340×1080 PNG bytes.
