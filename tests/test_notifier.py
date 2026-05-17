from unittest.mock import MagicMock, patch

import pytest

from vfs import notifier
from vfs.scraper import Slot


def test_format_message_lists_each_slot():
    slots = [
        Slot(date="05/20/2026 00:00:00", time=None,
             center="Netherlands Islamabad", category="TR"),
        Slot(date="05/21/2026 00:00:00", time="09:30",
             center="Netherlands Lahore", category="TR"),
    ]
    msg = notifier.format_message(slots)

    assert msg.startswith("New VFS appointment slots available:")
    assert "Netherlands Islamabad" in msg
    assert "05/20/2026 00:00:00" in msg
    # A slot with a time renders "date time"; one without renders just the date.
    assert "05/21/2026 00:00:00 09:30" in msg


def test_send_pushover_uses_emergency_priority():
    with patch("vfs.notifier.httpx.post") as mock_post:
        mock_post.return_value = MagicMock()
        notifier.send_pushover(token="tok", user="usr", body="slot open")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == notifier._PUSHOVER_API
    data = kwargs["data"]
    assert data["token"] == "tok"
    assert data["user"] == "usr"
    assert data["message"] == "slot open"
    # priority=2 is Emergency; retry/expire must stay within Pushover's bounds
    # (retry >= 30s, expire <= 10800s) or the API rejects the message.
    assert data["priority"] == 2
    assert data["retry"] >= 30
    assert data["expire"] <= 10800
    # A non-2xx (e.g. bad token) must surface, not be swallowed.
    mock_post.return_value.raise_for_status.assert_called_once()


def test_send_pushover_propagates_http_error():
    with patch("vfs.notifier.httpx.post") as mock_post:
        resp = MagicMock()
        resp.raise_for_status.side_effect = RuntimeError("401 Unauthorized")
        mock_post.return_value = resp

        with pytest.raises(RuntimeError, match="401"):
            notifier.send_pushover(token="bad", user="bad", body="x")
