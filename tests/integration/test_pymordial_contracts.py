"""Integration tests verifying full Pymordial contract fulfillment across all devices and controller."""

import numpy as np
from pymordial.core.app import PymordialApp
from pymordial.core.blueprints.bridge_device import PymordialBridgeDevice
from pymordial.core.blueprints.ocr_device import PymordialOCRDevice
from pymordial.core.blueprints.vision_device import PymordialVisionDevice
from pymordial.core.controller import PymordialController
from pymordial.ui.pixel import PymordialPixel

from pymordialdroid import (
    AdbDevice,
    AndroidApp,
    AndroidController,
    AndroidUiDevice,
    ScrcpyDevice,
    TesseractDevice,
)


class TestPymordialContracts:
    def test_adb_device_satisfies_contract(self, mocker):
        """AdbDevice must be a subclass of PymordialBridgeDevice."""
        assert issubclass(AdbDevice, PymordialBridgeDevice)

        adb = AdbDevice(host="127.0.0.1", port=5555)
        assert adb.name == "adb"
        assert adb.version == "0.1.0"
        assert not adb.is_connected()

        # Test package search
        mocker.patch.object(
            adb,
            "run_command",
            return_value="package:com.android.settings\npackage:com.example.game\npackage:com.other.app",
        )
        assert adb.find_package_by_keyword("game") == "com.example.game"
        assert adb.find_package_by_keyword("settings") == "com.android.settings"
        assert adb.find_package_by_keyword("nonexistent") is None

        # Test launch activity resolution
        mocker.patch.object(
            adb,
            "run_command",
            return_value="priority=0 preferredOrder=0 match=0x108000 specific=false\ncom.example.game/.MainActivity",
        )
        assert (
            adb.get_launch_activity("com.example.game")
            == "com.example.game/.MainActivity"
        )

    def test_ui_device_satisfies_contract(self):
        """AndroidUiDevice must be a subclass of PymordialVisionDevice."""
        assert issubclass(AndroidUiDevice, PymordialVisionDevice)
        ui = AndroidUiDevice()
        assert ui.name == "ui"
        assert ui.version == "0.1.0"

    def test_tesseract_device_satisfies_contract(self):
        """TesseractDevice must be a subclass of PymordialOCRDevice."""
        assert issubclass(TesseractDevice, PymordialOCRDevice)
        ocr = TesseractDevice()
        assert ocr.name == "ocr"
        assert ocr.version == "0.1.0"

    def test_app_satisfies_contract(self):
        """AndroidApp must be a subclass of PymordialApp."""
        assert issubclass(AndroidApp, PymordialApp)
        app = AndroidApp(app_name="TestApp", package_name="com.test")
        assert app.app_name == "TestApp"
        assert app.package_name == "com.test"

    def test_controller_satisfies_contract(self):
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

    def test_pixel_color_check_integration(self):
        """AndroidUiDevice check_pixel_color verifies RGB values."""
        red_screen = np.zeros((10, 10, 3), dtype=np.uint8)
        red_screen[:, :] = (255, 0, 0)

        ui = AndroidUiDevice()
        pixel = PymordialPixel(
            label="red_dot",
            position=(5, 5),
            pixel_color=(255, 0, 0),
            tolerance=5,
        )
        assert ui.check_pixel_color(pixel, screenshot=red_screen) is True
