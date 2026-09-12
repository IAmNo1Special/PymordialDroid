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
                name = serial
                if prompt_for_names:
                    try:
                        user_name = input(f"  Name for {serial}: ").strip()
                        if user_name:
                            name = user_name
                    except (EOFError, KeyboardInterrupt):
                        pass
                rec = DeviceRecord(serial=serial, ip=ip, name=name)
                discovered.append(rec)
        except Exception as e:
            log.error(f"Error retrieving IP for {serial}: {e}")

    return discovered


async def scan_hotspot_devices(
    signer: PythonRSASigner,
    base_ip: str = "192.168.137",
    start_host: int = 2,
    end_host: int = 25,
) -> list[str]:
    """Scans the local hotspot subnet concurrently to find active Android ADB devices.

    Args:
        signer: RSA key signer for testing ADB connection.
        base_ip: Subnet prefix, default Windows Mobile Hotspot "192.168.137".
        start_host: First host IP octet to ping.
        end_host: Last host IP octet to ping.

    Returns:
        List of responding IP addresses.
    """
    log.info(
        f"--- Scanning Hotspot {base_ip}.{start_host}-{end_host} for Phones (Parallel) ---"
    )

    async def check_ip(ip: str) -> str | None:
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

    tasks = [check_ip(f"{base_ip}.{i}") for i in range(start_host, end_host + 1)]
    results = await asyncio.gather(*tasks)
    active_ips = [ip for ip in results if ip]

    if active_ips:
        log.info(f"\nFound {len(active_ips)} active ADB devices on hotspot!")
    else:
        log.warning("\nNo new ADB devices found on hotspot.")

    return active_ips
