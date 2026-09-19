"""Unit tests for sendevent touch injection (touch_injector + AdbDevice wiring)."""

from unittest.mock import MagicMock

import pytest

from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.touch_injector import (
    TouchInjector,
    TouchMapper,
    detect_input_size,
    parse_touch_device,
)

GETEVENT_SAMPLE = """\
add device 1: /dev/input/event2
  name:     "gpio-keys"
  events:
    EV_KEY (0x01): KEY_POWER (0x74)
add device 2: /dev/input/event5
  name:     "sec_touchscreen"
  events:
    EV_SYN (0x00):
    EV_KEY (0x01): BTN_TOUCH (0x14a)
    EV_ABS (0x03):
      ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
      ABS_MT_TOUCH_MAJOR    : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_X     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
      ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
add device 3: /dev/input/event7
  name:     "stm_ts"
  events:
    EV_ABS (0x03):
      ABS_MT_SLOT           : value 0, min 0, max 4, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_X     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0
      ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
"""

GETEVENT_NO_TOUCH = """\
add device 1: /dev/input/event2
  name:     "gpio-keys"
  events:
    EV_KEY (0x01): KEY_POWER (0x74)
"""

GETEVENT_HEX_SAMPLE = """\
add device 4: /dev/input/event8
  name:     "sec_touchscreen"
  events:
    KEY (0001): 008f  0118  0119  0145  014a  0226  02be
    ABS (0003): 0000  : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                0001  : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                002f  : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                0030  : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                0031  : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                0035  : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                0036  : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                0039  : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
"""

GETEVENT_LABEL_SAMPLE = """\
add device 1: /dev/input/event8
  name:     "sec_touchscreen"
  events:
    KEY (0001): KEY_WAKEUP            0118                  0119                  BTN_TOOL_FINGER
                BTN_TOUCH             0226                  02be
    ABS (0003): ABS_X                 : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_Y                 : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
                ABS_MT_TOUCH_MAJOR    : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                ABS_MT_TOUCH_MINOR    : value 0, min 0, max 255, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_X     : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_POSITION_Y     : value 0, min 0, max 4095, fuzz 0, flat 0, resolution 0
                ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
  input props:
    INPUT_PROP_DIRECT
"""


def make_runner(script):
    """Builds a fake run_command dispatching on command prefix; records calls."""
    calls = []

    def run(cmd, decode=True):
        calls.append(cmd)
        for prefix, resp in script.items():
            if cmd.startswith(prefix):
                return resp() if callable(resp) else resp
        return ""

    return run, calls


def working_runner(calls_out=None):
    """Fake runner where getevent + sendevent probe succeed (landscape device)."""
    script = {
        "getevent -p": GETEVENT_SAMPLE,
        "sendevent": "",  # probe + events succeed silently
    }
    run, calls = make_runner(script)
    if calls_out is not None:
        orig = run

        def wrapped(cmd, decode=True):
            out = orig(cmd, decode=decode)
            calls_out.append(cmd)
            return out

        return wrapped
    return run


# --- parsing ---


def test_parse_touch_device_prefers_touchscreen_name():
    info = parse_touch_device(GETEVENT_SAMPLE)
    assert info is not None
    assert info.node == "/dev/input/event5"
    assert info.name == "sec_touchscreen"
    assert (info.x_min, info.x_max) == (0, 2339)
    assert (info.y_min, info.y_max) == (0, 1079)


def test_parse_touch_device_returns_none_without_mt():
    assert parse_touch_device(GETEVENT_NO_TOUCH) is None
    assert parse_touch_device("") is None


def test_parse_touch_device_real_world_format_has_no_hex_on_axis_lines():
    # Regression: real `getevent -p` never prints (0x..) on the ABS_MT_* axis
    # lines (only on the EV_* headers). The parser must not require them.
    sample = """\
add device 4: /dev/input/event9
  name:     "sec_touchscreen"
  events:
    EV_ABS (0x03):
      ABS_MT_SLOT           : value 0, min 0, max 15, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_X     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 2399, fuzz 0, flat 0, resolution 0
      ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
"""
    assert "(0x2f)" not in sample  # the fixture really has no hex codes
    info = parse_touch_device(sample)
    assert info is not None
    assert info.node == "/dev/input/event9"
    assert (info.x_min, info.x_max) == (0, 1079)
    assert (info.y_min, info.y_max) == (0, 2399)


def test_parse_touch_device_hex_sample():
    info = parse_touch_device(GETEVENT_HEX_SAMPLE)
    assert info is not None
    assert info.node == "/dev/input/event8"
    assert info.name == "sec_touchscreen"
    assert (info.x_min, info.x_max) == (0, 4095)
    assert (info.y_min, info.y_max) == (0, 4095)


def test_parse_touch_device_label_sample():
    info = parse_touch_device(GETEVENT_LABEL_SAMPLE)
    assert info is not None
    assert info.node == "/dev/input/event8"
    assert info.name == "sec_touchscreen"
    assert (info.x_min, info.x_max) == (0, 4095)
    assert (info.y_min, info.y_max) == (0, 4095)


# --- mapping ---


def test_mapper_scales_input_space_to_device_range():
    info = parse_touch_device(GETEVENT_SAMPLE)
    mapper = TouchMapper(info, input_width=2340, input_height=1080)
    assert mapper.swap_axes is False
    assert mapper.to_device(1170, 540) == (1170, 540)
    assert mapper.to_device(0, 0) == (0, 0)
    assert mapper.to_device(2340, 1080) == (2339, 1079)


def test_mapper_auto_swaps_inverted_aspects():
    portrait_sample = GETEVENT_SAMPLE.replace(
        "ABS_MT_POSITION_X     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0",
        "ABS_MT_POSITION_X     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0",
    ).replace(
        "ABS_MT_POSITION_Y     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0",
        "ABS_MT_POSITION_Y     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0",
    )
    info = parse_touch_device(portrait_sample)
    mapper = TouchMapper(info, input_width=2340, input_height=1080)
    assert mapper.swap_axes is True
    # landscape (2340, 0) -> swapped axes -> device (0, 2339)
    assert mapper.to_device(2340, 0) == (0, 2339)


def test_mapper_clamps_out_of_range():
    info = parse_touch_device(GETEVENT_SAMPLE)
    mapper = TouchMapper(info, input_width=2340, input_height=1080)
    assert mapper.to_device(-50, 9999) == (0, 1079)


# --- injector sequences ---


def test_touch_down_emits_slot_protocol_sequence():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    assert injector.available is True
    assert injector.touch_down(1170, 540, slot=0) is True

    down_cmd = calls[-1]
    parts = down_cmd.split(" && ")
    assert parts == [
        "sendevent /dev/input/event5 3 47 0",  # ABS_MT_SLOT
        "sendevent /dev/input/event5 3 57 1",  # ABS_MT_TRACKING_ID = 1
        "sendevent /dev/input/event5 3 53 1170",  # ABS_MT_POSITION_X
        "sendevent /dev/input/event5 3 54 540",  # ABS_MT_POSITION_Y
        "sendevent /dev/input/event5 1 330 1",  # BTN_TOUCH down
        "sendevent /dev/input/event5 0 0 0",  # SYN_REPORT
    ]


def test_touch_move_requires_active_slot():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    assert injector.touch_move(100, 100, slot=0) is False  # never went down
    injector.touch_down(100, 100, slot=0)
    assert injector.touch_move(200, 200, slot=0) is True
    move_cmd = calls[-1]
    assert move_cmd.split(" && ") == [
        "sendevent /dev/input/event5 3 47 0",
        "sendevent /dev/input/event5 3 53 200",
        "sendevent /dev/input/event5 3 54 200",
        "sendevent /dev/input/event5 0 0 0",
    ]


def test_btn_touch_released_only_when_last_slot_lifts():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    injector.touch_down(100, 100, slot=0)
    injector.touch_down(200, 200, slot=1)
    assert injector.active_slots == {0, 1}

    injector.touch_up(slot=0)
    up0 = calls[-1]
    assert "1 330 0" not in up0  # slot 1 still down: keep BTN_TOUCH

    injector.touch_up(slot=1)
    up1 = calls[-1]
    assert "sendevent /dev/input/event5 1 330 0" in up1  # last lift releases
    assert injector.active_slots == set()


def test_tracking_ids_increment_per_down():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    injector.touch_down(10, 10, slot=0)
    injector.touch_up(slot=0)
    injector.touch_down(20, 20, slot=0)
    second_down = calls[-1]
    assert "sendevent /dev/input/event5 3 57 2" in second_down


def test_touch_hold_is_single_command_with_sleep():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    assert injector.touch_hold(280, 702, duration_ms=5000, slot=0) is True
    cmd = calls[-1]
    assert "sleep 5.000" in cmd
    # down ... sleep ... up, all in one shell invocation
    assert cmd.index("3 57 1") < cmd.index("sleep 5.000") < cmd.rindex("0 0 0")
    assert "3 57 -1" in cmd  # lift at the end


def test_precise_drag_interpolates_steps():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    assert (
        injector.precise_drag(0, 0, 400, 0, steps=4, step_delay_ms=16, slot=0) is True
    )
    cmd = calls[-1]
    # down, then 4x (sleep + move), then up — one shell invocation
    assert cmd.count("sleep 0.016") == 4
    # interpolated x positions land at 100/200/300/400 in device units
    for expected_x in (100, 200, 300, 400):
        assert f"sendevent /dev/input/event5 3 53 {expected_x}" in cmd
    assert "sendevent /dev/input/event5 3 57 -1" in cmd  # lifted at end


def test_precise_drag_rejects_zero_steps():
    injector = TouchInjector(working_runner(), input_width=2340, input_height=1080)
    with pytest.raises(ValueError):
        injector.precise_drag(0, 0, 10, 10, steps=0)


def test_held_touch_context_manager_releases_on_exit():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    with injector.held_touch(280, 702, slot=0) as held:
        assert held.active_slots == {0}
        held.touch_move(280, 600, slot=0)
    assert injector.active_slots == set()
    assert "3 57 -1" in calls[-1]  # up emitted on exit


def test_held_touch_releases_on_exception():
    calls = []
    injector = TouchInjector(working_runner(calls), input_width=2340, input_height=1080)
    with pytest.raises(RuntimeError, match="boom"):
        with injector.held_touch(280, 702, slot=0):
            raise RuntimeError("boom")
    assert injector.active_slots == set()
    assert "3 57 -1" in calls[-1]


def test_held_touch_raises_when_injection_unavailable():
    run, _ = make_runner(
        {
            "getevent -p": GETEVENT_SAMPLE,
            "sendevent": "sendevent: /dev/input/event5: Permission denied",
        }
    )
    injector = TouchInjector(run, input_width=2340, input_height=1080)
    assert injector.available is False
    with pytest.raises(RuntimeError, match="touch injection unavailable"):
        with injector.held_touch(1, 1):
            pass


# --- input size detection ---


def test_detect_input_size_prefers_dumpsys_app_size():
    run, _ = make_runner(
        {
            "dumpsys": 'mBaseDisplayInfo=DisplayInfo{"Built-in Screen", app 2340 x 1080, real 1080 x 2340}',
            "wm size": "Physical size: 1080x2340",
        }
    )
    assert detect_input_size(run) == (2340, 1080)


def test_detect_input_size_falls_back_to_wm_size():
    run, _ = make_runner(
        {
            "dumpsys": "no app size here",
            "wm size": "Physical size: 1080x2340",
        }
    )
    assert detect_input_size(run) == (1080, 2340)


def test_detect_input_size_returns_none_when_blind():
    run, _ = make_runner({})
    # default fake returns "" for everything
    assert detect_input_size(run) is None


# --- AdbDevice wiring + fallbacks ---


def _denied_adb():
    # system_config is mocked: this repo's bundled binaries are absent in CI,
    # and SystemConfig validation requires real paths (pre-existing suite
    # failures construct devices without this mock).
    adb = AdbDevice(
        host="127.0.0.1", port=5555, system_config=MagicMock(), signer=MagicMock()
    )
    script = {
        "getevent -p": GETEVENT_SAMPLE,
        "sendevent": "sendevent: /dev/input/event5: Permission denied",
    }
    run, _ = make_runner(script)
    adb.run_command = run  # type: ignore[method-assign]
    return adb


def test_adb_precise_drag_falls_back_to_swipe(mocker):
    adb = _denied_adb()
    assert adb.touch_available is False
    swipe_spy = mocker.patch.object(adb, "swipe", return_value=True)
    assert adb.precise_drag(0, 0, 400, 0, steps=4, step_delay_ms=16) is True
    swipe_spy.assert_called_once_with(0, 0, 400, 0, duration=64)


def test_adb_touch_hold_falls_back_to_long_press(mocker):
    adb = _denied_adb()
    swipe_spy = mocker.patch.object(adb, "swipe", return_value=True)
    assert adb.touch_hold(280, 702, duration_ms=1500) is True
    swipe_spy.assert_called_once_with(280, 702, 280, 702, duration=1500)


def test_adb_touch_down_returns_false_when_unavailable():
    adb = _denied_adb()
    assert adb.touch_down(100, 100) is False


def test_adb_touch_injector_uses_explicit_input_size(mocker):
    adb = AdbDevice(
        host="127.0.0.1",
        port=5555,
        system_config=MagicMock(),
        signer=MagicMock(),
        touch_input_width=2340,
        touch_input_height=1080,
    )
    run, calls = make_runner(
        {
            "getevent -p": GETEVENT_SAMPLE,
            "sendevent": "",
        }
    )
    adb.run_command = run  # type: ignore[method-assign]
    assert adb.touch_down(1170, 540) is True
    # explicit size used: no dumpsys/wm size detection commands issued
    assert not any(c.startswith("dumpsys") or c.startswith("wm size") for c in calls)
    mapper = adb._touch()._mapper
    assert (mapper.input_width, mapper.input_height) == (2340, 1080)


# --- AndroidController delegation ---


def test_controller_touch_delegation(mocker):
    from unittest.mock import PropertyMock

    from pymordialdroid.android_controller import AndroidController

    mocker.patch(
        "pymordialdroid.android_controller.resolve_system_config",
        return_value=MagicMock(),
    )
    mocker.patch.object(
        AdbDevice, "touch_available", new_callable=PropertyMock, return_value=True
    )
    controller = AndroidController()

    assert controller.touch_available is True

    mocker.patch.object(controller.bridge, "touch_down", return_value=True)
    assert controller.touch_down(100, 200, slot=1) is True
    controller.bridge.touch_down.assert_called_once_with(100, 200, slot=1)

    mocker.patch.object(controller.bridge, "touch_move", return_value=True)
    assert controller.touch_move(150, 250, slot=1) is True
    controller.bridge.touch_move.assert_called_once_with(150, 250, slot=1)

    mocker.patch.object(controller.bridge, "touch_up", return_value=True)
    assert controller.touch_up(slot=1) is True
    controller.bridge.touch_up.assert_called_once_with(slot=1)

    mocker.patch.object(controller.bridge, "touch_hold", return_value=True)
    assert controller.touch_hold(280, 702, 1500, slot=0) is True
    controller.bridge.touch_hold.assert_called_once_with(280, 702, 1500, slot=0)

    mocker.patch.object(controller.bridge, "precise_drag", return_value=True)
    assert controller.precise_drag(0, 0, 200, 0, steps=10) is True
    controller.bridge.precise_drag.assert_called_once_with(
        0, 0, 200, 0, steps=10, step_delay_ms=16, slot=0
    )

    sentinel = object()
    mocker.patch.object(controller.bridge, "held_touch", return_value=sentinel)
    assert controller.held_touch(280, 702) is sentinel
