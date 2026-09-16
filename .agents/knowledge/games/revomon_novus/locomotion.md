---
type: Locomotion Guide
title: "Revomon: Novus — Locomotion, Camera & Multi-Touch Controls"
description: "Virtual analog stick kinematics, continuous locomotion, camera yaw/pitch dynamics, and dual-thumb multi-touch protocols."
resource: package:com.revomon.vr.novus
tags: [revomon-novus, locomotion, camera, multitouch, joystick, afk-prevention]
status: stable
generated:
  by: human:IAmNo1Special
  at: 2026-09-16T05:15:00Z
verified:
  - by: human:IAmNo1Special
    at: 2026-09-16T05:15:00Z
sources:
  - id: s24-ultra-locomotion
    resource: device:SM-S928U
    title: "Galaxy S24 Ultra Multi-Touch Locomotion Fieldwork"
---

# Revomon: Novus — Locomotion, Camera & Multi-Touch Controls

---

## 1. Virtual Movement Pad Kinematics

The analog joystick floats in the lower-left display quadrant with resting center anchor at:
$$(r_x, r_y) = (0.120, 0.650) \implies (280, 702)\text{ px on }2340 \times 1080$$

### Displacement Vectors
* **Forward (Up)**: `(280, 702) -> (280, 550)`. Velocity $\approx 4\text{ m/s}$ ($\sim 5\text{--}7\text{ m}$ displacement over $1500\text{ ms}$).
* **Backward (Down)**: `(280, 702) -> (280, 850)`. Avatar performs immediate $180^\circ$ turn facing camera.
* **Strafe Left**: `(280, 702) -> (80, 702)`. Left profile visible.
* **Strafe Right**: `(280, 702) -> (480, 702)`. Right profile visible.
* **Anti-AFK Micro-Nudge**: `swipe(280, 702 -> 280, 550, 400ms)` + $0.8\text{ s}$ settle. Safe to interleave every $<180\text{ s}$ to prevent server idle disconnects.

### Continuous Locomotion Pattern
* **Legacy (ADB `input swipe`)**: Repeated $1500\text{ ms}$ swipes incur a stop-start stuttering gait because swipe releases touch at conclusion.
* **Modern (`held_touch` / `touch_hold`)**: Holding touch via `dev.touch_down(280, 702)` -> `dev.touch_move(280, 550)` produces seamless, continuous forward walking indefinitely with zero stutter.

---

## 2. Camera Dynamics & Rotation

* **Camera Surface**: Entire right hemisphere $x \in [1170, 2340]$, $y \in [100, 980]$. Use $y = 540$ center to avoid HUD elements.
* **Unity Deadzone Bypass**: Unity gates coarse single-swipe rotation under 600 px. Emitting dense, interpolated move streams (`dev.precise_drag(steps=24, step_delay_ms=16)`) completely bypasses the deadzone, enabling sub-$30^\circ$ trims for crosshair aiming.
* **Yaw Gain (Large Flicks)**:
  * Horizontal swipe of $800\text{ px}$ $\approx 120^\circ$ rotation.
  * Horizontal swipe of $1000\text{ px}$ $\approx 120^\circ\text{--}150^\circ$ rotation.
* **Pitch Range**: Clamped to approximately $\pm 15\text{--}20^\circ$. Default pitch is optimal for navigation.

---

## 3. Concurrent Two-Thumb Multi-Touch Protocol

True two-thumb play (holding forward locomotion while rotating the camera concurrently) is executed via PymordialDroid's `scrcpy-control` multi-touch daemon:

```python
# Initialize persistent multi-touch daemon
dev.start_multitouch()

# Left Thumb (slot 0): Grab joystick knob and push forward
dev.touch_down(280, 702, slot=0)
dev.touch_move(280, 550, slot=0)

# Right Thumb (slot 1): Concurrently rotate camera via precise interpolated drag
dev.precise_drag(1755, 540, 1305, 540, steps=35, step_delay_ms=25, slot=1)

# Release Left Thumb
dev.touch_up(slot=0)
```

* **Latency**: $<1\text{ ms}$ over localhost tunnel.
* **Tested Environment**: Samsung Galaxy S24 Ultra (`SM-S928U`, Android 16), zero dropped frames.

---

## 4. Server Inactivity Rules & Anti-AFK

* **Inactivity Disconnect**: Server terminates sessions after **1260 seconds (21 minutes)** of inactivity.
* **Camera Input Does NOT Reset AFK**: Only character locomotion vectors on the virtual joystick reset the server idle timer.
* **Safe Parking**: Opening the Fullscreen Map modal suppresses the idle timeout.

See also: [Touch Injection Architecture](../../pymordialdroid/touch_injection.md) and [HUD Interface](./hud_interface.md).
