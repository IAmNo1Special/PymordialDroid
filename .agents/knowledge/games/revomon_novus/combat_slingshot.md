---
type: Combat Mechanics
title: "Revomon: Novus — Combat Initiation & Slingshot Mechanics"
description: "Slingshot Aim Mode activation, overworld projectile targeting, encounter triggering, and party battle dynamics."
resource: package:com.revomon.vr.novus
tags: [revomon-novus, combat, slingshot, aim-mode, battle-system, encounters]
status: stable
generated:
  by: human:IAmNo1Special
  at: 2026-09-16T05:15:00Z
verified:
  - by: human:IAmNo1Special
    at: 2026-09-16T05:15:00Z
sources:
  - id: revomon-app
    resource: package:com.revomon.vr.novus
    title: "Revomon: Novus Unity Android Client v0.0.1"
---

# Revomon: Novus — Combat Initiation & Slingshot Mechanics

Unlike traditional monster battlers with random grass encounters, ***Revomon: Novus*** uses physical overworld projectile targeting to initiate battles.

---

## 1. Slingshot Aim Mode Activation

1. **Locating Encounters**: Wild Revomon spawn as physical 3D entities roaming fields outside city safe zones (e.g. Glimmerhaven north fields).
2. **Opening Slingshot Mode**:
   - Tap Master Arrow `(0.952, 0.893)` $\rightarrow$ Action Menu Level 1.
   - Tap Top Button `(0.938, 0.787)` $\rightarrow$ Action Menu Level 2.
   - Tap **Slingshot Aim** `(0.868, 0.685)` `[2031, 740]`:
     - Camera transitions to an over-the-shoulder view.
     - A 3D targeting reticle projects onto display center $(0.500, 0.500)$.
     - Aim button illuminates green to signify active state.

---

## 2. Targeting & Firing

1. **Crosshair Alignment**:
   - Rotate camera view using fine camera drags on the right hemisphere to center the reticle on the wild Revomon.
   - Players retain full locomotion and jump agility while in Aim Mode.
2. **Discharging the Orb**:
   - Tap the Slingshot button a second time: Launches an energy projectile along the center vector.
   - Projectile collision with the target freezes overworld physics and transitions into the turn-based 3D combat stadium.
   - Aim Mode automatically deactivates upon firing.

---

## 3. Battle Lead & Party Management

* **Active Belt**: Tamers carry up to **6 Revomon**.
* **Overworld Companion**: Tapping any of the 6 slots in the Equipped Team menu summons that Revomon into the 3D overworld.
* **Battle Lead Designation**: The Revomon currently summoned alongside the player automatically enters combat as the active lead battler.
* **PC Terminals**: Located inside Revocenters (domed structures with cyan glass). Interacting with PC terminals allows depositing, withdrawing, and party healing while completely suppressing the server AFK disconnect timer.

See also: [HUD Interface](./hud_interface.md) and [Locomotion Guide](./locomotion.md).
