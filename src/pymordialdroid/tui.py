"""Rich Terminal User Interface (TUI) and interactive event loop for PymordialDroid."""

import asyncio
import sys
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from pymordialdroid.fleet import FleetCommander

# Windows-specific non-blocking keyboard input
if sys.platform == "win32":
    import msvcrt
else:
    msvcrt = None


class FleetTUI:
    """Manages the Rich Live dashboard, interactive menus, and keyboard controls."""

    def __init__(self, commander: FleetCommander, console: Console | None = None):
        self.commander = commander
        self.console = console or Console()

        # TUI State Machine
        self.tui_mode = "main"  # "main", "select_device", "select_viewer_type", "select_scan_method", "device_details", "select_device_for_details"
        self.selected_device_idx = 0
        self.tui_feedback = ""

    # --- UI RENDERING ---

    def generate_table(self) -> Table:
        """Creates the Rich table displaying device fleet status."""
        table = Table(title="Pymordial Droid Fleet Status", box=box.ROUNDED)
        table.add_column("Rank", justify="center", style="cyan", no_wrap=True)
        table.add_column("Name", style="magenta")
        table.add_column("IP", justify="right", style="green")
        table.add_column("Status", justify="center")
        table.add_column("Battery", justify="right")
        table.add_column("Last Action", style="italic")

        for i, phone in enumerate(self.commander.phones):
            status_style = "green" if phone.status == "Online" else "red"
            if phone.status == "Connecting...":
                status_style = "yellow"

            table.add_row(
                str(i),
                phone.record.name,
                phone.record.ip,
                f"[{status_style}]{phone.status}[/{status_style}]",
                phone.battery_level,
                phone.last_action,
            )
        return table

    def generate_menu_panel(self) -> Panel:
        """Creates a mode-sensitive menu panel with status bar and feedback."""
        if self.tui_mode == "main":
            online = sum(1 for p in self.commander.phones if p._connected)
            total = len(self.commander.phones)
            avg_battery = 0
            if self.commander.phones:
                bat_values = []
                for p in self.commander.phones:
                    if p.battery_level and p.battery_level != "N/A":
                        try:
                            bat_values.append(int(p.battery_level.replace("%", "")))
                        except ValueError:
                            pass
                avg_battery = sum(bat_values) // len(bat_values) if bat_values else 0

            status_bar = f"[dim]Fleet: {online}/{total} Online | Avg Battery: {avg_battery}%[/dim]"
            feedback_line = (
                f"\n[yellow]{self.tui_feedback}[/yellow]" if self.tui_feedback else ""
            )

            menu_text = (
                f"{status_bar}\n"
                "[bold cyan]Commands:[/bold cyan]\n"
                "[1] Add Devices         [5] Install APK\n"
                "[2] Launch Viewer(s)    [6] Device Details\n"
                "[3] Organize Windows    [7] Restore Layout\n"
                f"[4] Kill All Viewers    [Q] Quit{feedback_line}"
            )
            return Panel(menu_text, title="Menu", border_style="blue")

        elif self.tui_mode == "device_details":
            if not self.commander.phones:
                return Panel("No devices available.", border_style="red")
            p = self.commander.phones[self.selected_device_idx]
            details = (
                f"[bold cyan]{p.record.name}[/bold cyan]\n"
                f"IP: {p.record.ip}:{p.record.port}\n"
                f"Serial: {p.record.serial}\n"
                f"Status: {p.status}\n"
                f"Battery: {p.battery_level}\n"
                f"Last Action: {p.last_action}\n\n"
                "[0/Esc] Back"
            )
            return Panel(details, title="Device Details", border_style="cyan")

        elif self.tui_mode == "select_scan_method":
            menu_text = (
                "[bold cyan]Add Devices:[/bold cyan]\n"
                "[1] USB Scan (wired)\n"
                "[2] Hotspot Scan (192.168.137.x)\n"
                "[0/Esc] Cancel"
            )
            return Panel(menu_text, title="Scan Method", border_style="magenta")

        elif self.tui_mode == "select_device":
            lines = ["[bold cyan]Select Device(s):[/bold cyan]", "[A] All Devices"]
            for i, p in enumerate(self.commander.phones):
                lines.append(f"[{i + 1}] {p.record.name} ({p.record.ip})")
            lines.append("[0/Esc] Cancel")
            return Panel("\n".join(lines), title="Select Device", border_style="yellow")

        elif self.tui_mode == "select_viewer_type":
            if self.selected_device_idx == -1:
                target = "All Devices"
            else:
                device = (
                    self.commander.phones[self.selected_device_idx]
                    if self.commander.phones
                    else None
                )
                target = device.record.name if device else "Unknown"
            menu_text = (
                f"[bold cyan]Viewer for: {target}[/bold cyan]\n"
                "[1] Standard Viewer\n"
                "[2] Ghost Viewer (Screen Off)\n"
                "[0/Esc] Cancel"
            )
            return Panel(menu_text, title="Viewer Type", border_style="green")

        elif self.tui_mode == "select_device_for_details":
            lines = ["[bold cyan]View Details For:[/bold cyan]"]
            for i, p in enumerate(self.commander.phones):
                status = "🟢" if p._connected else "🔴"
                lines.append(f"[{i + 1}] {status} {p.record.name}")
            lines.append("[0/Esc] Cancel")
            return Panel("\n".join(lines), title="Select Device", border_style="cyan")

        return Panel("Unknown Mode", border_style="red")

    def generate_layout(self) -> Group:
        """Composes the full TUI layout with table and menu panel."""
        return Group(self.generate_table(), self.generate_menu_panel())

    # --- MAIN EVENT LOOP ---

    async def run(self) -> None:
        """Starts background monitoring and runs the interactive Rich Live loop."""
        self.commander.load_inventory()

        # Background Services
        t1 = asyncio.create_task(self.commander.heartbeat_monitor())
        t2 = asyncio.create_task(self.commander.mission_runner())

        try:
            with Live(
                self.generate_layout(),
                refresh_per_second=4,
                screen=True,
                console=self.console,
            ) as live:
                while self.commander.running:
                    live.update(self.generate_layout())

                    if msvcrt and msvcrt.kbhit():
                        key = msvcrt.getch()
                        await self._handle_key(key, live)

                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        finally:
            t1.cancel()
            t2.cancel()
            await self.commander.shutdown()

    # --- KEYBOARD DISPATCH ---

    async def _handle_key(self, key: bytes, live: Live) -> None:
        """Dispatches keypresses based on the current TUI mode."""
        if key in (b"\x1b", b"0"):  # ESC or 0 cancels/returns to main
            self.tui_mode = "main"
            return

        if self.tui_mode == "main":
            await self._handle_main_key(key, live)
        elif self.tui_mode == "select_scan_method":
            await self._handle_scan_method_key(key, live)
        elif self.tui_mode == "select_device":
            self._handle_device_selection_key(key)
        elif self.tui_mode == "select_viewer_type":
            self._handle_viewer_type_key(key)
        elif self.tui_mode == "device_details":
            self._handle_device_details_key()
        elif self.tui_mode == "select_device_for_details":
            self._handle_device_for_details_selection(key)

    async def _handle_main_key(self, key: bytes, live: Live) -> None:
        if key == b"1":
            self.tui_mode = "select_scan_method"
        elif key == b"2":
            self.tui_mode = "select_device"
        elif key == b"3":
            moved = self.commander.organize_viewers_grid()
            self.tui_feedback = f"Tiled {moved} window(s)."
            asyncio.create_task(self._clear_feedback_after(2))
        elif key == b"4":
            self.commander.kill_viewers()
            self.tui_feedback = "Killed active viewers."
            asyncio.create_task(self._clear_feedback_after(2))
        elif key == b"5":
            await self._install_apk_flow(live)
        elif key == b"6":
            if self.commander.phones:
                self.tui_mode = "select_device_for_details"
        elif key == b"7":
            if self.commander.phones:
                asyncio.create_task(self._restore_viewers_flow())
        elif key in (b"q", b"Q"):
            self.commander.running = False

    async def _handle_scan_method_key(self, key: bytes, live: Live) -> None:
        if key == b"1":
            live.stop()
            self.commander.initialize_fleet_via_usb()
            input("Press Enter to return to TUI...")
            live.start()
            self.tui_mode = "main"
        elif key == b"2":
            asyncio.create_task(self._scan_hotspot_flow())
            self.tui_mode = "main"
        else:
            self.tui_mode = "main"

    def _handle_device_selection_key(self, key: bytes) -> None:
        if not self.commander.phones:
            self.tui_mode = "main"
            return

        if key in (b"a", b"A"):
            self.selected_device_idx = -1  # All devices
            self.tui_mode = "select_viewer_type"
            return

        try:
            idx = int(key.decode()) - 1
            if 0 <= idx < len(self.commander.phones):
                self.selected_device_idx = idx
                self.tui_mode = "select_viewer_type"
            else:
                self.tui_mode = "main"
        except (ValueError, UnicodeDecodeError):
            self.tui_mode = "main"

    def _handle_viewer_type_key(self, key: bytes) -> None:
        ghost = key == b"2"
        if key not in (b"1", b"2"):
            self.tui_mode = "main"
            return

        viewer_type = "Ghost" if ghost else "Standard"
        if self.selected_device_idx == -1:
            self.tui_feedback = f"Launching {viewer_type} viewers for all devices..."
            asyncio.create_task(self._launch_viewers_with_feedback(ghost=ghost))
        else:
            device = self.commander.phones[self.selected_device_idx]
            if device.record.ip in self.commander.open_viewers:
                self.tui_feedback = f"Viewer already open for {device.record.name}"
                asyncio.create_task(self._clear_feedback_after(2))
                self.tui_mode = "main"
                return

            self.tui_feedback = (
                f"Launching {viewer_type} viewer for {device.record.name}..."
            )
            self.commander.launch_device_viewer(self.selected_device_idx, ghost=ghost)
            asyncio.create_task(self._clear_feedback_after(2))

        self.tui_mode = "main"

    def _handle_device_for_details_selection(self, key: bytes) -> None:
        if not self.commander.phones:
            self.tui_mode = "main"
            return

        try:
            idx = int(key.decode()) - 1
            if 0 <= idx < len(self.commander.phones):
                self.selected_device_idx = idx
                self.tui_mode = "device_details"
            else:
                self.tui_mode = "main"
        except (ValueError, UnicodeDecodeError):
            self.tui_mode = "main"

    def _handle_device_details_key(self) -> None:
        self.tui_mode = "main"

    # --- ASYNC FLOW HELPERS ---

    async def _launch_viewers_with_feedback(self, ghost: bool) -> None:
        count = await self.commander.batch_launch_viewers(ghost=ghost)
        self.tui_feedback = f"Launched {count} viewer(s)!"
        await asyncio.sleep(3)
        self.tui_feedback = ""

    async def _restore_viewers_flow(self) -> None:
        self.tui_feedback = "Restoring viewer layout..."
        count = await self.commander.restore_viewers_from_layout()
        self.tui_feedback = f"Restored {count} viewer(s)!"
        await asyncio.sleep(2)
        self.tui_feedback = ""

    async def _scan_hotspot_flow(self) -> None:
        self.tui_feedback = "Scanning hotspot subnet for ADB devices..."
        active_ips = await self.commander.scan_hotspot_for_phones()
        self.tui_feedback = (
            f"Found {len(active_ips)} device(s) on hotspot."
            if active_ips
            else "No new ADB devices found on hotspot."
        )
        await asyncio.sleep(3)
        self.tui_feedback = ""

    async def _clear_feedback_after(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        self.tui_feedback = ""

    async def _install_apk_flow(self, live: Live) -> None:
        live.stop()
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            apk_path = filedialog.askopenfilename(
                title="Select APK to Install",
                filetypes=[("APK files", "*.apk"), ("All files", "*.*")],
            )
            root.destroy()

            if apk_path:
                self.tui_feedback = (
                    f"Installing {Path(apk_path).name} on all devices..."
                )
                live.start()
                await self.commander.install_apk_on_all(apk_path)
                self.tui_feedback = "APK installation complete!"
                await asyncio.sleep(3)
                self.tui_feedback = ""
            else:
                live.start()
                self.tui_feedback = "APK install cancelled."
                await asyncio.sleep(2)
                self.tui_feedback = ""
        except ImportError:
            live.start()
            self.tui_feedback = "File dialog not available (tkinter missing)"
            await asyncio.sleep(3)
            self.tui_feedback = ""
