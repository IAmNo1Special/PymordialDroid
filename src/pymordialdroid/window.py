"""Window management, grid calculations, and Win32 display tiling for PymordialDroid."""

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from pymordialdroid.models import DeviceRecord

log = logging.getLogger("pymordialdroid")


@dataclass(frozen=True)
class WindowLayoutConfig:
    """Configuration for auto-tiling scrcpy viewer windows."""

    width: int = 320
    height: int = 600
    title_bar_padding: int = 30
    row_size: int = 3

    def get_position(self, rank: int) -> tuple[int, int]:
        """Calculates grid (x, y) coordinates for a given device rank."""
        col = rank % self.row_size
        row = rank // self.row_size
        pos_x = col * self.width
        pos_y = row * (self.height + self.title_bar_padding)
        return pos_x, pos_y


def get_viewer_window_title(rank: int, device_name: str) -> str:
    """Generates the standardized window title for a device viewer."""
    return f"Worker {rank}: {device_name}"


def organize_windows_grid(
    devices: Sequence[DeviceRecord],
    config: WindowLayoutConfig | None = None,
) -> int:
    """Organizes open scrcpy windows into a tiled grid on Windows.

    Returns:
        The number of windows successfully found and repositioned.
    """
    if sys.platform != "win32":
        log.warning("Window grid tiling is only supported on Windows.")
        return 0

    if not devices:
        return 0

    import ctypes

    cfg = config or WindowLayoutConfig()
    user32 = ctypes.windll.user32
    count = 0

    for i, rec in enumerate(devices):
        pos_x, pos_y = cfg.get_position(i)
        title = get_viewer_window_title(i, rec.name)
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.MoveWindow(hwnd, pos_x, pos_y, cfg.width, cfg.height, True)
            count += 1

    return count
