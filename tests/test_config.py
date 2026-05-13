from vfs.config import QuietHours


def test_quiet_hours_simple_window():
    qh = QuietHours(start=22, end=23)
    assert qh.contains(22) is True
    assert qh.contains(21) is False
    assert qh.contains(23) is False


def test_quiet_hours_overnight_window():
    qh = QuietHours(start=23, end=7)
    assert qh.contains(23) is True
    assert qh.contains(2) is True
    assert qh.contains(6) is True
    assert qh.contains(7) is False
    assert qh.contains(12) is False


def test_quiet_hours_equal_disables():
    qh = QuietHours(start=0, end=0)
    assert qh.contains(0) is False
    assert qh.contains(12) is False
