from pathlib import Path

from vfs.scraper import Slot
from vfs.state import diff, load, save


def test_diff_returns_only_new_slots():
    previous = [Slot("2026-05-20", "10:00", "Islamabad", "Schengen")]
    current = [
        Slot("2026-05-20", "10:00", "Islamabad", "Schengen"),
        Slot("2026-05-21", "11:00", "Islamabad", "Schengen"),
    ]
    assert diff(current, previous) == [Slot("2026-05-21", "11:00", "Islamabad", "Schengen")]


def test_diff_empty_when_unchanged():
    slots = [Slot("2026-05-20", "10:00", "Islamabad", "Schengen")]
    assert diff(slots, slots) == []


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    slots = [Slot("2026-05-20", None, "Karachi", "Tourist")]
    save(slots, path)
    assert load(path) == slots


def test_load_missing_file_returns_empty(tmp_path: Path):
    assert load(tmp_path / "absent.json") == []
