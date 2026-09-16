---
type: Vision Specification
title: "Revomon: Novus — Vision Grounding & State Detection Anchors"
description: "Computer vision heuristics, normalized color masks, title screen detection, and asset template matching."
resource: package:com.revomon.vr.novus
tags: [revomon-novus, vision, opencv, state-detection, title-screen, color-masks, templates]
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

# Revomon: Novus — Vision Grounding & State Detection Anchors

Because Revomon uses custom stylized fonts that cause OCR engines (e.g. Tesseract) to fail, vision automation relies on **normalized color thresholding**, **pixel-diff analysis**, and **OpenCV template matching**.

---

## 1. Title Screen & Authentication Detection

### 1.1 Play Button Anchor
* Normalized Region: $(r_x, r_y) \in [0.35, 0.65] \times [0.78, 0.92]$
* Tap Coordinate: `(0.500, 0.829)` $\implies (1170, 895)\text{ px}$
* Color Filter: Yellow pixels $(R > 180, G > 140, B < 120)$
* Detection Rule: Relative threshold **$>5\%$ yellow pixels** within bounding box. (At $2340 \times 1080 \approx 1800\text{ px}$; at $960 \times 442 \approx 19\text{ px}$).

### 1.2 Connect Button Anchor
* Normalized Region: $(0.40, 0.76)\text{--}(0.60, 0.82)$
* Tap Coordinate: `(0.500, 0.823)` $\implies (1170, 888)\text{ px}$
* Color Filter: Cyan pixels $(R > 150, G > 190, B > 100)$

### 1.3 Space Background Filter
* Title screen features animated celestial backdrop with $>15\%$ dark pixels $(\text{brightness} < 30)$, whereas overworld contains $<2\%$.

---

## 2. Overworld Verification Heuristic

A frame represents an active, controllable overworld state when:
1. `get_current_app()` equals `com.revomon.vr.novus`.
2. `is_title_screen()` evaluates `False`.
3. Dark pixel ratio $<10\%$ and black ratio $<25\%$ (not a loading bar).
4. At least one HUD landmark is verified:
   - **Minimap Compass**: Bright blips ($>10\text{ px}$, $\text{brightness} > 200$) in $(0.04, 0.05)\text{--}(0.10, 0.18)$.
   - **Shop Icon**: Light pixels ($>15\text{ px}$, $\text{brightness} > 200$) in $(0.85, 0.06)\text{--}(0.92, 0.12)$.
   - **Location Label**: Light pixels in $(0.15, 0.06)\text{--}(0.30, 0.16)$.

---

## 3. Reconnection Flow Timing

When an AFK disconnect returns the client to title:
1. Tap **Play** $\rightarrow$ Immutable Passport initializes in background ($\approx 4\text{--}5\text{ s}$).
2. Tap **Connect** $\rightarrow$ Server connection establishes.
3. **Loading Screen** with green progress bar ($\approx 9\text{--}14\text{ s}$).
4. Total Recovery Time: $\approx 13.6\text{ seconds}$ to return to active overworld state at saved spawn.

---

## 4. UI Asset Crops (`assets/`)

Template assets for OpenCV template matching are stored under `.agents/knowledge/assets/`:
* Minimap radar & player menu buttons: `menu_Avatar.png`, `menu_Clan.png`, `menu_Map.png`, `menu_Market.png`, `menu_Progress.png`.
* Action menu levels: `menu_explore_menu_open.png`, `menu_explore_btn_1_left.png`, `menu_explore_btn_3_top.png`.
* Submenu anchors: `menu_explore_gear_tr.png`, `sidebar_pos_settings_1.png`.

See also: [Game Profile](./profile.md), [HUD Interface](./hud_interface.md), and [Display Streaming](../../pymordialdroid/display_streaming.md).
