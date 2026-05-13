from __future__ import annotations

import json
from pathlib import Path

from vfs.scraper import Slot

DEFAULT_PATH = Path("state.json")


def load(path: Path = DEFAULT_PATH) -> list[Slot]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Slot(**s) for s in data.get("slots", [])]


def save(slots: list[Slot], path: Path = DEFAULT_PATH) -> None:
    payload = {
        "slots": [
            {"date": s.date, "time": s.time, "center": s.center, "category": s.category}
            for s in slots
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def diff(current: list[Slot], previous: list[Slot]) -> list[Slot]:
    previous_set = set(previous)
    return [s for s in current if s not in previous_set]
