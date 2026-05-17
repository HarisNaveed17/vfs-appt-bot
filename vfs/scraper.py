from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    date: str
    time: str | None
    center: str
    category: str


def parse_availability(
    data: dict,
    *,
    vac_code: str,
    visa_category: str,
) -> list[Slot]:
    """Parse a raw CheckIsSlotAvailable response dict into Slot objects.

    The response is captured from the browser XHR (vfs/auth.py) — VFS cannot
    be called directly over httpx (Cloudflare-blocked), so there is no fetch
    here, only parsing.
    """
    return _parse(data, vac_code=vac_code, visa_category=visa_category)


def _parse(data: dict, *, vac_code: str, visa_category: str) -> list[Slot]:
    slot_list = data.get("earliestSlotLists") or []

    if not slot_list:
        # If there's an earliest date but no slot list, surface it to avoid a silent miss.
        earliest = data.get("earliestDate")
        if earliest:
            return [Slot(date=str(earliest), time=None, center=vac_code, category=visa_category)]
        return []

    return [_map_slot(s, vac_code=vac_code, visa_category=visa_category) for s in slot_list]


def _map_slot(raw: dict, *, vac_code: str, visa_category: str) -> Slot:
    date = raw.get("date") or ""
    time_ = raw.get("slotTime") or raw.get("time")
    return Slot(
        date=str(date),
        time=str(time_) if time_ else None,
        center=raw.get("vacCode", vac_code),
        category=raw.get("visaCategoryCode", visa_category),
    )
