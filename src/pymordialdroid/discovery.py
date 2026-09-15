"""Hardware device discovery via wired USB ADB and subnet hotspot sweep."""

import asyncio
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

from adb_shell.adb_device_async import AdbDeviceTcpAsync
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from pymordialdroid.models import DeviceRecord

log = logging.getLogger("pymordialdroid")


def clean_adb_shell_output(raw: str | bytes) -> str | None:
    """Normalizes `adb shell` output, rejecting empty/placeholder values."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if not raw:
        return None
    # ADB shell often returns trailing \r\n; take first non-empty line.
    for line in raw.replace("\r", "\n").split("\n"):
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.lower() in ("null", "unknown", "(unknown)"):
            continue
        return cleaned
    return None


def query_device_name_via_adb(
    adb_path: Path | str, serial: str, timeout: float = 5.0
) -> str | None:
    """Best-effort query of the user-visible device name via ADB CLI.

    Tries, in order:
      1. `settings get global device_name` (e.g. "Galaxy S24 Ultra")
      2. `getprop ro.config.marketing_name` (e.g. "Galaxy S24 Ultra")
      3. `getprop ro.product.model` (e.g. "SM-S928B")

    Returns None if ADB is missing, device is offline/unauthorized, or all
    queries come back empty.
    """
    adb_str = str(adb_path)
    commands: list[list[str]] = [
        [adb_str, "-s", serial, "shell", "settings", "get", "global", "device_name"],
        [adb_str, "-s", serial, "shell", "getprop", "ro.config.marketing_name"],
        [adb_str, "-s", serial, "shell", "getprop", "ro.product.model"],
    ]
    for cmd in commands:
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        except Exception:
            continue
        if res.returncode != 0:
            continue
        name = clean_adb_shell_output(res.stdout)
        if name:
            return name
    return None


def discover_usb_devices(
    adb_path: Path | str,
    prompt_for_names: bool = True,
) -> list[DeviceRecord]:
    """Scans USB-connected devices, configures persistent TCP/IP on port 5555,

    extracts their Wi-Fi IP address via `ip route`, and returns DeviceRecord list.
    """
    adb_str = str(adb_path)
    log.info("Scanning USB devices...")

    try:
        cmd = [adb_str, "devices"]
        output = subprocess.check_output(cmd).decode("utf-8")
        lines = output.strip().split("\n")[1:]
        serials = [line.split()[0] for line in lines if "device" in line]
    except Exception as e:
        log.error(f"Error querying USB devices: {e}")
        serials = []

    if not serials:
        log.warning("No USB devices found.")
        return []

    discovered: list[DeviceRecord] = []
    for serial in serials:
        log.info(f"Processing USB device {serial}...")
        subprocess.run([adb_str, "-s", serial, "tcpip", "5555"], check=False)
        time.sleep(3)

        cmd_ip = [adb_str, "-s", serial, "shell", "ip", "route"]
        try:
            res = subprocess.check_output(cmd_ip).decode("utf-8")
            match = re.search(r"src (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", res)
            if match:
                ip = match.group(1)
                log.info(f"  Found IP: {ip}")
                # Prefer the real on-device name; fall back to USB serial.
                detected = query_device_name_via_adb(adb_str, serial) or serial
                name = detected
                if prompt_for_names:
                    try:
                        user_name = input(
                            f"  Name for {serial} [{detected}]: "
                        ).strip()
                        if user_name:
                            name = user_name
                    except (EOFError, KeyboardInterrupt):
                        pass
                rec = DeviceRecord(serial=f"{ip}:5555", ip=ip, port=5555, name=name)
                discovered.append(rec)
        except Exception as e:
            log.error(f"Error retrieving IP for {serial}: {e}")

    return discovered


async def scan_hotspot_devices(
    signer: PythonRSASigner,
    base_ip: str = "192.168.137",
    start_host: int = 2,
    end_host: int = 254,
    max_concurrency: int = 64,
) -> list[str]:
    """Scans the local hotspot subnet concurrently to find active Android ADB devices.

    Args:
        signer: RSA key signer for testing ADB connection.
        base_ip: Subnet prefix, default Windows Mobile Hotspot "192.168.137".
        start_host: First host IP octet to ping.
        end_host: Last host IP octet to ping.
        max_concurrency: Cap on parallel ping/handshake tasks.

    Returns:
        List of responding IP addresses.
    """
    return await scan_subnets_for_phones(
        signer,
        base_ips=[base_ip],
        start_host=start_host,
        end_host=end_host,
        max_concurrency=max_concurrency,
    )


def get_local_subnet_bases() -> list[str]:
    """Returns sorted /24 bases (e.g. '172.20.8') for up IPv4 interfaces.

    Skips loopback and link-local; keeps RFC-1918 first, then any other
    private/site-local address. Never raises — returns [] on failure.
    """
    try:
        import ipaddress
        import socket

        import psutil

        bases: list[str] = []
        seen: set[str] = set()
        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                ip = (addr.address or "").strip()
                if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                try:
                    parts = ip.split(".")
                    if len(parts) != 4:
                        continue
                    # Skip non-IPv4-looking / invalid octets.
                    if not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                        continue
                    base = ".".join(parts[:3])
                    # Prefer RFC-1918; still accept others (e.g. 172.20.x
                    # carrier NAT, 10.x lab nets) — just order them last.
                    if base not in seen:
                        seen.add(base)
                        bases.append(base)
                except Exception:
                    continue
        def _sort_key(b: str) -> tuple:
            try:
                is_private = ipaddress.ip_address(b + ".1").is_private
            except Exception:
                is_private = False
            return (0 if is_private else 1, b)

        return sorted(bases, key=_sort_key)
    except Exception:
        return []


def get_inventory_subnet_bases(ips: list[str]) -> list[str]:
    """Derives /24 bases from known device IPs (e.g. fleet on 172.20.8.x)."""
    bases: list[str] = []
    seen: set[str] = set()
    for ip in ips:
        parts = (ip or "").strip().split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            base = ".".join(parts[:3])
            if base not in seen:
                seen.add(base)
                bases.append(base)
    return sorted(bases)


def get_arp_candidate_ips() -> list[str]:
    """Harvests live-host IPs from the OS ARP/neighbor cache.

    Covers large subnets (e.g. /16 where the PC is 172.20.1.x but the
    phone is 172.20.8.x) without ping-sweeping 65k addresses.
    Never raises — returns [] on failure.
    """
    import re

    cmds: list[list[str] | str] = (
        [["arp", "-a"]]
        if sys.platform == "win32"
        else [["arp", "-an"], ["ip", "neigh", "show"]]
    )
    found: set[str] = set()
    for cmd in cmds:
        try:
            if isinstance(cmd, str):
                res = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=10
                )
            else:
                res = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=10, check=False
                )
        except Exception:
            continue
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        for m in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3})", out):
            ip = m.group(1)
            try:
                parts = [int(p) for p in ip.split(".")]
                if len(parts) != 4 or not all(0 <= p <= 255 for p in parts):
                    continue
                if parts[3] in (0, 255):
                    continue
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                if ip.startswith("224."):
                    continue
                found.add(ip)
            except Exception:
                continue
        if found:
            break
    return sorted(found)


async def scan_subnets_for_phones(
    signer: PythonRSASigner,
    base_ips: list[str],
    start_host: int = 2,
    end_host: int = 254,
    max_concurrency: int = 64,
    extra_ips: list[str] | None = None,
) -> list[str]:
    """Sweeps multiple /24 subnets for ADB devices with bounded concurrency.

    Args:
        extra_ips: Additional specific IPs to ADB-check (e.g. ARP cache
            candidates on a /16). Deduped against the sweep range.
    """
    subnets = sorted(set(b.strip().strip(".") for b in base_ips if b and b.strip()))
    if not subnets:
        return []
    log.info(f"--- Scanning {', '.join(subnets)} ({start_host}-{end_host}) ---")

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def check_ip(ip: str) -> str | None:
        async with sem:
            if sys.platform == "win32":
                cmd = f"ping -n 1 -w 200 {ip}"
            else:
                cmd = f"ping -c 1 -W 0.2 {ip}"

            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

            if proc.returncode == 0:
                log.info(f"[FOUND] {ip} is alive. Checking ADB handshake...")
                device = AdbDeviceTcpAsync(ip, 5555, default_transport_timeout_s=2)
                try:
                    await device.connect(rsa_keys=[signer], auth_timeout_s=2)
                    log.info(f"  [SUCCESS] Adopted {ip}!")
                    await device.close()
                    return ip
                except Exception:
                    log.warning(f"  [FAIL] {ip} refused ADB connection.")
            return None

    sweep = {
        f"{base}.{i}"
        for base in subnets
        for i in range(start_host, end_host + 1)
    }
    for ip in extra_ips or []:
        if ip and ip.strip():
            sweep.add(ip.strip())
    tasks = [check_ip(ip) for ip in sorted(sweep)]
    results = await asyncio.gather(*tasks)
    active_ips = sorted({ip for ip in results if ip})

    if active_ips:
        log.info(f"\nFound {len(active_ips)} active ADB device(s): {active_ips}")
    else:
        log.warning("\nNo ADB devices found on scanned subnets.")

    return active_ips
