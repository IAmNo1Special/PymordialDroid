# Knowledge Changelog

## 2026-09-16
- **Refactoring**: Sharded monolithic knowledge files (`pymordialdroid.md`, `pymordialdroid_issues.md`, `revomon_novus_knowledge.md`) into modular, domain-specific concepts under `pymordialdroid/` and `games/revomon_novus/`.
- **Standardization**: Converted all concepts to Open Knowledge Format (OKF v0.2) compliance with YAML frontmatter, trust signals, and progressive disclosure directory indices.
- **Update**: Documented non-root `scrcpy-control` multi-touch daemon (`devices/scrcpy_control.py`) and resolution of concurrent dual-thumb input.
- **Update**: Recorded field findings for native Android 14+ `motionevent` single-pointer touch hold and SELinux limitations on `sendevent`.
- **Update**: Added fine camera rotation calibration bypassing Unity deadzone using dense interpolated drags (`precise_drag`).
- **Update**: Documented 100% menu navigation, sidebar hierarchy, calibrated 2340x1080 combat stadium overlay (VS badge, attack cards, capsules), minimap radar wild Revomon blips, and waypoint reset in `games/revomon_novus/hud_interface.md`.
- **Update**: Documented 3-tap title screen re-login sequence (`Play` -> `Connect` -> `Welcome <User>` banner) in `games/revomon_novus/vision_anchors.md`.

## 2026-09-15
- **Creation**: Initial fieldwork logs and Revomon: Novus game mechanics repository established.
