"""Unit tests for AndroidUiDevice in pymordialdroid.devices."""

from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
from pymordial.core.blueprints.vision_device import PymordialVisionDevice
from pymordial.ui.image import PymordialImage
from pymordial.ui.pixel import PymordialPixel

from pymordialdroid.devices.ui_device import AndroidUiDevice


def test_ui_device_satisfies_contract():
    """AndroidUiDevice must be a subclass of PymordialVisionDevice."""
    assert issubclass(AndroidUiDevice, PymordialVisionDevice)
    ui = AndroidUiDevice()
    assert ui.name == "ui"
    assert ui.version == "0.1.0"


def test_ui_device_init():
    """Test initialization of AndroidUiDevice."""
    device = AndroidUiDevice()
    assert device.name == "ui"
    assert device.version == "0.1.0"
    assert device._ocr_device is not None


def test_ui_device_check_pixel_color():
    """Test pixel color matching with tolerance and resolution scaling."""
    device = AndroidUiDevice()

    # Image is 200x200 RGB
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    img[40, 20] = [255, 10, 10]  # Red-ish pixel at (x=20, y=40)

    # Pixel defined on 100x100 screen at (x=10, y=20) -> should scale 2x to (x=20, y=40)
    pixel = PymordialPixel(
        label="test_pixel",
        position=(10, 20),
        pixel_color=(255, 0, 0),
        tolerance=15,
        og_resolution=(100, 100),
    )

    assert device.check_pixel_color(pixel, pymordial_screenshot=img) is True

    # Out of tolerance check
    pixel_strict = PymordialPixel(
        label="test_pixel_strict",
        position=(10, 20),
        pixel_color=(0, 255, 0),
        tolerance=5,
        og_resolution=(100, 100),
    )
    assert device.check_pixel_color(pixel_strict, pymordial_screenshot=img) is False


def test_ui_device_scale_img_to_screen(tmp_path):
    """Test scaling reference image to match screen resolution."""
    device = AndroidUiDevice()

    # Create dummy needle image 50x50
    needle_path = tmp_path / "needle.png"
    needle_img = Image.new("RGB", (50, 50), color="blue")
    needle_img.save(needle_path)

    # Screen is 2000x1000, reference was 1000x500 (ratio 2.0)
    screen = Image.new("RGB", (2000, 1000), color="black")
    scaled = device.scale_img_to_screen(
        image_path=needle_path,
        screen_image=screen,
        ref_resolution=(1000, 500),
    )

    assert scaled.size == (100, 100)


def test_ui_device_where_element_image(tmp_path):
    """Test where_element using mocked template matching."""
    device = AndroidUiDevice()

    needle_path = tmp_path / "needle.png"
    needle_img = Image.new("RGB", (20, 20), color="white")
    needle_img.save(needle_path)

    element = PymordialImage(
        label="button",
        filepath=str(needle_path),
        confidence=0.85,
        og_resolution=(100, 100),
    )

    # Mock screen 100x100
    screen = np.zeros((100, 100, 3), dtype=np.uint8)

    # Mock cv2.minMaxLoc to return match at (x=40, y=50) with confidence 0.95
    with patch("cv2.minMaxLoc", return_value=(0.1, 0.95, (0, 0), (40, 50))):
        coords = device.where_element(
            element,
            screenshot=screen,
            set_position=True,
            set_size=True,
        )

        # Center of 20x20 at (40, 50) is (40 + 10, 50 + 10) = (50, 60)
        assert coords == (50, 60)
        assert element.position == (40, 50)
        assert element.size == (20, 20)


def test_ui_device_ocr_delegation():
    """Test OCR methods find_text, check_text, read_text on UI device."""
    mock_ocr = MagicMock()
    mock_ocr.find_text.return_value = (100, 200)
    mock_ocr.extract_text.return_value = "Line 1\nLine 2\n"

    device = AndroidUiDevice(ocr_device=mock_ocr)
    screen = np.zeros((50, 50, 3), dtype=np.uint8)

    # 1. find_text
    coords = device.find_text("Search", pymordial_screenshot=screen)
    assert coords == (100, 200)
    mock_ocr.find_text.assert_called_once()

    # 2. check_text
    assert (
        device.check_text("Line 1", pymordial_screenshot=screen, case_sensitive=True)
        is True
    )
    assert (
        device.check_text("Missing", pymordial_screenshot=screen, case_sensitive=False)
        is False
    )

    # 3. read_text
    lines = device.read_text(pymordial_screenshot=screen, case_sensitive=True)
    assert lines == ["Line 1", "Line 2"]


def test_ui_device_read_text_preserves_case():
    """read_text must return lines verbatim (no lowercasing).

    Regression: the default path lowercased recognized text, breaking
    callers that compare against original casing.
    """
    mock_ocr = MagicMock()
    mock_ocr.extract_text.return_value = "Hello Greensboro\nUPPER lower\n"

    device = AndroidUiDevice(ocr_device=mock_ocr)
    screen = np.zeros((50, 50, 3), dtype=np.uint8)

    assert device.read_text(pymordial_screenshot=screen) == [
        "Hello Greensboro",
        "UPPER lower",
    ]
    assert device.read_text(pymordial_screenshot=screen, case_sensitive=True) == [
        "Hello Greensboro",
        "UPPER lower",
    ]
