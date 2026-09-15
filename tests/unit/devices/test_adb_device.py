"""Unit tests for AdbDevice in pymordialdroid.devices."""

from pymordial.core.blueprints.bridge_device import PymordialBridgeDevice

from pymordialdroid.devices.adb_device import AdbDevice


def test_adb_device_satisfies_contract():
    """AdbDevice must be a subclass of PymordialBridgeDevice."""
    assert issubclass(AdbDevice, PymordialBridgeDevice)

    adb = AdbDevice(host="127.0.0.1", port=5555)
    assert adb.name == "adb"
    assert adb.version == "0.1.0"
    assert not adb.is_connected()


def test_adb_device_find_package(mocker):
    """Test package search with keyword."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    mocker.patch.object(
        adb,
        "run_command",
        return_value="package:com.android.settings\npackage:com.example.game\npackage:com.other.app",
    )
    assert adb.find_package_by_keyword("game") == "com.example.game"
    assert adb.find_package_by_keyword("settings") == "com.android.settings"
    assert adb.find_package_by_keyword("nonexistent") is None


def test_adb_device_find_package_prefers_component_match(mocker):
    """Keyword equal to a dot-component must beat substring rivals.

    Regression: 'settings' resolved to com.sec.usbsettings (shortest
    substring) instead of com.android.settings on Samsung devices.
    """
    adb = AdbDevice(host="127.0.0.1", port=5555)
    mocker.patch.object(
        adb,
        "run_command",
        return_value=(
            "package:com.sec.usbsettings\n"
            "package:com.android.settings\n"
            "package:com.samsung.android.setting.multisound"
        ),
    )
    assert adb.find_package_by_keyword("settings") == "com.android.settings"
    # Exact match still wins over everything.
    assert adb.find_package_by_keyword("com.sec.usbsettings") == "com.sec.usbsettings"
    # Pure substring fallback keeps working.
    assert adb.find_package_by_keyword("multisound") == (
        "com.samsung.android.setting.multisound"
    )


def test_adb_device_find_package_ignores_garbage_lines(mocker):
    """Non-package lines (Samsung multi-user warnings) must not match."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    mocker.patch.object(
        adb,
        "run_command",
        return_value=(
            "Error: java.lang.SecurityException: Shell does not have permission\n"
            "package:com.android.settings\n"
            "some other noise\n"
            "package:com.example.game"
        ),
    )
    assert adb.find_package_by_keyword("settings") == "com.android.settings"
    assert adb.find_package_by_keyword("SecurityException") is None
    assert adb.find_package_by_keyword("noise") is None


def test_adb_device_close_all_apps_skips_garbage_lines(mocker):
    """close_all_apps must never force-stop a non-package line."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    stopped: list[str] = []

    def fake_run(cmd: str, decode: bool = True):  # type: ignore[no-untyped-def]
        if cmd == "pm list packages":
            return (
                "Error: java.lang.SecurityException: blah\n"
                "package:com.app.one\n"
                "package:com.keep.me"
            )
        stopped.append(cmd)
        return "OK"

    mocker.patch.object(adb, "run_command", side_effect=fake_run)
    assert adb.close_all_apps(exclude=["com.keep.me"]) == 1
    assert stopped == ["am force-stop com.app.one"]


def test_adb_device_get_launch_activity(mocker):
    """Test launch activity resolution."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    mocker.patch.object(
        adb,
        "run_command",
        return_value="priority=0 preferredOrder=0 match=0x108000 specific=false\ncom.example.game/.MainActivity",
    )
    assert (
        adb.get_launch_activity("com.example.game") == "com.example.game/.MainActivity"
    )


def test_adb_extended_gestures_and_escaping(mocker):
    """Test swipe, go_back, flexible tap, close_all_apps, and robust type_text escaping."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    run_mock = mocker.patch.object(adb, "run_command", return_value="OK")

    # 1. Flexible tap
    adb.tap((150, 250))
    run_mock.assert_called_with("input tap 150 250")

    adb.tap(300, 400)
    run_mock.assert_called_with("input tap 300 400")

    # 2. Swipe
    adb.swipe(100, 500, 100, 100, duration=400)
    run_mock.assert_called_with("input swipe 100 500 100 100 400")

    # 3. Go back
    adb.go_back()
    run_mock.assert_called_with("input keyevent 4")

    # 4. Type text escaping special characters
    adb.type_text("Hello World! & <test>; | 'quote'")
    call_arg = run_mock.call_args[0][0]
    assert "Hello%sWorld!" in call_arg
    assert "\\&" in call_arg
    assert "\\<" in call_arg
    assert "\\>" in call_arg
    assert "\\|" in call_arg
    assert "\\'" in call_arg

    # 5. close_all_apps
    mocker.patch.object(
        adb,
        "run_command",
        side_effect=[
            "package:com.app.one\npackage:com.app.two\npackage:com.keep.me",  # pm list packages
            "OK",  # am force-stop com.app.one
            "OK",  # am force-stop com.app.two
        ],
    )
    closed = adb.close_all_apps(exclude=["com.keep.me"])
    assert closed == 2


def test_adb_device_is_app_running(mocker):
    """Test is_app_running query."""
    adb = AdbDevice(host="127.0.0.1", port=5555)
    mocker.patch.object(adb, "run_command", return_value="1234\n")
    assert adb.is_app_running(package_name="com.test.app") is True

    mocker.patch.object(adb, "run_command", return_value="")
    assert adb.is_app_running(package_name="com.test.app", max_retries=1) is False
