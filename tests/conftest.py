"""Shared pytest fixtures for PymordialDroid tests."""

from unittest.mock import MagicMock

import pytest

from pymordialdroid.models import DeviceRecord


@pytest.fixture
def sample_device_record() -> DeviceRecord:
    """Provides a sample DeviceRecord."""
    return DeviceRecord(
        serial="192.168.1.50:5555",
        ip="192.168.1.50",
        port=5555,
        name="TestGalaxyS24",
    )


@pytest.fixture
def mock_signer() -> MagicMock:
    """Provides a mocked PythonRSASigner."""
    signer = MagicMock()
    signer.public_key = b"test_public_key"
    return signer
