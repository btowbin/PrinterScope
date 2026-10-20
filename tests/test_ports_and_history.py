import pytest

from cnc_microscope.models import PointsHistory, StagePoint
from cnc_microscope.motion import (PortInfo, detect_printer, printer_port_score, probe_marlin,
                                   rank_printer_ports)

CH340 = PortInfo("COM3", "USB-SERIAL CH340 (COM3)", 0x1A86, 0x7523)
BLUETOOTH = PortInfo("COM4", "Standard Serial over Bluetooth link (COM4)", None, None)
ARDUINO = PortInfo("COM5", "Arduino Uno (COM5)", 0x2341, 0x0043)
ODD = PortInfo("COM6", "Some serial adapter", None, None)


def test_ch340_ranks_first_and_bluetooth_is_excluded():
    ranked = rank_printer_ports([BLUETOOTH, ARDUINO, ODD, CH340])
    assert [p.device for p in ranked] == ["COM3", "COM5"]
    assert printer_port_score(BLUETOOTH) < 0


def test_unusual_adapters_are_probed_when_nothing_looks_like_a_printer():
    ranked = rank_printer_ports([BLUETOOTH, ODD])
    assert [p.device for p in ranked] == ["COM6"]
    assert rank_printer_ports([]) == []


def test_detect_returns_first_port_and_baud_that_answers():
    calls = []

    def probe(port, baud):
        calls.append((port, baud))
        return (port, baud) == ("COM5", 250000)

    found = detect_printer([ARDUINO, CH340, BLUETOOTH], probe=probe)
    assert found == ("COM5", 250000)
    # best candidate first, both baud rates tried before moving on, Bluetooth never touched
    assert calls == [("COM3", 115200), ("COM3", 250000), ("COM5", 115200), ("COM5", 250000)]


def test_detect_returns_none_when_nothing_answers():
    assert detect_printer([CH340], probe=lambda p, b: False) is None


class FakeSerial:
    def __init__(self, replies):
        self.replies, self.written, self.closed = list(replies), [], False

    def reset_input_buffer(self): pass
    def write(self, data): self.written.append(data)
    def flush(self): pass
    def readline(self): return self.replies.pop(0) if self.replies else b""
    def close(self): self.closed = True


def test_probe_recognises_marlin_firmware_reply():
    fake = FakeSerial([b"echo:Marlin 2.0.9\n", b"FIRMWARE_NAME:Marlin 2.0.9.3 (Ender)\n"])
    assert probe_marlin("COM3", 115200, open_serial=lambda p, b: fake, reset_wait_s=0)
    assert fake.written == [b"M115\n"] and fake.closed


def test_probe_rejects_other_devices_and_unopenable_ports():
    fake = FakeSerial([b"hello\n", b"\xff\xfe\n"])
    assert not probe_marlin("COM3", 115200, open_serial=lambda p, b: fake,
                            reset_wait_s=0, reply_timeout_s=0.05)
    assert fake.closed

    def busy(port, baud):
        raise OSError("Access is denied")
    assert not probe_marlin("COM3", 115200, open_serial=busy, reset_wait_s=0)


def points(n):
    return [StagePoint(f"Point {i}", i, i, i) for i in range(1, n + 1)]


def test_history_restores_snapshots_in_reverse_order():
    history, current = PointsHistory(), points(3)
    history.record("remove position", current)
    current = current[:2]
    history.record("clear all positions", current)
    current = []
    assert history.next_label() == "clear all positions"
    label, current = history.undo()
    assert label == "clear all positions" and len(current) == 2
    label, current = history.undo()
    assert label == "remove position" and len(current) == 3
    assert not history.can_undo()
    with pytest.raises(IndexError):
        history.undo()


def test_history_snapshots_are_independent_of_later_edits():
    history, current = PointsHistory(), points(2)
    history.record("update position", current)
    current[0].x = 99.0           # in-place edit after the snapshot
    current[1].name = "renamed"
    _label, restored = history.undo()
    assert restored[0].x == 1 and restored[1].name == "Point 2"


def test_history_is_capped():
    history = PointsHistory(limit=3)
    for i in range(10):
        history.record(f"edit {i}", points(1))
    labels = []
    while history.can_undo():
        labels.append(history.undo()[0])
    assert labels == ["edit 9", "edit 8", "edit 7"]
