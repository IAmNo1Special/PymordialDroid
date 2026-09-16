# PymordialDroid Architecture & Automation Framework

- [Automation Architecture & System Overview](./architecture.md) — High-level host architecture, bundled binary resolution, wireless ADB networking, and Pymordial contract implementations.
- [Configuration & State Management](./configuration.md) — Directory locations, JSON schemas, PIN resolution rules, and window layout persistence in ~/.pymordialdroid/.
- [Display Streaming & Perception Pipeline](./display_streaming.md) — Low-latency scrcpy H.264 video streaming, Direct3D 11 rendering, frame providers, and screencap perception architecture.
- [Touch Injection & Multi-Touch Architecture](./touch_injection.md) — Low-level touch injection backends (scrcpy-control, motionevent, sendevent) and Unified Touch API on AdbDevice.
- [Bugs, Issues & Fieldwork Findings](./issues.md) — Living log of host-side automation gaps, touch injection resolutions, and perception caveats encountered during fieldwork.
