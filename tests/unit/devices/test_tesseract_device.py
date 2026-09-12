"""Unit tests for TesseractDevice in pymordialdroid.devices."""

from unittest.mock import patch

import numpy as np
import pytest
from pymordial.core.blueprints.ocr_device import PymordialOCRDevice

from pymordialdroid.devices.tesseract_device import TesseractDevice
from pymordialdroid.utils.extract_strategies import DefaultExtractStrategy


def test_tesseract_device_satisfies_contract():
    """TesseractDevice must be a subclass of PymordialOCRDevice."""
    assert issubclass(TesseractDevice, PymordialOCRDevice)
    ocr = TesseractDevice()
    assert ocr.name == "ocr"
    assert ocr.version == "0.1.0"


def test_tesseract_device_init():
    """Test device initialization."""
    device = TesseractDevice()
    assert device.name == "ocr"
    assert device.version == "0.1.0"


def test_tesseract_device_load_image():
    """Test image loading from numpy array, bytes, and invalid input."""
    device = TesseractDevice()

    # 1. Numpy array
    arr = np.zeros((50, 50, 3), dtype=np.uint8)
    loaded = device._load_image(arr)
    assert np.array_equal(loaded, arr)

    # 2. Invalid path
    with pytest.raises(ValueError, match="Could not load image"):
        device._load_image("non_existent_image_12345.png")


def test_extract_text_mocked():
    """Test text extraction with mocked pytesseract."""
    device = TesseractDevice()
    img = np.zeros((100, 100, 3), dtype=np.uint8)

    with patch("pytesseract.image_to_string", return_value="  Detected Text  \n"):
        text = device.extract_text(img)
        assert text == "Detected Text"


def test_find_text_mocked():
    """Test finding text bounding box and center coordinate calculation."""
    device = TesseractDevice()
    img = np.zeros((100, 100, 3), dtype=np.uint8)

    mock_dict = {
        "text": ["", "Hello", "World"],
        "conf": [0, 95, 90],
        "left": [0, 20, 60],
        "top": [0, 30, 30],
        "width": [0, 20, 30],
        "height": [0, 10, 10],
    }

    # Custom strategy with upscale_factor=1 for simplicity
    strategy = DefaultExtractStrategy(upscale_factor=1)

    with patch("pytesseract.image_to_data", return_value=mock_dict):
        coords = device.find_text("world", img, strategy=strategy)
        # Center of (60, 30, 30, 10) is (60 + 15, 30 + 5) = (75, 35)
        assert coords == (75, 35)

        # Test text not found
        coords_not_found = device.find_text("NonExistent", img, strategy=strategy)
        assert coords_not_found is None
