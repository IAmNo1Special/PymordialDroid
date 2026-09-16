---
type: Game Profile
title: "Revomon: Novus — Game Profile & Geometry Normalization"
description: "Application package identifiers, Unity engine profile, Web3 Immutable Passport authentication, and normalized coordinate space math."
resource: package:com.revomon.vr.novus
tags: [revomon-novus, unity, architecture, coordinates, geometry, immutable-passport]
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

# Revomon: Novus — Game Profile & Geometry Normalization

> [!NOTE]
> This guide defines the foundational parameters, engine profile, and coordinate system for ***Revomon: Novus***. For UI anchors and menu states, see [HUD Interface](./hud_interface.md). For movement, see [Locomotion Guide](./locomotion.md). For battle systems, see [Combat & Slingshot](./combat_slingshot.md).

---

## 1. Game Profile & Architecture

| Parameter | Specification |
| :--- | :--- |
| **Title** | *Revomon: Novus* |
| **Engine / Platform** | Unity (Android / iOS / Apple) |
| **Package / App ID** | `com.revomon.vr.novus` |
| **Main Activity** | `com.revomon.vr.novus/com.unity3d.player.UnityPlayerActivity` |
| **Client Version** | `v0.0.1` |
| **Authentication Layer** | Web3 Immutable Passport Integration (SSO) |
| **Display Mode** | Fixed Landscape |
| **Coordinate Standard** | Normalized ratio space $(r_x, r_y) \in [0.0, 1.0]$ |

---

## 2. Client Geometry & Resolution Normalization

Because *Revomon: Novus* is deployed across devices with differing aspect ratios (16:9, 19.5:9, 20:9), all UI elements and touch coordinates are mapped as normalized ratios $(r_x, r_y) \in [0.0, 1.0]$.

The target pixel position for any device resolution $(W_{\text{screen}}, H_{\text{screen}})$ is calculated as:

$$X_{\text{target}} = \lfloor r_x \cdot W_{\text{screen}} \rfloor, \quad Y_{\text{target}} = \lfloor r_y \cdot H_{\text{screen}} \rfloor$$

### Reference Resolution
Testing and live field verification are calibrated against the Samsung Galaxy S24 Ultra (`SM-S928U`):
* Screen width: $W = 2340\text{ px}$
* Screen height: $H = 1080\text{ px}$
* Aspect ratio: $19.5:9$
* Live stream feed: $960 \times 442\text{ px}$

See also: [HUD Interface](./hud_interface.md) and [Vision Anchors](./vision_anchors.md).
