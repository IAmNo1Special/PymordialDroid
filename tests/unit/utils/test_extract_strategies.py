"""Unit tests for DefaultExtractStrategy in pymordialdroid.utils.extract_strategies."""

import numpy as np
import pytest

from pymordialdroid.utils.extract_strategies import DefaultExtractStrategy


def test_default_extract_strategy_preprocessing():
    """Test DefaultExtractStrategy preprocessing pipeline."""
    strat = DefaultExtractStrategy(upscale_factor=2, denoise_strength=3)
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    # Add a white box
    img[10:30, 10:30] = 255

    processed = strat.preprocess(img)
    assert isinstance(processed, np.ndarray)
    # Scaled 2x
    assert processed.shape[0] == 100
    assert processed.shape[1] == 100
    assert strat.tesseract_config() == "--oem 3 --psm 6"


def test_default_extract_strategy_postprocess_text():
    """Test postprocessing cleans up whitespace."""
    strat = DefaultExtractStrategy()
    assert strat.postprocess_text("  hello world  \n") == "hello world"


def test_default_extract_strategy_caps_large_inputs():
    """Phone screenshots are downscaled before the 2x upscale (speed)."""
    strat = DefaultExtractStrategy()
    img = np.zeros((2340, 1080, 3), dtype=np.uint8)
    img[100:200, 100:400] = 255

    processed = strat.preprocess(img)
    assert processed.ndim == 2  # binary image
    # 2340 -> capped 1600, then 2x upscale -> longest edge 3200.
    assert max(processed.shape) == 3200
    assert max(processed.shape) < 2340 * 2  # strictly cheaper than before


def test_default_extract_strategy_nlmeans_opt_in():
    """Legacy Non-Local Means path still available for noisy sources."""
    strat = DefaultExtractStrategy(denoise="nlmeans", denoise_strength=3)
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    img[10:30, 10:30] = 255
    processed = strat.preprocess(img)
    assert processed.shape == (100, 100)


def test_default_extract_strategy_rejects_bad_denoise():
    """Invalid denoise method fails fast."""
    with pytest.raises(ValueError):
        DefaultExtractStrategy(denoise="median")
