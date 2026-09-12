"""Unit tests for DefaultExtractStrategy in pymordialdroid.utils.extract_strategies."""

import numpy as np

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
