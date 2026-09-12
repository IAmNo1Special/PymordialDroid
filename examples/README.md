# PymordialDroid Examples

End-to-end demo scripts for PymordialDroid.

## Prerequisites

- Python >= 3.13
- `uv` installed
- A physical Android device with USB debugging enabled
- ADB binary available (bundled in `src/pymordialdroid/bin/scrcpy/` or on PATH)

## Setup

```bash
uv run python examples/01_connect_physical_device.py
```

## Scripts

| Script | Topic |
|---|---|
| `01_connect_physical_device.py` | USB discovery, explicit AdbDevice connect, lazy AndroidController connect, wireless ADB pairing |
| `02_launch_app.py` | AndroidApp lifecycle: open, screen capture, verify, close |
| `03_find_and_click.py` | OCR text search, PymordialText elements, OpenCV template matching, clicking found coordinates |
| `04_fleet_management.py` | FleetCommander inventory, per-device control, viewer lifecycle, heartbeat monitor, fleet-wide APK install |

## Notes

- Examples assume a device is reachable at `192.168.1.50:5555` or discovered via USB.
- Update `ip`, `port`, `package_name`, and `template_path` placeholders to match your environment.
- OCR requires the bundled Tesseract binaries or a system `tesseract` installation.
