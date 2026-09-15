"""Vision and UI element detection implementing Pymordial's PymordialVisionDevice contract."""

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from pymordial.core.blueprints.bridge_device import PymordialBridgeDevice
from pymordial.core.blueprints.ocr_device import PymordialOCRDevice
from pymordial.core.blueprints.vision_device import PymordialVisionDevice
from pymordial.ui.element import PymordialElement
from pymordial.ui.image import PymordialImage
from pymordial.ui.pixel import PymordialPixel
from pymordial.ui.text import PymordialText

from pymordialdroid.devices.tesseract_device import TesseractDevice
from pymordialdroid.utils.extract_strategies import PymordialExtractStrategy

log = logging.getLogger("pymordialdroid")


class AndroidUiDevice(PymordialVisionDevice):
    """Handles visual recognition tasks: template matching, pixel checks, and OCR."""

    name: str = "ui"
    version: str = "0.1.0"

    def __init__(
        self,
        bridge_device: PymordialBridgeDevice | None = None,
        ocr_device: PymordialOCRDevice | None = None,
    ) -> None:
        self.bridge_device = bridge_device
        self._ocr_device: PymordialOCRDevice = ocr_device or TesseractDevice()

    def initialize(self, config: Any = None) -> None:
        """Initializes the UI vision plugin."""
        pass

    def shutdown(self) -> None:
        """Performs cleanup."""
        pass

    def set_bridge_device(self, bridge_device: PymordialBridgeDevice) -> None:
        """Sets the underlying bridge device."""
        self.bridge_device = bridge_device

    def set_ocr_device(self, ocr_device: PymordialOCRDevice) -> None:
        """Sets the OCR device."""
        self._ocr_device = ocr_device

    def _ensure_screenshot(
        self, screenshot: bytes | np.ndarray | Image.Image | str | Path | None
    ) -> bytes | np.ndarray | None:
        """Returns the screenshot, capturing a fresh one via bridge_device if not provided."""
        if screenshot is not None:
            if isinstance(screenshot, (Path, str)):
                return cv2.imread(str(screenshot))
            if isinstance(screenshot, Image.Image):
                return np.array(screenshot)
            return screenshot

        if self.bridge_device:
            return self.bridge_device.capture_screenshot()

        return None

    def scale_img_to_screen(
        self,
        image_path: str | Path,
        screen_image: str | Image.Image | bytes | np.ndarray,
        ref_resolution: tuple[int, int] | None = None,
    ) -> Image.Image:
        """Scales a template image to match the device screen resolution.

        Args:
            image_path: Path to the reference image.
            screen_image: The current screen image.
            ref_resolution: Original resolution (width, height) the template was captured at.

        Returns:
            Scaled PIL Image.
        """
        if isinstance(screen_image, (bytes, bytearray)):
            screen_img = Image.open(BytesIO(screen_image))
        elif isinstance(screen_image, np.ndarray):
            screen_img = Image.fromarray(cv2.cvtColor(screen_image, cv2.COLOR_BGR2RGB))
        elif isinstance(screen_image, (str, Path)):
            screen_img = Image.open(str(screen_image))
        elif isinstance(screen_image, Image.Image):
            screen_img = screen_image
        else:
            raise ValueError(f"Unsupported screen image type: {type(screen_image)}")

        needle_img = Image.open(str(image_path))
        if not ref_resolution:
            return needle_img

        screen_w, screen_h = screen_img.size
        ref_w, ref_h = ref_resolution

        if ref_w <= 0 or ref_h <= 0:
            return needle_img

        ratio_w = screen_w / ref_w
        ratio_h = screen_h / ref_h

        scaled_size = (
            max(1, int(needle_img.size[0] * ratio_w)),
            max(1, int(needle_img.size[1] * ratio_h)),
        )
        return needle_img.resize(scaled_size, Image.Resampling.BICUBIC)

    def check_pixel_color(
        self,
        pymordial_pixel: PymordialPixel | None = None,
        pymordial_screenshot: bytes | np.ndarray | None = None,
        coords: tuple[int, int] | None = None,
        expected_color: tuple[int, int, int] | None = None,
        tolerance: int = 10,
        screenshot: bytes | np.ndarray | None = None,
    ) -> bool | None:
        """Checks if a pixel matches the target color within tolerance.

        Supports coordinate scaling when og_resolution is set on pymordial_pixel.
        """
        raw_screen = (
            pymordial_screenshot if pymordial_screenshot is not None else screenshot
        )
        screenshot_data = self._ensure_screenshot(raw_screen)
        if screenshot_data is None:
            return None

        # Resolve coordinates, expected color, and tolerance
        if pymordial_pixel:
            pos = getattr(pymordial_pixel, "position", None)
            if pos is None:
                return None
            target_coords = (int(pos[0]), int(pos[1]))
            exp_rgb = getattr(
                pymordial_pixel,
                "pixel_color",
                getattr(pymordial_pixel, "expected_color", None),
            )
            tol = getattr(pymordial_pixel, "tolerance", tolerance)
            og_res = getattr(pymordial_pixel, "og_resolution", None)
        elif coords and expected_color:
            target_coords = coords
            exp_rgb = expected_color
            tol = tolerance
            og_res = None
        else:
            return None

        if exp_rgb is None:
            return None

        # Convert screenshot to PIL for uniform coordinate and color lookup
        if isinstance(screenshot_data, (bytes, bytearray)):
            img = Image.open(BytesIO(screenshot_data))
        elif isinstance(screenshot_data, np.ndarray):
            img = Image.fromarray(screenshot_data)
        else:
            img = screenshot_data

        actual_w, actual_h = img.size

        # Scale coordinates if original resolution is specified
        if og_res:
            scale_x = actual_w / og_res[0]
            scale_y = actual_h / og_res[1]
            target_coords = (
                int(target_coords[0] * scale_x),
                int(target_coords[1] * scale_y),
            )

        x, y = target_coords
        if not (0 <= x < actual_w and 0 <= y < actual_h):
            return False

        pixel_color = img.getpixel((x, y))
        # Strip alpha channel if present
        actual_color = pixel_color[:3]
        target_rgb = exp_rgb[:3]

        return all(abs(a - t) <= tol for a, t in zip(actual_color, target_rgb))

    def where_element(
        self,
        element: PymordialElement,
        screenshot: bytes | np.ndarray | None = None,
        max_tries: int = 1,
        set_position: bool = False,
        set_size: bool = False,
        wait_time: float = 0.5,
        pymordial_screenshot: bytes | np.ndarray | None = None,
    ) -> tuple[int, int] | None:
        """Finds (center_x, center_y) coordinates of a UI element on screen."""
        target_screen = screenshot if screenshot is not None else pymordial_screenshot
        if isinstance(element, PymordialPixel):
            if self.check_pixel_color(
                pymordial_pixel=element, pymordial_screenshot=target_screen
            ):
                return (int(element.position[0]), int(element.position[1]))
            return None

        if isinstance(element, PymordialText):
            text = getattr(element, "element_text", getattr(element, "text", ""))
            strat = getattr(element, "extract_strategy", None)
            return self.find_text(text, pymordial_screenshot=screenshot, strategy=strat)

        if not isinstance(element, PymordialImage):
            # Bounding box center fallback
            return getattr(element, "center", None)

        # Handle PymordialImage
        raw_path = getattr(element, "filepath", getattr(element, "source_path", ""))
        template_path = Path(raw_path)
        if not template_path.exists():
            log.warning(f"Template image not found: {template_path}")
            return None

        og_res = getattr(element, "og_resolution", None)
        confidence = getattr(element, "confidence", 0.8)

        for attempt in range(max_tries):
            screen_data = self._ensure_screenshot(target_screen)
            if screen_data is None:
                if attempt < max_tries - 1:
                    time.sleep(wait_time)
                    continue
                return None

            try:
                # Convert screen data to PIL Image
                if isinstance(screen_data, (bytes, bytearray)):
                    haystack_pil = Image.open(BytesIO(screen_data))
                elif isinstance(screen_data, np.ndarray):
                    haystack_pil = Image.fromarray(screen_data)
                elif isinstance(screen_data, Image.Image):
                    haystack_pil = screen_data
                else:
                    haystack_pil = Image.open(str(screen_data))

                # Scale template needle to match current screen resolution
                scaled_needle_pil = self.scale_img_to_screen(
                    image_path=template_path,
                    screen_image=haystack_pil,
                    ref_resolution=og_res,
                )

                # Prepare OpenCV BGR images
                haystack_cv = cv2.cvtColor(np.array(haystack_pil), cv2.COLOR_RGB2BGR)
                needle_cv = cv2.cvtColor(np.array(scaled_needle_pil), cv2.COLOR_RGB2BGR)

                region = getattr(element, "region", None)
                if region:
                    rx, ry, rw, rh = region
                    haystack_cv = haystack_cv[ry : ry + rh, rx : rx + rw]
                    offset_x, offset_y = rx, ry
                else:
                    offset_x, offset_y = 0, 0

                # Template matching
                result = cv2.matchTemplate(haystack_cv, needle_cv, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val >= confidence:
                    match_x = max_loc[0] + offset_x
                    match_y = max_loc[1] + offset_y
                    needle_w, needle_h = scaled_needle_pil.size

                    center_coords = (
                        match_x + needle_w // 2,
                        match_y + needle_h // 2,
                    )

                    if set_position:
                        element.position = (match_x, match_y)
                    if set_size:
                        element.size = (needle_w, needle_h)

                    return center_coords

            except Exception as e:
                log.error(f"Error finding element {element.label}: {e}")

            if attempt < max_tries - 1:
                time.sleep(wait_time)
                screenshot = None  # Force fresh screenshot capture

        return None

    def where_elements(
        self,
        elements: list[PymordialElement],
        screenshot: bytes | np.ndarray | None = None,
        max_tries: int = 1,
        pymordial_screenshot: bytes | np.ndarray | None = None,
    ) -> tuple[int, int] | None:
        """Finds the coordinates of the first matching element from a list."""
        target_screen = screenshot if screenshot is not None else pymordial_screenshot
        for element in elements:
            coords = self.where_element(
                element, screenshot=target_screen, max_tries=max_tries
            )
            if coords is not None:
                return coords
        return None

    def find_text(
        self,
        text_to_find: str,
        pymordial_screenshot: Path | bytes | str | np.ndarray | None = None,
        strategy: PymordialExtractStrategy | None = None,
    ) -> tuple[int, int] | None:
        """Finds the center coordinates of specified text using the OCR device."""
        screenshot = self._ensure_screenshot(pymordial_screenshot)
        if screenshot is None:
            return None

        if hasattr(self._ocr_device, "find_text"):
            return self._ocr_device.find_text(
                text_to_find, screenshot, strategy=strategy
            )
        return None

    def check_text(
        self,
        text_to_find: str,
        pymordial_screenshot: Path | bytes | str | np.ndarray | None = None,
        case_sensitive: bool = False,
        strategy: PymordialExtractStrategy | None = None,
    ) -> bool:
        """Checks if text is visible on screen using the OCR device."""
        screenshot = self._ensure_screenshot(pymordial_screenshot)
        if screenshot is None:
            return False

        try:
            extracted = self._ocr_device.extract_text(screenshot, strategy=strategy)
            if case_sensitive:
                return text_to_find in extracted
            return text_to_find.lower() in extracted.lower()
        except Exception as e:
            log.error(f"Error checking text: {e}")
            return False

    def read_text(
        self,
        pymordial_screenshot: Path | bytes | str | np.ndarray | None = None,
        case_sensitive: bool = False,
        strategy: PymordialExtractStrategy | None = None,
    ) -> list[str]:
        """Reads text lines from the screen using the OCR device.

        Lines are always returned verbatim as recognized; ``case_sensitive``
        is accepted for signature compatibility but no longer mutates the
        output (case-insensitive *matching* belongs in :meth:`check_text`).
        """
        screenshot = self._ensure_screenshot(pymordial_screenshot)
        if screenshot is None:
            return []

        try:
            text = self._ocr_device.extract_text(screenshot, strategy=strategy)
            return [line.strip() for line in text.split("\n") if line.strip()]
        except Exception as e:
            log.error(f"Error reading text: {e}")
            return []


__all__ = [
    "AndroidUiDevice",
]
