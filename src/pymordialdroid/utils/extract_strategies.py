"""OCR extraction and image preprocessing strategies for PymordialDroid."""

import logging
from typing import Any

import cv2
import numpy as np
from pymordial.core.blueprints.extract_strategy import PymordialExtractStrategy

log = logging.getLogger("pymordialdroid")


class DefaultExtractStrategy(PymordialExtractStrategy):
    """Generic image preprocessing strategy suitable for standard OCR tasks.

    Pipeline:
    1. 2x cubic upscaling to sharpen letterforms.
    2. Grayscale conversion.
    3. Fast Non-Local Means Denoising to filter noise artifacts.
    4. Otsu's thresholding for optimal binarization.
    5. Automatic polarity correction (inverts dark backgrounds to produce black text on white).
    """

    def __init__(
        self,
        upscale_factor: int = 2,
        denoise_strength: int = 5,
        tesseract_config: str = "--oem 3 --psm 6",
    ) -> None:
        self.upscale_factor = upscale_factor
        self.denoise_strength = denoise_strength
        self.config_str = tesseract_config

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """Applies grayscale, upscaling, denoising, and thresholding."""
        try:
            # 1. Upscale
            if self.upscale_factor > 1:
                image = cv2.resize(
                    image,
                    None,
                    fx=self.upscale_factor,
                    fy=self.upscale_factor,
                    interpolation=cv2.INTER_CUBIC,
                )

            # 2. Grayscale
            if len(image.shape) == 3 and image.shape[2] >= 3:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            else:
                gray = image

            # 3. Denoise
            denoised = cv2.fastNlMeansDenoising(
                gray,
                None,
                self.denoise_strength,
                templateWindowSize=7,
                searchWindowSize=21,
            )

            # 4. Otsu's threshold
            _, thresh = cv2.threshold(
                denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            # 5. Background inversion if dark
            if np.mean(thresh) < 127:
                thresh = cv2.bitwise_not(thresh)

            return thresh
        except Exception as e:
            log.warning(f"DefaultExtractStrategy preprocessing failed: {e}")
            return image

    def tesseract_config(self) -> str:
        """Returns the Tesseract command-line configuration arguments."""
        return self.config_str

    def postprocess_text(self, text: str) -> Any:
        """Trims whitespace from OCR extracted text."""
        return text.strip()
