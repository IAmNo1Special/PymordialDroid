"""Shared pytest fixtures for PymordialDroid tests."""

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pymordialdroid.models import DeviceRecord

# Every production module that falls back to resolve_system_config() when no
# explicit SystemConfig is passed. Unit tests must not depend on real
# adb/scrcpy binaries existing on disk.
_RESOLVER_MODULES = (
    "pymordialdroid.devices.adb_device",
    "pymordialdroid.devices.scrcpy_device",
    "pymordialdroid.devices.tesseract_device",
    "pymordialdroid.android_controller",
    "pymordialdroid.fleet",
)


@pytest.fixture(autouse=True)
def hermetic_system_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Make device construction hermetic: no real binaries required.

    SystemConfig validates adb/scrcpy paths with pydantic FilePath, so
    constructing any device without an explicit config raises on machines
    without the bundled binaries (Linux CI, fresh clones). This fixture
    creates placeholder binary files under tmp_path and patches the
    resolver in every module that imports it, so tests exercise device
    logic instead of the host's install state.

    Production validation in pymordialdroid/config.py is untouched: a real
    user with missing binaries still gets the clear ValidationError.
    """
    from pymordialdroid.config import SystemConfig

    adb_bin = tmp_path / "adb"
    adb_bin.touch()
    scrcpy_bin = tmp_path / "scrcpy"
    scrcpy_bin.touch()
    hermetic = SystemConfig(adb_bin_path=adb_bin, scrcpy_bin_path=scrcpy_bin)

    def _fake_resolve(*args, **kwargs) -> SystemConfig:
        return hermetic

    for module_name in _RESOLVER_MODULES:
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "resolve_system_config", _fake_resolve)

    return hermetic


@pytest.fixture
def sample_device_record() -> DeviceRecord:
    """Provides a sample DeviceRecord."""
    return DeviceRecord(
        serial="192.168.1.50:5555",
        ip="192.168.1.50",
        port=5555,
        name="TestGalaxyS24",
    )


@pytest.fixture
def mock_signer() -> MagicMock:
    """Provides a mocked PythonRSASigner."""
    signer = MagicMock()
    signer.public_key = b"test_public_key"
    return signer
