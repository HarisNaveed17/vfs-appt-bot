from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Slot:
    date: str
    time: str | None
    center: str
    category: str


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

ENDPOINT = "https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable"


class BlockedError(RuntimeError):
    """Raised when VFS returns 429/403 or a Cloudflare challenge page."""


def fetch_availability(
    *,
    country_code: str,
    mission_code: str,
    vac_code: str,
    visa_category: str,
    email: str,
    authorize: str,
    client_source: str | None = None,
) -> list[Slot]:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "authorize": authorize,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://visa.vfsglobal.com",
        "referer": "https://visa.vfsglobal.com/",
        "route": f"{country_code}/en/{mission_code}",
        "user-agent": USER_AGENT,
    }
    if client_source:
        headers["clientsource"] = client_source

    payload = {
        "countryCode": country_code,
        "missionCode": mission_code,
        "vacCode": vac_code,
        "visaCategoryCode": visa_category,
        "roleName": "Individual",
        "loginUser": email,
        "payCode": "",
    }

    with httpx.Client(timeout=20.0) as client:
        response = client.post(ENDPOINT, headers=headers, json=payload)

    if response.status_code in (403, 429):
        raise BlockedError(f"VFS returned HTTP {response.status_code}")
    if "Just a moment" in response.text or "cf-chl" in response.text:
        raise BlockedError("Cloudflare challenge page — Playwright fallback needed")
    response.raise_for_status()

    return _parse(response.json(), vac_code=vac_code, visa_category=visa_category)


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
    # Field names inferred from naming conventions; verify against a real non-empty response.
    date = raw.get("slotDate") or raw.get("date") or raw.get("availableDate") or ""
    time_ = raw.get("slotTime") or raw.get("time") or raw.get("availableTime")
    return Slot(
        date=str(date),
        time=str(time_) if time_ else None,
        center=raw.get("vacCode", vac_code),
        category=raw.get("visaCategoryCode", visa_category),
    )
