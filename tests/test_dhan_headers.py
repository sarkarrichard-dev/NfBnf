from __future__ import annotations

from index_ai.config import DhanSettings
from index_ai.dhan import DhanClient


def test_chart_requests_omit_client_id_header() -> None:
    settings = DhanSettings(
        client_id="999999",
        access_token="eyJ.test.token",
        api_base_url="https://api.dhan.co/v2",
        api_key="k",
        api_secret="s",
        auth_base_url="https://auth.dhan.co",
        token_expiry="",
    )
    client = DhanClient(settings)
    chart_headers = client._headers("/charts/intraday")
    assert "access-token" in chart_headers
    assert "client-id" not in chart_headers

    ltp_headers = client._headers("/marketfeed/ltp")
    assert ltp_headers.get("client-id") == "999999"
