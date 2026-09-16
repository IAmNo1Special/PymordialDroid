# PymordialDroid — Bugs, Issues & Shortcomings (from Revomon Novus fieldwork)

> Living log of host-side automation gaps encountered while playing Revomon Novus on Galaxy S24 Ultra (R5CX51P0AMK over USB). Updated 2026-09-15.

## 1. No fine camera control (HIGH — blocks pixel-perfect aiming)

* **Symptom:** horizontal camera drags <600 px in 2340x1080 landscape space produce zero rotation (verified: 6× 200 px/300 ms, 600 px/800 ms, 600 px/300 ms → bit-identical frames). Only ≥800 px flicks rotate (~120–150° each). Sub-30° trims for slingshot crosshair alignment are impossible.
* **Status (Field-verified 2026-09-16):** RESOLVED for fine camera rotation via interpolated swipe / drag. 200 px drags now register and rotate the camera properly on Galaxy S24 Ultra.

## 2. No touch-hold / continuous-press primitive (HIGH — movement stutters)

* **Symptom:** joystick travel requires repeated `swipe(...,1500ms)` + 1 s settle; every release stops the avatar -> stop-start gait, ~1 action per 2–3 s. Cannot hold forward while adjusting camera (real players use two thumbs).
* **Needed:** `touch_down(x,y)` / `touch_move(x,y)` / `touch_hold(ms)` / `touch_up()` API on `AdbDevice` + `AndroidController`, ideally multi-touch (left stick + right camera + Jump tap concurrently).
* **Field Findings & Resolution (2026-09-16):**
  - **SELinux Permissions:** On non-rooted retail Samsung hardware (Galaxy S24 Ultra, One UI 6.1+), SELinux restricts `shell` from writing directly to `/dev/input/event*` (`sendevent` gets `Permission denied`), preventing multi-touch slot injection without root.
  - **Native Android 14+ `motionevent` Primitive:** Shell *does* have full permission to invoke `input motionevent <DOWN|MOVE|UP> <x> <y>`.
  - **Joystick Hold Sequence:** Grabbing the virtual joystick center `(280, 702)` and dragging to push position `(280, 550)` via `input motionevent DOWN 280 702 && input motionevent MOVE 280 550 && sleep N && input motionevent UP 280 550` produces smooth, continuous forward walking for the entire duration with zero stop-start stutter. Verified live on Galaxy S24 Ultra.
* **Library backend (2026-09-16, branch `feature/touch-injection-work`):** `MotionEventInjector` (`devices/motionevent_injector.py`) implements `touch_down/move/up/cancel/hold/held_touch/precise_drag` over `input motionevent`. `AdbDevice` now selects backends **motionevent → sendevent → legacy `input swipe`** (probed once, cached); `touch_cancel` added for stuck-pointer recovery. Coordinates are display/input-space pixels, same as `input tap` — no touchscreen-axis mapping. Stock `input` is **single-pointer only** (no pointer-id arg) — two-thumb concurrent joystick+camera is NOT possible via motionevent; needs an `app_process` helper (out of scope). Also fixed: sendevent probe previously missed the SELinux denial because `input`/adb_shell drops stderr — all probes now append `2>&1`.
* **Multi-touch RESOLVED via scrcpy-control (field-verified 2026-09-16, Galaxy S24 Ultra / Android 16 / scrcpy 4.1, Novus in foreground):** Malcom's on-machine agent drove a live dual-thumb scenario over scrcpy-server's control socket — 100% pass: pointer 0 DOWN joystick (280,702) → drag (280,550) hold; pointer 1 DOWN camera (1400,500) while pointer 0 held; 25 interpolated MOVEs rotating the camera 200px over ~1.5s; independent UPs. Zero permission issues, zero SELinux blocks. Protocol: push blob to `/data/local/tmp/scrcpy-server.jar`; start `app_process / com.genymobile.scrcpy.Server 4.1 scid=<31-bit hex, MUST be < 0x80000000 or Java Integer.parseInt throws> log_level=info video=false audio=false control=true send_dummy_byte=true send_device_meta=false raw_stream=false tunnel_forward=true cleanup=false`; `adb forward tcp:<port> localabstract:scrcpy_<scid8>`; `recv(1)` for the `0x00` dummy byte (the forward accepts before the server is ready — the dummy byte is the real handshake); touch packets are `INJECT_TOUCH_EVENT`, 32 bytes big-endian `struct.pack('>BBQiiHHHii', 2, action, pointer_id, x, y, width, height, 0xffff, 0, 0)` with action 0=DOWN/1=UP/2=MOVE; raw display pixels with `video=false`. Server-side `PointersState` auto-synthesizes ACTION_POINTER_DOWN/UP. Library: `devices/scrcpy_control.py` (`ScrcpyControlClient` lifecycle + `ScrcpyControlInjector`, slot == pointer_id); `AdbDevice` policy — daemon starts lazily on first slot>0 request or when explicitly enabled (`scrcpy_control=True` / `start_multitouch()`), then serves all slots; fallbacks remain motionevent (single-pointer) → sendevent (rooted) → swipe. Shared-session reuse with the video live stream (`control=true` on one server) is a documented follow-up, not built yet.

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
