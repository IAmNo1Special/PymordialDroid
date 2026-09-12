"""Unit tests for configuration in pymordialdroid.config."""

from unittest.mock import patch

from pymordialdroid.config import SystemConfig, get_default_pin


def test_get_default_pin_fallback():
    """Test get_default_pin fallback when no config or env var is present."""
    with (
        patch("pymordialdroid.config.get_app_config", return_value={}),
        patch.dict("os.environ", {}, clear=True),
    ):
        assert get_default_pin() == "1234"


def test_get_default_pin_from_env():
    """Test get_default_pin from environment variable."""
    with (
        patch("pymordialdroid.config.get_app_config", return_value={}),
        patch.dict("os.environ", {"DEVICE_PIN": "987654"}),
    ):
        assert get_default_pin() == "987654"


def test_get_default_pin_from_config():
    """Test get_default_pin from config dict."""
    with patch(
        "pymordialdroid.config.get_app_config", return_value={"default_pin": "555555"}
    ):
        assert get_default_pin() == "555555"


def test_system_config_validation(tmp_path):
    """Test SystemConfig path validation."""
    fake_scrcpy = tmp_path / "scrcpy.exe"
    fake_scrcpy.touch()
    fake_adb = tmp_path / "adb.exe"
    fake_adb.touch()

    cfg = SystemConfig(scrcpy_bin_path=fake_scrcpy, adb_bin_path=fake_adb)
    assert cfg.scrcpy_bin_path == fake_scrcpy
    assert cfg.adb_bin_path == fake_adb
