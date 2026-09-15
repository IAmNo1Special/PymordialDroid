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
    1. Grayscale conversion first (later steps run on 1 channel, not 3).
    2. Downscale inputs larger than ``max_dimension`` (phone screenshots are
       far bigger than Tesseract needs; this dominates runtime savings).
    3. 2x cubic upscaling to sharpen letterforms.
    4. Fast denoising (Gaussian blur by default; opt-in Non-Local Means).
    5. Otsu's thresholding for optimal binarization.
    6. Automatic polarity correction (inverts dark backgrounds to produce black text on white).

    The previous default ran Non-Local Means on a 2x-upscaled 1080p
    screenshot (~10MP), costing ~4-6s per frame. Defaults below land the
    same 1080p shot around ~1s total OCR time.
    """

    def __init__(
        self,
        upscale_factor: int = 2,
        denoise_strength: int = 5,
        tesseract_config: str = "--oem 3 --psm 6",
        max_dimension: int = 1600,
        denoise: str = "gaussian",
    ) -> None:
        self.upscale_factor = upscale_factor
        self.denoise_strength = denoise_strength
        self.config_str = tesseract_config
        self.max_dimension = max_dimension
        if denoise not in ("gaussian", "nlmeans"):
            raise ValueError("denoise must be 'gaussian' or 'nlmeans'")
        self.denoise = denoise

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        """Applies grayscale, downscale cap, upscaling, denoising, thresholding."""
        try:
            # 1. Grayscale first: everything downstream handles 1 channel.
            if len(image.shape) == 3 and image.shape[2] >= 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # 2. Downscale oversized inputs (INTER_AREA for decimation).
            height, width = image.shape[:2]
            longest = max(height, width)
            if self.max_dimension > 0 and longest > self.max_dimension:
                scale = self.max_dimension / longest
                image = cv2.resize(
                    image,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )

            # 3. Upscale
            if self.upscale_factor > 1:
                image = cv2.resize(
                    image,
                    None,
                    fx=self.upscale_factor,
                    fy=self.upscale_factor,
                    interpolation=cv2.INTER_CUBIC,
                )

            # 4. Denoise (Gaussian default; NL-means opt-in for noisy sources)
            if self.denoise == "nlmeans":
                gray = cv2.fastNlMeansDenoising(
                    image,
                    None,
                    self.denoise_strength,
                    templateWindowSize=7,
                    searchWindowSize=21,
                )
            else:
                strength = max(0, int(self.denoise_strength))
                ksize = strength | 1  # odd kernel; 0/1 -> light 3x3
                ksize = min(max(ksize, 3), 9)
                gray = cv2.GaussianBlur(image, (ksize, ksize), 0)

            # 5. Otsu's threshold
            _, thresh = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            # 6. Background inversion if dark
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
