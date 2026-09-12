"""Configuration management, path resolution, and RSA signer setup for PymordialDroid."""

import json
import os
import sys
from pathlib import Path

from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from dotenv import load_dotenv
from pydantic import BaseModel, Field, FilePath

# Load environment variables from .env
load_dotenv()

DATA_DIR = Path.home() / ".pymordialdroid"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_app_config() -> dict:
    """Reads ~/.pymordialdroid/config.json if it exists."""
    config_file = DATA_DIR / "config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_default_pin() -> str:
    """Resolves PIN from config.json, env vars, or default."""
    cfg = get_app_config()
    if cfg.get("default_pin"):
        return str(cfg["default_pin"])
    return os.getenv("DEVICE_PIN") or os.getenv("PYMORDIALDROID_PIN", "1234")


class SystemConfig(BaseModel):
    """Validates system paths (ADB, Scrcpy, and Tesseract) and manages RSA signing."""

    scrcpy_bin_path: FilePath
    adb_bin_path: FilePath
    tesseract_bin_path: Path | None = None
    adb_key_path: Path = Field(
        default_factory=lambda: Path.home() / ".android" / "adbkey"
    )

    def get_signer(self) -> PythonRSASigner:
        """Loads or generates the RSA signer."""
        if not self.adb_key_path.exists():
            self.adb_key_path.parent.mkdir(parents=True, exist_ok=True)
            keygen(str(self.adb_key_path))

        priv = self.adb_key_path.read_text()
        try:
            pub = self.adb_key_path.with_suffix(".pub").read_text()
        except FileNotFoundError:
            pub = ""
        return PythonRSASigner(pub, priv)


def resolve_system_config(
    adb_override: Path | str | None = None,
    scrcpy_override: Path | str | None = None,
    tesseract_override: Path | str | None = None,
) -> SystemConfig:
    """Resolves ADB, Scrcpy, and Tesseract paths using overrides, config.json, or bundled binaries."""
    base_dir = Path(__file__).resolve().parent
    adb_exe = "adb.exe" if sys.platform == "win32" else "adb"
    scrcpy_exe = "scrcpy.exe" if sys.platform == "win32" else "scrcpy"
    tesseract_exe = "tesseract.exe" if sys.platform == "win32" else "tesseract"

    app_config = get_app_config()
    adb_path_val = adb_override or app_config.get("adb_path")
    scrcpy_path_val = scrcpy_override or app_config.get("scrcpy_path")
    tesseract_path_val = tesseract_override or app_config.get("tesseract_path")

    resolved_adb = (
        Path(adb_path_val) if adb_path_val else base_dir / "bin" / "scrcpy" / adb_exe
    )
    resolved_scrcpy = (
        Path(scrcpy_path_val)
        if scrcpy_path_val
        else base_dir / "bin" / "scrcpy" / scrcpy_exe
    )

    resolved_tesseract: Path | None = None
    if tesseract_path_val and Path(tesseract_path_val).exists():
        resolved_tesseract = Path(tesseract_path_val)
    else:
        bundled_tess = base_dir / "bin" / "tesseract" / tesseract_exe
        if bundled_tess.exists():
            resolved_tesseract = bundled_tess
        else:
            import shutil

            which_tess = shutil.which("tesseract")
            if which_tess:
                resolved_tesseract = Path(which_tess)

    # Configure pytesseract if resolved
    if resolved_tesseract:
        try:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = str(resolved_tesseract)
        except ImportError:
            pass

    return SystemConfig(
        adb_bin_path=resolved_adb,
        scrcpy_bin_path=resolved_scrcpy,
        tesseract_bin_path=resolved_tesseract,
    )
