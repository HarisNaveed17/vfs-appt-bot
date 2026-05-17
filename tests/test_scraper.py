from vfs.scraper import Slot, _parse


NO_SLOTS_RESPONSE = {
    "earliestDate": None,
    "earliestSlotLists": [],
    "error": {"code": 1035, "description": "No slots available", "type": "Information"},
}


def test_parse_no_slots_returns_empty():
    assert _parse(NO_SLOTS_RESPONSE, vac_code="NISL", visa_category="TR") == []


def test_parse_earliest_date_only():
    data = {"earliestDate": "2026-06-15", "earliestSlotLists": []}
    result = _parse(data, vac_code="NISL", visa_category="TR")
    assert result == [Slot(date="2026-06-15", time=None, center="NISL", category="TR")]


def test_parse_slot_list():
    # Verified-real shape: each entry is {date, applicant} — key is `date`
    # (not slotDate) and there is NO per-slot time field. center/category
    # fall back to the call args since the entries don't carry them.
    data = {
        "earliestDate": "06/15/2026 00:00:00",
        "earliestSlotLists": [
            {"date": "06/15/2026 00:00:00", "applicant": "1"},
            {"date": "06/16/2026 00:00:00", "applicant": "1"},
        ],
    }
    result = _parse(data, vac_code="NISL", visa_category="TR")
    assert len(result) == 2
    assert result[0] == Slot(
        date="06/15/2026 00:00:00", time=None, center="NISL", category="TR"
    )
    assert result[1] == Slot(
        date="06/16/2026 00:00:00", time=None, center="NISL", category="TR"
    )


def test_parse_slot_has_no_time():
    # Real entries never carry a per-slot time, so time is always None.
    data = {
        "earliestDate": "06/15/2026 00:00:00",
        "earliestSlotLists": [{"date": "06/15/2026 00:00:00", "applicant": "1"}],
    }
    result = _parse(data, vac_code="NISL", visa_category="TR")
    assert result[0].date == "06/15/2026 00:00:00"
    assert result[0].time is None
