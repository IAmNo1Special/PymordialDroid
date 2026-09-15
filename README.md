# PymordialDroid

> High-performance real Android device fleet automation and display streaming using `scrcpy` and `adb`.

Part of the Pymordial ecosystem, focused on Android device automation (physical phones, wireless ADB fleets, and future Android runtimes). Built directly on the core [Pymordial](https://github.com/IAmNo1Special/Pymordial) abstractions (`PymordialController`, `PymordialBridgeDevice`, `PymordialVisionDevice`, `PymordialApp`). For BlueStacks emulator automation, see [PymordialBlue](https://github.com/IAmNo1Special/PymordialBlue).

## Features

- **Pymordial Core Integration**: Fully implements Pymordial's abstract device and controller architecture:
  - `AndroidController`: High-level controller orchestrating bridge, vision, display, and OCR plugins.
  - `AdbDevice`: Hardware/network bridge via ADB (pure-Python + binary fallback, wireless port 5555 management, touch gestures like `swipe` and `go_back`, shell escaping, and app lifecycle).
  - `AndroidUiDevice`: Vision device implementing aspect ratio & resolution scaling (`scale_img_to_screen`), OpenCV normalized template matching (`PymordialImage`), pixel color sampling (`PymordialPixel`), and OCR routing (`PymordialText`).
  - `TesseractDevice`: OCR engine supporting image text extraction and bounding box coordinate resolution (`find_text`), bundled with portable Windows Tesseract binaries.
  - `DefaultExtractStrategy`: Preprocessing pipeline (upscaling, grayscale, fast non-local means denoising, Otsu thresholding, inversion).
  - `ScrcpyDevice`: Display streaming and viewport control using `scrcpy`.
  - `AndroidApp`: Concrete Android application lifecycle with state machine management.
- **Fleet Commander Rich TUI**: Interactive dashboard displaying device status, IP, battery percentage, latency, and real-time activity.
- **Scrcpy Video Streaming**: Fast, low-latency 60 FPS video feeds with auto-tiling grid window management.
- **Ghost Mode**: Screen-off background viewer mode to minimize device power consumption during automation.
- **Auto-Healing Heartbeat**: Background task that actively monitors connections and reconnects dropped devices.
- **Auto-Discovery**: Subnet sweep and USB discovery to detect devices, enable TCP/IP port 5555, and register fleet members.
- **Centralized Runtime State**: Fleet inventories and viewer arrangements are stored cleanly in `~/.pymordialdroid/`.

## Architecture & Ecosystem

```
               ┌────────────────────────┐
               │       Pymordial        │
               │ (Abstract Base Classes)│
               └───────────┬────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
  ┌──────────────────────┐    ┌──────────────────────┐
  │   PymordialDroid     │    │    PymordialBlue     │
  │  (Android Platform,  │    │ (BlueStacks Windows  │
  │  Physical/Wireless)  │    │  Emulator Automation)│
  └──────────────────────┘    └──────────────────────┘
```

## Quick Start

### 1. Run via UV

Launch the interactive Fleet Commander TUI:

```bash
uv run pymordialdroid
```

Manage fleet inventory directly from the command line:

```bash
# List all registered devices
uv run pymordialdroid list

# Add or update a device
uv run pymordialdroid add 172.20.8.50 --name "Galaxy S24 Ultra"
uv run pymordialdroid add 172.20.8.50:5555 --pin 110516

# Remove a device by IP, serial, or name
uv run pymordialdroid remove 172.20.8.50
uv run pymordialdroid rm "Galaxy S24 Ultra"
```

Or run as module:

```bash
uv run python -m pymordialdroid
```

### 2. Python API Usage

```python
from pymordialdroid import AndroidApp, AndroidController

# Define your target Android app
app = AndroidApp(
    app_name="Settings",
    package_name="com.android.settings",
)

# Initialize controller connected to an ADB device
controller = AndroidController(ip="172.20.8.50", port=5555)

# Connect and manage app lifecycle
controller.connect()
controller.launch_app("Settings")
```

### 3. Configuration (`~/.pymordialdroid/`)

Configuration files are located at `%USERPROFILE%\.pymordialdroid\` on Windows (or `~/.pymordialdroid/` on Linux/macOS):
- `config.json`: Default PIN and binary path overrides.
- `fleet_inventory.json`: Device records (serial, IP, port, custom name).
- `viewer_layout.json`: Open viewer states for session restoration.

For full architecture and setup documentation, see [.agents/knowledge/pymordialdroid_automation_stack.md](.agents/knowledge/pymordialdroid_automation_stack.md).
