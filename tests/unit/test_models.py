"""Unit tests for data models in pymordialdroid.models."""

from pymordialdroid.models import DeviceRecord


def test_device_record_defaults_and_validation():
    """Test DeviceRecord instantiation and default values."""
    rec = DeviceRecord(serial="192.168.1.10:5555", ip="192.168.1.10")
    assert rec.serial == "192.168.1.10:5555"
    assert rec.ip == "192.168.1.10"
    assert rec.port == 5555
    assert rec.name == "Unknown"
    assert rec.pin is None

    custom = DeviceRecord(
        serial="custom_serial",
        ip="10.0.0.5",
        port=5556,
        name="Pixel 9",
        pin="123456",
    )
    assert custom.serial == "custom_serial"
    assert custom.port == 5556
    assert custom.name == "Pixel 9"
    assert custom.pin == "123456"
