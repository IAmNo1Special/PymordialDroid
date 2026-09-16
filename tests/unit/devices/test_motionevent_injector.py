"""Unit tests for the `input motionevent` backend (motionevent_injector.py).

Covers the capability probe (positive/negative/cached), exact DOWN/MOVE/UP
command strings, remembered UP coordinates, hold/drag command building,
single-pointer honesty (slot > 0), and the AdbDevice backend priority chain
(motionevent -> sendevent -> legacy `input swipe` fallback).
"""

from unittest.mock import MagicMock

import pytest

from pymordialdroid.devices.adb_device import AdbDevice
from pymordialdroid.devices.motionevent_injector import MotionEventInjector

PROBE_SUPPORTED = (
    "Error: Invalid arguments for command: motionevent\n"
    "Usage: input [<source>] <command> [<arg>...]\n"
    "The commands and default sources are:\n"
    "      motionevent <DOWN|UP|MOVE|CANCEL> <x> <y>\n"
)
PROBE_UNKNOWN = (
    "Error: Unknown command: motionevent\n"
    "Usage: input [<source>] <command> [<arg>...]\n"
)

GETEVENT_TOUCH = """\
add device 2: /dev/input/event5
  name:     "sec_touchscreen"
  events:
    EV_KEY (0x01): BTN_TOUCH (0x14a)
    EV_ABS (0x03):
      ABS_MT_SLOT           : value 0, min 0, max 9, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_X     : value 0, min 0, max 2339, fuzz 0, flat 0, resolution 0
      ABS_MT_POSITION_Y     : value 0, min 0, max 1079, fuzz 0, flat 0, resolution 0
      ABS_MT_TRACKING_ID    : value 0, min 0, max 65535, fuzz 0, flat 0, resolution 0
"""


def make_runner(script):
    """Fake run_command dispatching on command prefix; records calls."""
    calls = []

    def run(cmd, decode=True):
        calls.append(cmd)
        for prefix, resp in script.items():
            if cmd.startswith(prefix):
                return resp() if callable(resp) else resp
        return ""

    return run, calls


def _me_runner(script_extra=None, probe=PROBE_SUPPORTED):
    """Runner where `input motionevent` probes supported; injects succeed."""
    script = {
        "input motionevent 2>&1": probe,  # probe (exact, no args)
        "input motionevent": "",  # injections succeed silently
    }
    script.update(script_extra or {})
    return make_runner(script)


def _injector(script_extra=None, probe=PROBE_SUPPORTED):
    run, calls = _me_runner(script_extra, probe)
    return MotionEventInjector(run), calls


# --- capability probe ---


def test_probe_supported_by_invalid_arguments():
    inj, calls = _injector()
    assert inj.available is True
    assert calls == ["input motionevent 2>&1"]


def test_probe_unknown_command_is_unsupported():
    inj, _ = _injector(probe=PROBE_UNKNOWN)
    assert inj.available is False


def test_probe_empty_output_is_unsupported():
    inj, _ = _injector(probe="")
    assert inj.available is False


def test_probe_result_is_cached():
    inj, calls = _injector()
    assert inj.available is True
    assert inj.available is True
    assert calls.count("input motionevent 2>&1") == 1


def test_probe_failure_returns_false():
    def boom(cmd, decode=True):
        raise RuntimeError("transport down")

    inj = MotionEventInjector(boom)
    assert inj.available is False


# --- command building ---


def test_touch_down_emits_exact_command():
    inj, calls = _injector()
    assert inj.touch_down(280, 702) is True
    assert calls[-1] == "input motionevent DOWN 280 702 2>&1"


def test_touch_move_requires_down_first():
    inj, calls = _injector()
    assert inj.touch_move(280, 550) is False
    assert not any("MOVE" in c for c in calls)


def test_touch_up_remembers_last_position():
    inj, calls = _injector()
    assert inj.touch_down(280, 702) is True
    assert inj.touch_move(280, 550) is True
    assert inj.touch_up() is True
    ups = [c for c in calls if "motionevent UP" in c]
    assert ups == ["input motionevent UP 280 550 2>&1"]


def test_touch_up_without_down_still_sends_for_recovery():
    inj, calls = _injector()
    assert inj.touch_up() is True  # stuck-pointer recovery path
    assert calls[-1] == "input motionevent UP 0 0 2>&1"


def test_touch_cancel_emits_cancel_action():
    inj, calls = _injector()
    assert inj.touch_down(280, 702) is True
    assert inj.touch_cancel() is True
    assert calls[-1] == "input motionevent CANCEL 280 702 2>&1"


def test_touch_hold_chains_down_sleep_up():
    inj, calls = _injector()
    assert inj.touch_hold(280, 702, duration_ms=5000) is True
    assert calls[-1] == (
        "input motionevent DOWN 280 702 2>&1"
        " && sleep 5.000 && "
        "input motionevent UP 280 702 2>&1"
    )


def test_touch_hold_duration_formatting():
    inj, calls = _injector()
    assert inj.touch_hold(1, 2, duration_ms=1500) is True
    assert "sleep 1.500" in calls[-1]


def test_precise_drag_emits_interpolated_moves():
    inj, calls = _injector()
    assert inj.precise_drag(0, 0, 400, 0, steps=4, step_delay_ms=16) is True
    cmd = calls[-1]
    assert cmd.startswith("input motionevent DOWN 0 0 2>&1")
    assert cmd.endswith("input motionevent UP 400 0 2>&1")
    assert cmd.count("motionevent MOVE") == 4
    assert cmd.count("sleep 0.016") == 4
    # endpoints: first move at 25%, last move == end
    assert "input motionevent MOVE 100 0 2>&1" in cmd
    assert "input motionevent MOVE 400 0 2>&1" in cmd


def test_precise_drag_rejects_zero_steps():
    inj, _ = _injector()
    with pytest.raises(ValueError):
        inj.precise_drag(0, 0, 400, 0, steps=0)


def test_injection_error_output_counts_as_failure():
    inj, _ = _injector({"input motionevent": "Error: Failed to inject event."})
    assert inj.touch_down(280, 702) is False
    assert inj.active_slots == set()


def test_active_slots_tracks_pointer():
    inj, _ = _injector()
    assert inj.active_slots == set()
    inj.touch_down(280, 702)
    assert inj.active_slots == {0}
    inj.touch_up()
    assert inj.active_slots == set()


def test_held_touch_releases_on_exit():
    inj, calls = _injector()
    with inj.held_touch(280, 702):
        assert inj.active_slots == {0}
    ups = [c for c in calls if "motionevent UP" in c]
    assert len(ups) == 1
    assert inj.active_slots == set()


def test_held_touch_releases_on_exception():
    inj, calls = _injector()
    with pytest.raises(RuntimeError, match="boom"):
        with inj.held_touch(280, 702):
            raise RuntimeError("boom")
    assert any("motionevent UP" in c for c in calls)


# --- single-pointer honesty ---


@pytest.mark.parametrize("call", ["down", "move", "up", "cancel", "hold", "drag"])
def test_nonzero_slot_returns_false_without_injecting(call):
    inj, calls = _injector()
    inj.touch_down(280, 702)  # establish slot 0 pointer first
    n_before = len(calls)
    if call == "down":
        assert inj.touch_down(1, 1, slot=1) is False
    elif call == "move":
        assert inj.touch_move(1, 1, slot=1) is False
    elif call == "up":
        assert inj.touch_up(slot=1) is False
    elif call == "cancel":
        assert inj.touch_cancel(slot=1) is False
    elif call == "hold":
        assert inj.touch_hold(1, 1, duration_ms=100, slot=1) is False
    else:
        assert inj.precise_drag(0, 0, 1, 1, steps=2, slot=1) is False
    assert len(calls) == n_before  # no new injection commands issued


# --- AdbDevice backend priority: motionevent -> sendevent -> swipe ---


def _adb(script):
    adb = AdbDevice(
        host="127.0.0.1", port=5555, system_config=MagicMock(), signer=MagicMock()
    )
    run, calls = make_runner(script)
    adb.run_command = run  # type: ignore[method-assign]
    return adb, calls


def test_backend_prefers_motionevent_over_sendevent():
    adb, calls = _adb(
        {
            "input motionevent 2>&1": PROBE_SUPPORTED,
            "input motionevent": "",
            "getevent -p": GETEVENT_TOUCH,
            "sendevent": "",
        }
    )
    assert adb.touch_available is True
    assert adb.touch_down(280, 702) is True
    assert "input motionevent DOWN 280 702 2>&1" in calls
    # sendevent backend never probed: no getevent/sendevent traffic at all
    assert not any(c.startswith("getevent") or c.startswith("sendevent") for c in calls)


def test_backend_falls_back_to_sendevent_when_motionevent_absent():
    adb, calls = _adb(
        {
            # no motionevent keys: probe returns "" -> unsupported
            "getevent -p": GETEVENT_TOUCH,
            "sendevent": "",
        }
    )
    assert adb.touch_available is True
    assert adb.touch_down(1170, 540) is True
    assert any(c.startswith("input motionevent 2>&1") for c in calls)
    assert any(c.startswith("sendevent") for c in calls)


def test_backend_selection_is_cached():
    adb, calls = _adb(
        {
            "input motionevent 2>&1": PROBE_SUPPORTED,
            "input motionevent": "",
        }
    )
    assert adb.touch_down(1, 1) is True
    assert adb.touch_move(2, 2) is True
    assert calls.count("input motionevent 2>&1") == 1  # probe ran once


def test_no_backend_falls_back_to_swipe_for_hold_and_drag(mocker):
    adb, _ = _adb({})  # motionevent "" + no touchscreen -> nothing available
    assert adb.touch_available is False
    swipe_spy = mocker.patch.object(adb, "swipe", return_value=True)
    assert adb.touch_hold(280, 702, duration_ms=1500) is True
    swipe_spy.assert_called_once_with(280, 702, 280, 702, duration=1500)

    adb2, _ = _adb({})
    swipe_spy2 = mocker.patch.object(adb2, "swipe", return_value=True)
    assert adb2.precise_drag(0, 0, 400, 0, steps=4, step_delay_ms=16) is True
    swipe_spy2.assert_called_once_with(0, 0, 400, 0, duration=64)


def test_no_backend_bare_down_move_up_stay_honest():
    adb, _ = _adb({})
    assert adb.touch_available is False
    assert adb.touch_down(100, 100) is False
    assert adb.touch_move(100, 100) is False
    assert adb.touch_up() is False
    assert adb.touch_cancel() is False


def test_no_backend_held_touch_raises():
    adb, _ = _adb({})
    with pytest.raises(RuntimeError, match="no usable backend"):
        adb.held_touch(100, 100)


def test_motionevent_backend_rejects_nonzero_slot():
    adb, calls = _adb(
        {
            "input motionevent 2>&1": PROBE_SUPPORTED,
            "input motionevent": "",
        }
    )
    assert adb.touch_available is True
    assert adb.touch_down(1, 1, slot=1) is False
    assert not any("motionevent DOWN" in c for c in calls)


# --- AndroidController delegation ---


def test_controller_touch_cancel_delegation(mocker):
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

    mocker.patch.object(controller.bridge, "touch_cancel", return_value=True)
    assert controller.touch_cancel(slot=0) is True
    controller.bridge.touch_cancel.assert_called_once_with(slot=0)
