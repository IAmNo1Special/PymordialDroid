"""Unit tests for AndroidController in pymordialdroid."""

from pymordial.core.controller import PymordialController

from pymordialdroid.android_app import AndroidApp
from pymordialdroid.android_controller import AndroidController
from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.scrcpy_device import ScrcpyDevice
from pymordialdroid.devices.tesseract_device import TesseractDevice
from pymordialdroid.devices.ui_device import AndroidUiDevice


def test_controller_satisfies_contract():
    """AndroidController must be a subclass of PymordialController."""
    assert issubclass(AndroidController, PymordialController)

    controller = AndroidController(
        ip="192.168.1.100",
        port=5555,
        device_name="TestDevice",
    )
    assert controller.ip == "192.168.1.100"
    assert controller.port == 5555
    assert controller.device_name == "TestDevice"
    assert isinstance(controller.bridge, AdbDevice)
    assert isinstance(controller.adb, AdbDevice)
    assert isinstance(controller.ui, AndroidUiDevice)
    assert isinstance(controller.scrcpy, ScrcpyDevice)
    assert isinstance(controller.ocr, TesseractDevice)


def test_controller_app_registration():
    """Registering an app with controller sets app.pymordial_controller."""
    app = AndroidApp(app_name="DemoApp", package_name="com.demo")
    controller = AndroidController()
    controller.add_app(app)

    assert "DemoApp" in controller.list_apps()
    assert app.pymordial_controller is controller


def test_controller_ocr_and_extended_delegations(mocker):
    """Test controller delegates gestures, commands, and exposes OCR."""
    controller = AndroidController()
    assert controller.ocr is not None
    assert controller.ocr.name == "ocr"

    mocker.patch.object(controller.bridge, "swipe", return_value=True)
    assert controller.swipe(10, 20, 30, 40) is True
    controller.bridge.swipe.assert_called_with(
        start_x=10, start_y=20, end_x=30, end_y=40, duration=300
    )

    mocker.patch.object(controller.bridge, "go_back")
    controller.go_back()
    controller.bridge.go_back.assert_called_once()
