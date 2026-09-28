import asyncio

import httpx

from index_ai.dhan_errors import DhanAuthError
from index_ai.scanner import _friendly_error


def test_timeout_error_names_its_class_instead_of_an_empty_string():
    # regression: str(asyncio.TimeoutError()) == "" -- every timed-out stage
    # logged "stage=trails · error=" with no diagnostic value at all
    assert _friendly_error(asyncio.TimeoutError()) == "TimeoutError"
    assert _friendly_error(TimeoutError()) == "TimeoutError"


def test_ordinary_exceptions_keep_their_own_message():
    assert _friendly_error(ValueError("bad thing")) == "bad thing"


def test_dhan_auth_error_and_http_status_paths_are_unaffected():
    assert _friendly_error(DhanAuthError("token expired")) == "token expired"
    resp = httpx.Response(401, request=httpx.Request("GET", "https://api.dhan.co/x"))
    reason = _friendly_error(httpx.HTTPStatusError("x", request=resp.request, response=resp))
    assert reason  # still produces the Dhan-specific explanation, not an empty string
