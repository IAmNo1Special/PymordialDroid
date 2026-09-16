---
type: Configuration Reference
title: PymordialDroid — Configuration & State Management
description: Directory locations, JSON schemas, PIN resolution rules, and window layout persistence in ~/.pymordialdroid/.
resource: src/pymordialdroid/config.py
tags: [configuration, json, pin-resolution, fleet-inventory, persistence]
status: stable
generated:
  by: human:IAmNo1Special
  at: 2026-09-16T05:15:00Z
verified:
  - by: human:IAmNo1Special
    at: 2026-09-16T05:15:00Z
sources:
  - id: pymordialdroid-config
    resource: src/pymordialdroid/config.py
    title: PymordialDroid Config Module
---

# PymordialDroid — Configuration & State Management

All dynamic runtime data and user configuration are kept out of the source tree and stored in the user directory `~/.pymordialdroid/` (`%USERPROFILE%\.pymordialdroid` on Windows):

## 1. Global Configuration (`config.json`)

Located at `~/.pymordialdroid/config.json`:

```json
{
    "default_pin": "110516",
    "adb_path": null,
    "scrcpy_path": null
}
```

### PIN Resolution Precedence
When unlocking devices or negotiating pairing codes, PIN is resolved in the following priority:
1. Per-device `pin` in `fleet_inventory.json` (if specified)
2. Environment variable `DEVICE_PIN` or `PYMORDIALDROID_PIN`
3. `default_pin` in `~/.pymordialdroid/config.json`
4. Hardcoded fallback `"1234"`

---

## 2. Fleet Inventory (`fleet_inventory.json`)

Located at `~/.pymordialdroid/fleet_inventory.json`. Holds records of all discovered or manually registered physical and virtual devices:

```json
[
    {
        "serial": "172.20.8.50:5555",
        "ip": "172.20.8.50",
        "port": 5555,
        "name": "Galaxy S24 Ultra",
        "pin": "110516"
    }
]
```

### Fields
* `serial`: Unique ADB serial identifier (`<ip>:<port>` for wireless, hardware serial for USB).
* `ip`: Host IPv4 address.
* `port`: ADB TCP port (typically `5555`).
* `name`: Human-readable label displayed in TUI and window titles.
* `pin`: (Optional) Device-specific screen unlock PIN override.

---

## 3. Viewer Layout Persistence (`viewer_layout.json`)

Located at `~/.pymordialdroid/viewer_layout.json`.

Tracks currently active `scrcpy` viewers across sessions so window arrangements, tile positions, and connected monitors can be restored automatically when restarting Fleet Commander.

```json
{
    "viewers": [
        {
            "serial": "172.20.8.50:5555",
            "x": 100,
            "y": 100,
            "width": 540,
            "height": 1170,
            "title": "Galaxy S24 Ultra"
        }
    ]
}
```

See also: [Architecture Guide](./architecture.md) and [Display Streaming](./display_streaming.md).
