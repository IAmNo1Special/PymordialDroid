"""Data models for PymordialDroid."""

from pydantic import BaseModel


class DeviceRecord(BaseModel):
    """Schema for a saved device in our JSON fleet inventory file."""

    serial: str
    ip: str
    port: int = 5555
    name: str = "Unknown"
    pin: str | None = None
