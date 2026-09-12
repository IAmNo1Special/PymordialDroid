"""Tesseract OCR device implementing Pymordial's PymordialOCRDevice contract."""

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytesseract
from pymordial.core.blueprints.ocr_device import PymordialOCRDevice

from pymordialdroid.config import SystemConfig, resolve_system_config
from pymordialdroid.utils.extract_strategies import (
    DefaultExtractStrategy,
    PymordialExtractStrategy,
)

log = logging.getLogger("pymordialdroid")


class TesseractDevice(PymordialOCRDevice):
    """Optical Character Recognition device powered by Tesseract and OpenCV."""

    name: str = "ocr"
    version: str = "0.1.0"

    def __init__(
        self,
        config: str = "--oem 3 --psm 6",
        system_config: SystemConfig | None = None,
    ) -> None:
        self.config = config
        self.system_config = system_config or resolve_system_config()

        if self.system_config.tesseract_bin_path:
            pytesseract.pytesseract.tesseract_cmd = str(
                self.system_config.tesseract_bin_path
            )
            log.info(
                f"Using configured Tesseract: {self.system_config.tesseract_bin_path}"
            )

    def initialize(self, config: Any = None) -> None:
        """Initializes the OCR device plugin."""
        pass

    def shutdown(self) -> None:
        """Cleans up resources."""
        pass

    def _load_image(self, image_path: Path | bytes | str | np.ndarray) -> np.ndarray:
        """Loads and normalizes an image from various input types into an OpenCV BGR numpy array."""
        if isinstance(image_path, np.ndarray):
            return image_path

        if isinstance(image_path, (bytes, bytearray)):
            nparr = np.frombuffer(image_path, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        else:
            image = cv2.imread(str(image_path))

        if image is None:
            raise ValueError(f"Could not load image from {type(image_path)}")

        return image

    def extract_text(
        self,
        image_path: Path | bytes | str | np.ndarray,
        strategy: PymordialExtractStrategy | None = None,
    ) -> str:
        """Extracts text from an image with optional preprocessing.

        Args:
            image_path: Source image as Path, bytes, string, or numpy array.
            strategy: Optional extraction strategy (defaults to DefaultExtractStrategy).

        Returns:
            Extracted text string.
        """
        try:
            image = self._load_image(image_path)
            strat = strategy or DefaultExtractStrategy()
            processed = strat.preprocess(image)

            tess_config = (
                getattr(strat, "tesseract_config", lambda: self.config)() or self.config
            )
            text = pytesseract.image_to_string(processed, config=tess_config)

            postprocess = getattr(strat, "postprocess_text", lambda t: t.strip())
            return postprocess(text)
        except Exception as e:
            log.error(f"Error extracting text with Tesseract: {e}")
            raise ValueError(f"Failed to extract text: {e}") from e

    def find_text(
        self,
        search_text: str,
        image_path: Path | bytes | str | np.ndarray,
        strategy: PymordialExtractStrategy | None = None,
    ) -> tuple[int, int] | None:
        """Finds (center_x, center_y) coordinates of search_text in the source image.

        Args:
            search_text: Text keyword or substring to search for.
            image_path: Source image as Path, bytes, string, or numpy array.
            strategy: Optional preprocessing strategy.

        Returns:
            (center_x, center_y) coordinates if located, None otherwise.
        """
        try:
            image = self._load_image(image_path)
            strat = strategy or DefaultExtractStrategy()
            processed = strat.preprocess(image)

            tess_config = (
                getattr(strat, "tesseract_config", lambda: self.config)() or self.config
            )
            data = pytesseract.image_to_data(
                processed, config=tess_config, output_type=pytesseract.Output.DICT
            )

            search_lower = search_text.lower().strip()
            n_boxes = len(data.get("text", []))
            upscale = getattr(strat, "upscale_factor", 1) or 1

            for i in range(n_boxes):
                conf = int(data["conf"][i])
                if conf > 0:
                    box_text = data["text"][i].strip().lower()
                    if search_lower in box_text:
                        x, y, w, h = (
                            data["left"][i],
                            data["top"][i],
                            data["width"][i],
                            data["height"][i],
                        )
                        center_x = int((x + w // 2) / upscale)
                        center_y = int((y + h // 2) / upscale)
                        return (center_x, center_y)

            return None
        except Exception as e:
            log.error(f"Error finding text '{search_text}' with Tesseract: {e}")
            return None


__all__ = [
    "TesseractDevice",
]
