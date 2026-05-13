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
    data = {
        "earliestDate": "2026-06-15",
        "earliestSlotLists": [
            {"slotDate": "2026-06-15", "slotTime": "10:00", "vacCode": "NISL", "visaCategoryCode": "TR"},
            {"slotDate": "2026-06-16", "slotTime": "14:30", "vacCode": "NISL", "visaCategoryCode": "TR"},
        ],
    }
    result = _parse(data, vac_code="NISL", visa_category="TR")
    assert len(result) == 2
    assert result[0] == Slot(date="2026-06-15", time="10:00", center="NISL", category="TR")
    assert result[1] == Slot(date="2026-06-16", time="14:30", center="NISL", category="TR")


def test_parse_slot_list_missing_time():
    data = {
        "earliestDate": "2026-06-15",
        "earliestSlotLists": [{"slotDate": "2026-06-15"}],
    }
    result = _parse(data, vac_code="NISL", visa_category="TR")
    assert result[0].time is None
