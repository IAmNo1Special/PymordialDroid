"""First-run binary downloader for adb/scrcpy (issue #1).

Keeps adb and scrcpy out of git. On first use (or whenever
``resolve_system_config`` cannot find a binary), PymordialDroid downloads
pinned releases for the current platform into ``~/.pymordialdroid/bin/``
and verifies them with SHA-256 before use. Cache is per version, so a
pin bump is the only thing that triggers a re-download. Explicit
overrides (``adb_path``/``scrcpy_path`` in config.json, or the matching
constructor args) always take precedence and never trigger a download.

License caveat: scrcpy is GPLv2. This module *downloads* the binary at
runtime instead of bundling it; the distribution itself carries no
scrcpy code, and driving it over subprocess/socket is arms-length (mere
aggregation). If a distribution model needs certainty, the offline path
below prints exact manual-install instructions (zero license surface),
and counsel should confirm the interpretation before this is wired into
any installer or release automation.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from rich.progress import BarColumn, DownloadColumn, Progress, TransferSpeedColumn

from .config import DATA_DIR

BIN_DIR = DATA_DIR / "bin"
VERSIONS_FILE = BIN_DIR / "versions.json"

SCRCPY_VERSION = "4.1"
PLATFORM_TOOLS_VERSION = "36.0.0"


@dataclass(frozen=True)
class Package:
    """One downloadable archive that provides one or more binaries."""

    version: str
    url: str
    sha256: str
    kind: str  # "zip" or "tar.gz"


# Pinned per-platform packages. SHA-256 values verified against
# https://github.com/Genymobile/scrcpy/releases/download/v4.1/SHA256SUMS.txt
# (scrcpy) and locally hashed downloads of the platform-tools archives.
# Bump a version here to trigger a re-download for every user.
_PACKAGES: dict[str, dict[str, Package]] = {
    "win32": {
        # The scrcpy win64 zip ships adb.exe, AdbWinApi.dll and scrcpy.exe.
        "adb": Package(
            version=SCRCPY_VERSION,
            url="https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-win64-v4.1.zip",
            sha256="5b12172b3264b2889f4583ee64752ce832e29bc8b1089dca81093459697165db",
            kind="zip",
        ),
        "scrcpy": Package(
            version=SCRCPY_VERSION,
            url="https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-win64-v4.1.zip",
            sha256="5b12172b3264b2889f4583ee64752ce832e29bc8b1089dca81093459697165db",
            kind="zip",
        ),
    },
    "darwin-arm64": {
        "scrcpy": Package(
            version=SCRCPY_VERSION,
            url="https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-macos-aarch64-v4.1.tar.gz",
            sha256="20fd47c9014dd5e0fa77091f3cb7adbda8445a360c4584aeaa0150b5b3988ff3",
            kind="tar.gz",
        ),
        "adb": Package(
            version=PLATFORM_TOOLS_VERSION,
            url="https://dl.google.com/android/repository/platform-tools_r36.0.0-darwin.zip",
            sha256="d3e9fa1df3345cf728586908426615a60863d2632f73f1ce14f0f1349ef000fd",
            kind="zip",
        ),
    },
    "darwin-x86_64": {
        "scrcpy": Package(
            version=SCRCPY_VERSION,
            url="https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-macos-x86_64-v4.1.tar.gz",
            sha256="ee2a7223bc8dbdc4f482db1134bcf441178dafb833492b71ca4c22090c58ce72",
            kind="tar.gz",
        ),
        "adb": Package(
            version=PLATFORM_TOOLS_VERSION,
            url="https://dl.google.com/android/repository/platform-tools_r36.0.0-darwin.zip",
            sha256="d3e9fa1df3345cf728586908426615a60863d2632f73f1ce14f0f1349ef000fd",
            kind="zip",
        ),
    },
    "linux-x86_64": {
        "scrcpy": Package(
            version=SCRCPY_VERSION,
            url="https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-linux-x86_64-v4.1.tar.gz",
            sha256="ad56ae8bfeedf41e824945c11dbf55fcb092b3e615b9b486f48a50e30d389635",
            kind="tar.gz",
        ),
        "adb": Package(
            version=PLATFORM_TOOLS_VERSION,
            url="https://dl.google.com/android/repository/platform-tools_r36.0.0-linux.zip",
            sha256="0ead642c943ffe79701fccca8f5f1c69c4ce4f43df2eefee553f6ccb27cbfbe8",
            kind="zip",
        ),
    },
}


class BinaryFetchError(Exception):
    """Base error for first-run binary downloads."""


class UnsupportedPlatformError(BinaryFetchError):
    """Raised when no pinned package exists for this platform."""


class ChecksumError(BinaryFetchError):
    """Raised when a downloaded archive fails SHA-256 verification."""


class BinaryDownloadError(BinaryFetchError):
    """Raised when a binary cannot be downloaded.

    Carries manual-install instructions so the failure is loud and
    actionable instead of a bare network traceback.
    """

    def __init__(self, name: str, url: str, dest: Path, cause: Exception) -> None:
        self.name = name
        self.url = url
        self.dest = dest
        self.cause = cause
        super().__init__(manual_instructions(name, url, dest, cause))


def exe_name(name: str) -> str:
    """Platform executable filename for a binary name."""
    return f"{name}.exe" if sys.platform == "win32" else name


def platform_key() -> str:
    """Normalize this machine to one of the pinned package keys."""
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        if machine in ("x86_64", "amd64"):
            return "darwin-x86_64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    raise UnsupportedPlatformError(
        f"No pinned adb/scrcpy package for {sys.platform}/{platform.machine()}. "
        "Install platform-tools and scrcpy manually and point "
        '"adb_path"/"scrcpy_path" in ~/.pymordialdroid/config.json at them.'
    )


def manual_instructions(name: str, url: str, dest: Path, cause: Exception) -> str:
    """Human-readable fallback printed when a download fails."""
    return (
        f"Could not download {name} ({cause}).\n"
        "Manual install:\n"
        f"  1. Download: {url}\n"
        f"  2. Extract the {exe_name(name)} binary and place it at: {dest}\n"
        '  Or set "adb_path"/"scrcpy_path" in ~/.pymordialdroid/config.json '
        "to point at binaries you already have."
    )


def _is_current(name: str) -> bool:
    """True when the cached binary matches the pinned version."""
    key = platform_key()
    pinned = _PACKAGES[key][name].version
    if not VERSIONS_FILE.exists():
        return False
    try:
        recorded = json.loads(VERSIONS_FILE.read_text())
    except (OSError, ValueError):
        return False
    return recorded.get(f"{key}:{name}") == pinned


def _record_pin(name: str) -> None:
    key = platform_key()
    pinned = _PACKAGES[key][name].version
    recorded: dict[str, str] = {}
    if VERSIONS_FILE.exists():
        try:
            recorded = json.loads(VERSIONS_FILE.read_text())
        except (OSError, ValueError):
            recorded = {}
    recorded[f"{key}:{name}"] = pinned
    VERSIONS_FILE.write_text(json.dumps(recorded, indent=2))


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path, desc: str) -> None:
    """Stream a URL to disk with a rich progress bar."""
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as f:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
        ) as progress:
            task = progress.add_task(f"Downloading {desc}", total=total)
            while chunk := resp.read(65536):
                f.write(chunk)
                progress.update(task, advance=len(chunk))


def _extract(archive: Path, kind: str, into: Path) -> None:
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(into)
    elif kind == "tar.gz":
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(into, filter="data")
    else:  # pragma: no cover - table only contains zip/tar.gz
        raise BinaryFetchError(f"Unknown archive kind: {kind}")


def _find_exe(root: Path, exe: str) -> Path | None:
    """Locate the executable inside an extracted archive tree."""
    for candidate in root.rglob(exe):
        if candidate.is_file():
            return candidate
    return None


def _download_and_install(name: str, pkg: Package, dest: Path) -> None:
    exe = exe_name(name)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pymordialdroid-dl-") as tmp:
        tmpdir = Path(tmp)
        archive = tmpdir / f"{name}.{pkg.kind}"
        try:
            _download(pkg.url, archive, f"{name} {pkg.version}")
        except (urllib.error.URLError, OSError) as exc:
            raise BinaryDownloadError(name, pkg.url, dest, exc) from exc
        actual = _sha256_of(archive)
        if actual != pkg.sha256:
            raise ChecksumError(
                f"SHA-256 mismatch for {name} {pkg.version}: "
                f"expected {pkg.sha256}, got {actual}. "
                "The download may be corrupt or tampered with; refusing to install."
            )
        staged = tmpdir / "extracted"
        staged.mkdir()
        _extract(archive, pkg.kind, staged)
        found = _find_exe(staged, exe)
        if found is None:
            raise BinaryFetchError(
                f"Downloaded {name} {pkg.version} but found no {exe} inside the archive."
            )
        shutil.copy2(found, dest)
        if sys.platform != "win32":
            dest.chmod(dest.stat().st_mode | 0o111)
    _record_pin(name)


def is_binary_current(name: str) -> bool:
    """Public check used by config resolution before any download."""
    return _is_current(name)


def ensure_binary(name: str) -> Path:
    """Return a usable path for ``name`` (``"adb"`` or ``"scrcpy"``).

    Uses the cached download when it matches the pinned version, and
    downloads + verifies it on first run. Raises BinaryDownloadError
    (with manual-install instructions) when the download fails.
    """
    if name not in ("adb", "scrcpy"):
        raise ValueError(f"Unknown binary: {name!r}")
    key = platform_key()  # raises UnsupportedPlatformError early
    dest = BIN_DIR / exe_name(name)
    if dest.exists() and _is_current(name):
        return dest
    _download_and_install(name, _PACKAGES[key][name], dest)
    return dest
