"""Unit tests for the first-run binary downloader (issue #1).

All network access is mocked; the suite never touches the network.
"""

import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from pymordialdroid import binary_fetch
from pymordialdroid.binary_fetch import (
    BinaryDownloadError,
    ChecksumError,
    Package,
    UnsupportedPlatformError,
    _download_and_install,
    ensure_binary,
    exe_name,
)


@pytest.fixture()
def bin_dir(tmp_path, monkeypatch):
    """Point the downloader cache at a temp directory."""
    monkeypatch.setattr(binary_fetch, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(
        binary_fetch, "VERSIONS_FILE", tmp_path / "bin" / "versions.json"
    )
    return tmp_path / "bin"


def test_exe_name_matches_platform():
    import sys

    expected = "adb.exe" if sys.platform == "win32" else "adb"
    assert exe_name("adb") == expected


def test_ensure_binary_skips_download_when_present(bin_dir):
    exe = bin_dir / exe_name("adb")
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    with (
        patch.object(binary_fetch, "_is_current", return_value=True),
        patch.object(binary_fetch, "_download_and_install") as dl,
    ):
        assert ensure_binary("adb") == exe
        dl.assert_not_called()


def test_ensure_binary_unknown_name():
    with pytest.raises(ValueError):
        ensure_binary("not-a-binary")


def test_ensure_binary_unsupported_platform():
    with patch.object(
        binary_fetch, "platform_key", side_effect=UnsupportedPlatformError("nope")
    ):
        with pytest.raises(UnsupportedPlatformError):
            ensure_binary("adb")


def _write_garbage_download(url: str, dest: Path, desc: str) -> None:
    dest.write_bytes(b"not the real archive")


def test_checksum_mismatch_raises(bin_dir):
    pkg = Package(
        version="0.0-test",
        url="https://example.com/fake.zip",
        sha256="0" * 64,
        kind="zip",
    )
    dest = bin_dir / exe_name("scrcpy")
    with patch.object(binary_fetch, "_download", side_effect=_write_garbage_download):
        with pytest.raises(ChecksumError) as excinfo:
            _download_and_install("scrcpy", pkg, dest)
    assert "SHA-256 mismatch" in str(excinfo.value)
    assert not dest.exists()


def test_offline_error_carries_manual_instructions(bin_dir):
    def boom(url: str, dest: Path, desc: str) -> None:
        raise urllib.error.URLError("no route to host")

    with patch.object(binary_fetch, "_download", side_effect=boom):
        with pytest.raises(BinaryDownloadError) as excinfo:
            ensure_binary("scrcpy")
    message = str(excinfo.value)
    assert "Manual install" in message
    assert "github.com/Genymobile" in message
    assert "~/.pymordialdroid/config.json" in message


def test_config_override_skips_download(tmp_path):
    """Explicit paths never trigger the downloader."""
    from pymordialdroid.config import resolve_system_config

    fake_adb = tmp_path / "adb"
    fake_scrcpy = tmp_path / "scrcpy"
    fake_adb.touch()
    fake_scrcpy.touch()
    with patch.object(
        binary_fetch, "ensure_binary", side_effect=AssertionError("must not download")
    ):
        cfg = resolve_system_config(
            adb_override=fake_adb, scrcpy_override=fake_scrcpy
        )
    assert cfg.adb_bin_path == fake_adb
    assert cfg.scrcpy_bin_path == fake_scrcpy
