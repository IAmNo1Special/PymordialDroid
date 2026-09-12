"""Command-line entry point and runtime bootstrap for PymordialDroid."""

import argparse
import asyncio
import logging
import sys

from rich.logging import RichHandler

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


def main() -> None:
    """Main CLI entry point for pymordialdroid."""
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
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        print("\nGoodbye.")
        sys.exit(0)


if __name__ == "__main__":
    main()
