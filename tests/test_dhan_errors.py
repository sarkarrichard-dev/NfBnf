from __future__ import annotations

from index_ai.dhan_errors import parse_dhan_error_payload


def test_invalid_ip_message_is_actionable() -> None:
    msg = parse_dhan_error_payload(
        {"errorCode": "DH-905", "errorMessage": "Invalid IP"},
    )
    assert msg is not None
    assert "whitelist" in msg.lower()
    assert "invalid ip" not in msg.lower() or "not whitelisted" in msg.lower()
