"""Command-line entry point and runtime bootstrap for PymordialDroid."""

import argparse
import asyncio
import logging
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from pymordialdroid.fleet import FleetCommander
from pymordialdroid.tui import FleetTUI


def setup_logging(verbose: bool = False) -> None:
    """Configures rich console logging handler."""
    logging.basicConfig(
        level="DEBUG" if verbose else "INFO",
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


async def run_app() -> None:
    """Instantiates FleetCommander and runs the interactive TUI."""
    commander = FleetCommander()
    tui = FleetTUI(commander)
    await tui.run()


def cmd_list(args: argparse.Namespace) -> None:
    """Lists all configured fleet devices from inventory."""
    commander = FleetCommander()
    phones = commander.load_inventory()
    console = Console()

    if not phones:
        console.print("[yellow]No devices found in fleet inventory.[/yellow]")
        console.print("Add a device with: [bold]pymordialdroid add <ip>[/bold]")
        return

    table = Table(title="PymordialDroid Fleet Inventory", border_style="cyan")
    # Rank is the 0-based index used by the API, TUI table, viewer titles
    # ("Worker {rank}"), and --device selectors. It must stay 0-based so a
    # displayed rank always addresses the same device everywhere.
    table.add_column("Rank", justify="center", style="dim")
    table.add_column("Name", style="bold green")
    table.add_column("IP", style="cyan")
    table.add_column("Port", justify="right")
    table.add_column("Serial", style="magenta")
    table.add_column("PIN", justify="center", style="dim")

    for idx, phone in enumerate(phones):
        rec = phone.record
        table.add_row(
            str(idx),
            rec.name,
            rec.ip,
            str(rec.port),
            rec.serial,
            rec.pin or "-",
        )

    console.print(table)


def cmd_add(args: argparse.Namespace) -> None:
    """Adds a device to the fleet inventory."""
    console = Console()
    target = args.device
    port = args.port

    # Allow passing IP:port directly as target (e.g. 172.20.8.50:5555)
    if ":" in target and port == 5555:
        parts = target.split(":", 1)
        ip = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            ip = target
    else:
        ip = target

    commander = FleetCommander()
    rec = commander.add_device(
        ip=ip,
        port=port,
        name=args.name,
        pin=args.pin,
        serial=args.serial,
    )
    console.print(
        f"[green][+] Added device:[/green] [bold]{rec.name}[/bold] @ {rec.ip}:{rec.port} (serial={rec.serial})"
    )


def cmd_remove(args: argparse.Namespace) -> None:
    """Removes a device from the fleet inventory."""
    console = Console()
    commander = FleetCommander()
    removed = commander.remove_device(args.identifier)

    if removed:
        console.print(
            f"[green][+] Successfully removed device matching[/green] '[bold]{args.identifier}[/bold]'"
        )
    else:
        console.print(
            f"[red][-] No device matching[/red] '[bold]{args.identifier}[/bold]' [red]found in inventory.[/red]"
        )
        sys.exit(1)


def _resolve_phones(commander: FleetCommander, device: str | None):  # type: ignore[no-untyped-def]
    """Resolves --device selector to Phone list (all when omitted).

    Accepts a 0-based rank (matching `list` output), IP, ip:port, serial,
    or name. Returns [] when nothing matches.
    """
    phones = commander.load_inventory()
    if not phones:
        return []
    if not device:
        return phones
    try:
        rank = int(device)
        if 0 <= rank < len(phones):
            return [phones[rank]]
    except (ValueError, TypeError):
        pass
    idx = commander._find_phone_index(device)
    if idx is not None:
        return [phones[idx]]
    return []


def cmd_record(args: argparse.Namespace) -> None:
    """Starts/stops headless scrcpy MP4 evidence recording."""
    import asyncio

    console = Console()
    commander = FleetCommander()
    targets = _resolve_phones(commander, args.device)
    if not targets:
        console.print("[red]No matching devices in fleet inventory.[/red]")
        sys.exit(1)

    if args.action == "start":
        started = 0
        for phone in targets:
            dest = None
            if args.output_dir:
                from pathlib import Path as _Path

                dest = _Path(args.output_dir) / f"evidence_{phone.record.name}.mp4"
            path = phone.controller.start_recording(
                output_path=dest,
                show_touches=args.show_touches,
                time_limit=args.time_limit,
            )
            if path is not None:
                console.print(f"[green][REC][/green] {phone.record.name} -> {path}")
                started += 1
            else:
                console.print(f"[red][FAIL][/red] {phone.record.name}")
        if started == 0:
            sys.exit(1)
    elif args.action == "stop":
        stopped = 0
        for phone in targets:
            path = phone.controller.stop_recording()
            if path is not None:
                console.print(f"[green][SAVED][/green] {phone.record.name} -> {path}")
                stopped += 1
        console.print(f"Stopped {stopped} recording(s).")
    elif args.action == "evidence":
        clips = asyncio.run(
            commander.record_evidence_all(
                duration_sec=args.duration,
                output_dir=args.output_dir,
                show_touches=args.show_touches,
            )
            if not args.device
            else _record_single_evidence(targets[0], args)
        )
        if isinstance(clips, dict):
            for ip, path in clips.items():
                console.print(f"[green][CLIP][/green] {ip} -> {path}")
            if not clips:
                sys.exit(1)
        elif clips:
            console.print(f"[green][CLIP][/green] {clips}")


async def _record_single_evidence(phone, args):  # type: ignore[no-untyped-def]
    """Records one evidence clip, returning {ip: path} for uniform handling."""
    path = await phone.record_evidence(
        duration_sec=args.duration,
        output_dir=args.output_dir,
        show_touches=args.show_touches,
    )
    return {phone.record.ip: path} if path else {}


def cmd_feed(args: argparse.Namespace) -> None:
    """Starts/stops low-latency screen feeds for fast frame polling."""
    console = Console()
    commander = FleetCommander()
    targets = _resolve_phones(commander, getattr(args, "device", None))
    if not targets:
        console.print("[red]No matching devices in fleet inventory.[/red]")
        sys.exit(1)

    backend = getattr(args, "backend", "live")
    if args.action == "start":
        if backend == "live":
            count = commander.start_live_streams(
                max_size=args.max_size,
                max_fps=args.max_fps,
                new_display=args.new_display,
                targets=targets,
            )
            console.print(f"Started {count} live H.264 stream(s) (<100ms target).")
        else:
            count = commander.start_headless_feeds(
                max_size=args.max_size,
                max_fps=args.max_fps,
                new_display=args.new_display,
                targets=targets,
            )
            console.print(f"Started {count} headless MKV feed(s) (fallback).")
        if count == 0:
            sys.exit(1)
    else:
        live = commander.stop_live_streams(targets=targets)
        mkv = commander.stop_headless_feeds(targets=targets)
        console.print(f"Stopped {live} live stream(s), {mkv} MKV feed(s).")


def build_parser() -> argparse.ArgumentParser:
    """Builds CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="pymordialdroid",
        description="PymordialDroid: Android device fleet automation using Scrcpy and ADB",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # tui subcommand
    subparsers.add_parser(
        "tui", help="Launch interactive Fleet Commander TUI dashboard"
    )

    # list subcommand (aliases: devices, ls)
    subparsers.add_parser(
        "list", aliases=["devices", "ls"], help="List configured devices"
    )

    # add subcommand
    add_parser = subparsers.add_parser(
        "add", help="Add or update a device in fleet inventory"
    )
    add_parser.add_argument("device", help="Device IP address (or ip:port)")
    add_parser.add_argument(
        "--port", "-p", type=int, default=5555, help="ADB TCP port (default: 5555)"
    )
    add_parser.add_argument(
        "--name", "-n", type=str, default=None, help="Custom device name"
    )
    add_parser.add_argument(
        "--pin", type=str, default=None, help="Device lockscreen PIN"
    )
    add_parser.add_argument(
        "--serial", "-s", type=str, default=None, help="Explicit ADB serial"
    )

    # remove subcommand (aliases: rm)
    rm_parser = subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove a device from fleet inventory"
    )
    rm_parser.add_argument("identifier", help="Device IP, serial, or name to remove")

    # record subcommand: headless MP4 evidence clips
    record_parser = subparsers.add_parser(
        "record", help="Headless scrcpy MP4 evidence recording (no window)"
    )
    record_parser.add_argument(
        "action",
        choices=["start", "stop", "evidence"],
        help="start: begin recording; stop: finalize; evidence: time-limited clip",
    )
    record_parser.add_argument(
        "--device",
        "-d",
        default=None,
        help="IP, serial, name, or 0-based rank from list (default: all)",
    )
    record_parser.add_argument(
        "--output-dir", default=None, help="Evidence output directory"
    )
    record_parser.add_argument(
        "--show-touches", action="store_true", help="Show touch trails in clip"
    )
    record_parser.add_argument(
        "--time-limit", type=int, default=None, help="Auto-stop start after N seconds"
    )
    record_parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Clip length for evidence (default: 60)",
    )

    # feed subcommand: low-latency screen feeds for fast frame polling
    feed_parser = subparsers.add_parser(
        "feed", help="Low-latency screen feed for fast vision frames"
    )
    feed_parser.add_argument("action", choices=["start", "stop"])
    feed_parser.add_argument(
        "--device",
        "-d",
        default=None,
        help="IP, serial, name, or 0-based rank from list (default: all)",
    )
    feed_parser.add_argument("--max-size", type=int, default=960)
    feed_parser.add_argument("--max-fps", type=int, default=30)
    feed_parser.add_argument(
        "--new-display",
        default=None,
        help='Isolated virtual display, e.g. "1920x1080" (Android 10+)',
    )
    feed_parser.add_argument(
        "--backend",
        choices=["live", "mkv"],
        default="live",
        help="live: direct H.264 (<100ms, default); mkv: file-tail fallback",
    )

    return parser


def main() -> None:
    """Main CLI entry point for pymordialdroid."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.command in ("list", "devices", "ls"):
        cmd_list(args)
    elif args.command == "add":
        cmd_add(args)
    elif args.command in ("remove", "rm"):
        cmd_remove(args)
    elif args.command == "record":
        cmd_record(args)
    elif args.command == "feed":
        cmd_feed(args)
    elif args.command == "tui" or args.command is None:
        try:
            asyncio.run(run_app())
        except KeyboardInterrupt:
            print("\nGoodbye.")
            sys.exit(0)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
