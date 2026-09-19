"""Unit tests for PymordialDroid CLI subcommands (add, remove, list)."""

import pytest

from pymordialdroid.cli import build_parser, cmd_add, cmd_feed, cmd_list, cmd_remove
from pymordialdroid.models import DeviceRecord


def test_build_parser_subcommands():
    """Verify parser recognizes subcommands and options."""
    parser = build_parser()

    # List
    args_list = parser.parse_args(["list"])
    assert args_list.command == "list"

    args_ls = parser.parse_args(["ls"])
    assert args_ls.command == "ls"

    # Add
    args_add = parser.parse_args(
        ["add", "192.168.1.100", "--name", "PhoneA", "--port", "5555", "--pin", "0000"]
    )
    assert args_add.command == "add"
    assert args_add.device == "192.168.1.100"
    assert args_add.name == "PhoneA"
    assert args_add.port == 5555
    assert args_add.pin == "0000"

    # Remove
    args_rm = parser.parse_args(["remove", "PhoneA"])
    assert args_rm.command == "remove"
    assert args_rm.identifier == "PhoneA"

    args_rm_alias = parser.parse_args(["rm", "192.168.1.100"])
    assert args_rm_alias.command == "rm"
    assert args_rm_alias.identifier == "192.168.1.100"


def test_cmd_add_and_list(mocker, tmp_path):
    """Test cmd_add adds to inventory and cmd_list displays it."""
    mock_fc = mocker.MagicMock()
    mock_fc.phones = []

    def fake_add(ip, port=5555, name=None, pin=None, serial=None):
        rec = DeviceRecord(
            serial=serial or f"{ip}:{port}",
            ip=ip,
            port=port,
            name=name or f"Device-{ip}",
            pin=pin,
        )
        fake_phone = mocker.MagicMock()
        fake_phone.record = rec
        mock_fc.phones.append(fake_phone)
        return rec

    mock_fc.add_device.side_effect = fake_add
    mock_fc.load_inventory.return_value = mock_fc.phones

    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    # Add device via cmd_add
    parser = build_parser()
    args_add = parser.parse_args(
        ["add", "192.168.1.50:5555", "--name", "TestDevice", "--pin", "9999"]
    )
    cmd_add(args_add)

    assert len(mock_fc.phones) == 1
    rec = mock_fc.phones[0].record
    assert rec.ip == "192.168.1.50"
    assert rec.port == 5555
    assert rec.name == "TestDevice"
    assert rec.pin == "9999"

    # List devices
    args_list = parser.parse_args(["list"])
    cmd_list(args_list)


def test_cmd_remove(mocker):
    """Test cmd_remove calls remove_device."""
    mock_fc = mocker.MagicMock()
    mock_fc.remove_device.return_value = True

    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    parser = build_parser()
    args_rm = parser.parse_args(["remove", "TestDevice"])
    cmd_remove(args_rm)

    mock_fc.remove_device.assert_called_once_with("TestDevice")


def test_cmd_remove_nonexistent(mocker):
    """Test cmd_remove exits with code 1 if device not found."""
    mock_fc = mocker.MagicMock()
    mock_fc.remove_device.return_value = False

    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    parser = build_parser()
    args_rm = parser.parse_args(["remove", "MissingDevice"])

    with pytest.raises(SystemExit) as exc_info:
        cmd_remove(args_rm)

    assert exc_info.value.code == 1


def _two_test_phones(mocker):  # type: ignore[no-untyped-def]
    """Builds two fake fleet phones (rank 0 and rank 1)."""
    phones = []
    for i, name in enumerate(("PhoneA", "PhoneB")):
        fake_phone = mocker.MagicMock()
        fake_phone.record = DeviceRecord(
            serial=f"10.0.0.{i + 1}:5555",
            ip=f"10.0.0.{i + 1}",
            port=5555,
            name=name,
        )
        phones.append(fake_phone)
    return phones


def test_cmd_list_rank_is_zero_based(mocker):
    """list Rank must match API/TUI 0-based ranks (mistarget regression)."""
    import re

    from rich.console import Console as RichConsole

    rec_console = RichConsole(record=True, width=120)
    mocker.patch("pymordialdroid.cli.Console", return_value=rec_console)

    mock_fc = mocker.MagicMock()
    mock_fc.phones = _two_test_phones(mocker)
    mock_fc.load_inventory.return_value = mock_fc.phones
    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    cmd_list(build_parser().parse_args(["list"]))
    text = rec_console.export_text()
    assert re.search(r"│\s*0\s*│.*PhoneA", text), text
    assert re.search(r"│\s*1\s*│.*PhoneB", text), text


def test_feed_parser_has_device_selector():
    """feed start/stop must accept --device (fleet-wide by default)."""
    parser = build_parser()
    args = parser.parse_args(["feed", "start"])
    assert args.device is None and args.backend == "live"
    args = parser.parse_args(["feed", "start", "--device", "PhoneB"])
    assert args.device == "PhoneB"
    args = parser.parse_args(["feed", "stop", "-d", "0"])
    assert args.action == "stop" and args.device == "0"


def test_cmd_feed_targets_single_device(mocker):
    """feed start --device must not start streams on other fleet members."""
    mock_fc = mocker.MagicMock()
    phones = _two_test_phones(mocker)
    mock_fc.load_inventory.return_value = phones
    mock_fc._find_phone_index.side_effect = lambda ident: {"PhoneB": 1}.get(ident)
    mock_fc.start_live_streams.return_value = 1
    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    args = build_parser().parse_args(["feed", "start", "--device", "PhoneB"])
    cmd_feed(args)

    mock_fc.start_live_streams.assert_called_once()
    _, kwargs = mock_fc.start_live_streams.call_args
    assert kwargs["targets"] == [phones[1]]


def test_cmd_feed_stop_targets_single_device(mocker):
    """feed stop --device must only stop that device's streams."""
    mock_fc = mocker.MagicMock()
    phones = _two_test_phones(mocker)
    mock_fc.load_inventory.return_value = phones
    mock_fc._find_phone_index.side_effect = lambda ident: {"0": 0}.get(ident)
    mocker.patch("pymordialdroid.cli.FleetCommander", return_value=mock_fc)

    args = build_parser().parse_args(["feed", "stop", "--device", "0"])
    cmd_feed(args)

    mock_fc.stop_live_streams.assert_called_once_with(targets=[phones[0]])
    mock_fc.stop_headless_feeds.assert_called_once_with(targets=[phones[0]])
