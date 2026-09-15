"""Example: Benchmark live-stream latency (sub-100ms verification).

Starts a direct H.264 live stream on the first fleet device, samples
``capture_screen()`` N times, and reports latency percentiles plus decoder
stats (frame age, decode fps). Run against real hardware:

    uv run python examples/05_live_stream.py [--samples 100] [--max-size 960]

Target: p50 capture < 100ms on USB or good WiFi. If p50 is higher, lower
``--max-size`` / bit rate, prefer USB, or check WiFi congestion.
"""

import argparse
import statistics
import sys
import time

from pymordialdroid import FleetCommander


def percentile(values: list[float], pct: float) -> float:
    """Computes a percentile from a sorted-able list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return ordered[rank]


def main() -> int:
    """Runs the latency benchmark against the first fleet device."""
    parser = argparse.ArgumentParser(description="Live-stream latency benchmark")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--max-size", type=int, default=960)
    parser.add_argument("--device", type=int, default=0, help="Fleet rank")
    args = parser.parse_args()

    commander = FleetCommander()
    phones = commander.load_inventory()
    if not phones or not (0 <= args.device < len(phones)):
        print("No device at that rank. Add one with: uv run pymordialdroid add <ip>")
        return 1
    phone = phones[args.device]
    print(f"Starting live stream on {phone.record.name} ...")
    if not phone.start_live_stream(max_size=args.max_size):
        print("Live stream failed to start (PyAV missing? device offline?).")
        return 1

    try:
        latencies: list[float] = []
        misses = 0
        for _ in range(args.samples):
            start = time.perf_counter()
            frame = phone.controller.capture_screen()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if frame:
                latencies.append(elapsed_ms)
            else:
                misses += 1
            time.sleep(0.01)
        stats = phone.get_live_stats()
        print(f"samples={len(latencies)} misses={misses}")
        if latencies:
            print(f"mean={statistics.fmean(latencies):.1f}ms "
                  f"p50={percentile(latencies, 50):.1f}ms "
                  f"p95={percentile(latencies, 95):.1f}ms "
                  f"p99={percentile(latencies, 99):.1f}ms")
        print(f"stream: age_ms={stats.get('age_ms')} fps={stats.get('fps')} "
              f"stale={stats.get('stale')} decoded={stats.get('frames_decoded')} "
              f"size={stats.get('width')}x{stats.get('height')}")
        ok = latencies and percentile(latencies, 50) < 100
        print("TARGET MET (p50 < 100ms)" if ok else "TARGET MISSED (p50 >= 100ms)")
        return 0 if ok else 2
    finally:
        phone.stop_live_stream()


if __name__ == "__main__":
    sys.exit(main())
