from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from index_ai.config import MEMORY_DIR, DhanSettings, candle_interval_minutes, update_env_values
from index_ai.market_clock import now_ist_iso, parse_ist_datetime

_OAUTH_STATE_PATH = MEMORY_DIR / "dhan_oauth_state.json"
_CONSENT_TTL_SECONDS = 600  # Dhan login links expire quickly (~few minutes)

_UUID_TOKEN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


def looks_like_jwt(value: str) -> bool:
    s = (value or "").strip()
    parts = s.split(".")
    return len(parts) == 3 and parts[0].startswith("eyJ") and len(parts[1]) > 10


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.strip().split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_access_token(raw: str) -> str:
    """Strip whitespace, quotes, and Bearer prefix from pasted Dhan JWT."""
    s = (raw or "").strip().strip('"').strip("'")
    if s.lower().startswith("bearer "):
        s = s[7:].strip()
    return s.replace("\n", "").replace("\r", "")


def oauth_redirect_urls() -> list[str]:
    """Redirect URLs to register on the Dhan API key (must match exactly)."""
    return [
        "http://127.0.0.1:8000/dhan/oauth/callback",
        "http://127.0.0.1:8000/callback",
    ]


def parse_oauth_callback_value(raw: str | None) -> str:
    """
    Normalize tokenId / full redirect URL / JWT from OAuth step 2 or manual paste.
    """
    text = normalize_access_token(raw or "")
    if not text:
        return ""
    if looks_like_jwt(text):
        return text
    extracted = normalize_token_id(text)
    return extracted or text


def _validate_dhan_client_id(client_id: str) -> str:
    cid = (client_id or "").strip()
    if not cid:
        raise RuntimeError(
            "DHAN_CLIENT_ID is missing. Add your numeric Dhan Client ID to .env "
            "(web.dhan.co → profile — not the API key)."
        )
    if not cid.isdigit():
        raise RuntimeError(
            f"DHAN_CLIENT_ID must be numeric (got {cid!r}). "
            "Use the Dhan Client ID from your profile, not the API key."
        )
    return cid


def _load_oauth_state() -> dict[str, Any]:
    if not _OAUTH_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(_OAUTH_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_oauth_state(state: dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _OAUTH_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _store_pending_consent(consent_app_id: str, client_id: str) -> None:
    _save_oauth_state(
        {
            "consentAppId": consent_app_id,
            "client_id": client_id,
            "created_at": now_ist_iso(),
            "created_monotonic": time.monotonic(),
        }
    )


def _reject_consent_app_id_mistake(token_id: str) -> None:
    """Users often paste consentAppId from step 1 instead of tokenId from redirect."""
    state = _load_oauth_state()
    pending = str(state.get("consentAppId") or "").strip()
    if pending and pending.lower() == token_id.strip().lower():
        raise RuntimeError(
            "That value is consentAppId from step 1 (Create Login Link), not tokenId. "
            "Open the Login URL in your browser, complete login, then either let the app "
            "save automatically or paste tokenId from the redirect URL."
        )


def _consent_link_stale() -> bool:
    state = _load_oauth_state()
    created = state.get("created_monotonic")
    if not isinstance(created, (int, float)):
        return False
    return time.monotonic() - float(created) > _CONSENT_TTL_SECONDS


def normalize_token_id(raw: str) -> str:
    """
    Accept a bare tokenId or a full redirect URL / query string from Step 2.

    Dhan redirects to: ``{redirect_URL}/?tokenId={uuid}``
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "tokenid=" in s.lower():
        if "://" in s:
            parsed = urlparse(s)
            qs = parse_qs(parsed.query)
        else:
            qs = parse_qs(s.lstrip("?"))
        for key in ("tokenId", "tokenid"):
            if key in qs and qs[key]:
                return str(qs[key][0]).strip()
    # Sometimes operators paste "tokenId=uuid" without a leading ?
    m = re.search(r"tokenId=([^&\s#]+)", s, re.I)
    if m:
        return m.group(1).strip()
    return s


def _auth_headers(settings: DhanSettings) -> dict[str, str]:
    if not settings.app_credentials_ready:
        raise RuntimeError(
            "Dhan API key and secret are missing. Set DHAN_API_KEY and DHAN_API_SECRET in .env "
            "(from web.dhan.co → My Profile → Access DhanHQ APIs → API key)."
        )
    return {
        "app_id": settings.api_key,
        "app_secret": settings.api_secret,
    }


def _parse_json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    text = (response.text or "").strip()
    return {"status": "error", "message": text[:500] if text else response.reason_phrase}


def _raise_dhan_http_error(response: httpx.Response, step: str) -> None:
    from index_ai.dhan_errors import parse_dhan_error_payload

    data = _parse_json_response(response)
    friendly = parse_dhan_error_payload(data) if isinstance(data, dict) else None
    hint = friendly or data.get("message") or data.get("error") or data.get("statusMessage") or data
    raise RuntimeError(f"Dhan {step} failed ({response.status_code}): {hint}")


def _ensure_success_payload(data: dict[str, Any], step: str) -> None:
    status = str(data.get("status") or "").strip().lower()
    if status and status not in ("success", "ok"):
        raise RuntimeError(f"Dhan {step} rejected the request: {data}")


def _extract_consume_payload(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("accessToken") or data.get("access_token") or _looks_like_token_field(data.get("token")):
        return data
    inner = data.get("data")
    if isinstance(inner, dict) and (
        inner.get("accessToken") or inner.get("access_token") or _looks_like_token_field(inner.get("token"))
    ):
        return inner
    return data


def _looks_like_token_field(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and (looks_like_jwt(text) or bool(_UUID_TOKEN_RE.match(text)))


def finalize_token_save(
    settings: DhanSettings,
    *,
    access_token: str,
    client_id: str,
    expiry: str,
    source: str,
    dhan_client_name: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Write token to .env, verify on option-chain API, clear health cache."""
    token = normalize_access_token(access_token)
    cid = _validate_dhan_client_id(client_id)
    if not token:
        raise RuntimeError("Dhan did not return an access token.")

    clear_dhan_health_cache()
    probe: dict[str, Any] = {}
    if verify:
        probe = probe_access_token(_settings_with_token(settings, token, cid))

    update_env_values(
        {
            "DHAN_CLIENT_ID": cid,
            "DHAN_ACCESS_TOKEN": token,
            "DHAN_TOKEN_EXPIRY": expiry,
            "DHAN_TOKEN_SOURCE": _source_to_token_class(source),
        }
    )
    clear_dhan_health_cache()

    message = "Access token saved to .env and verified on Dhan option-chain API."
    if verify and not probe.get("profile_ok"):
        message += " (/profile unavailable — normal for some tokens.)"
    if verify and not probe.get("charts_ok"):
        message += " Intraday charts need Data API on web.dhan.co → Access DhanHQ APIs."

    from index_ai.market_clock import format_ist_display

    out: dict[str, Any] = {
        "status": "saved",
        "source": source,
        "dhanClientId": cid,
        "expiryTime": format_ist_display(expiry) if expiry else None,
        "message": message,
    }
    if dhan_client_name:
        out["dhanClientName"] = dhan_client_name
    if verify:
        out["probe"] = probe
    return out


def generate_consent(settings: DhanSettings) -> dict[str, Any]:
    client_id = _validate_dhan_client_id(settings.client_id)
    url = f"{settings.auth_base_url}/app/generate-consent"
    with httpx.Client(timeout=30) as client:
        response = client.post(
            url,
            params={"client_id": client_id},
            headers=_auth_headers(settings),
        )
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "generate-consent (step 1)")
        data = _parse_json_response(response)

    _ensure_success_payload(data, "generate-consent (step 1)")
    consent_id = str(data.get("consentAppId") or "").strip()
    if not consent_id:
        raise RuntimeError(f"Dhan generate-consent did not return consentAppId: {data}")

    _store_pending_consent(consent_id, client_id)
    login_url = f"{settings.auth_base_url}/login/consentApp-login?consentAppId={consent_id}"
    redirects = oauth_redirect_urls()
    return {
        "status": "success",
        "consentAppId": consent_id,
        "consentAppStatus": data.get("consentAppStatus"),
        "login_url": login_url,
        "client_id_used": client_id,
        "oauth_redirect_urls": redirects,
        "instructions": (
            "1. Open login_url now (expires in a few minutes).\n"
            "2. Complete Dhan login + 2FA.\n"
            "3. Browser redirects to your app — token saves automatically.\n"
            "   Manual fallback: paste tokenId from the address bar → Save Token.\n\n"
            f"Redirect URL on Dhan must be exactly one of:\n  • {redirects[0]}\n  • {redirects[1]}"
        ),
        "oauth_troubleshooting": _oauth_troubleshooting(),
    }


def _oauth_troubleshooting() -> str:
    urls = "\n  • ".join(oauth_redirect_urls())
    return (
        "OAuth 'Details not found' usually means:\n"
        "• DHAN_CLIENT_ID in .env is not your numeric Dhan Client ID for this API key.\n"
        "• API key was created on a different Dhan account.\n"
        f"• Redirect URL on the API key must be exactly one of:\n  • {urls}\n"
        "• Login link was opened too late (create a fresh link).\n"
        "• You pasted consentAppId instead of tokenId from the redirect.\n"
        "Fallback: web.dhan.co → Access DhanHQ APIs → Generate Access Token → paste eyJ… JWT → Save Token."
    )


def save_access_token_direct(settings: DhanSettings, access_token: str) -> dict[str, Any]:
    """
    Save a JWT access token copied from Dhan Web or from a redirect that already contains the JWT.

    Do not send this value to consumeApp-consent (that endpoint expects a short session tokenId).
    """
    token = normalize_access_token(access_token)
    if not looks_like_jwt(token):
        raise RuntimeError(
            "This does not look like a Dhan access token (JWT). "
            "Use a short tokenId from the redirect URL, or paste the full eyJ... JWT from Dhan Web."
        )
    payload = _jwt_payload(token)
    client_id = str(payload.get("dhanClientId") or settings.client_id or "").strip()
    if not client_id:
        raise RuntimeError("Could not read dhanClientId from JWT. Set DHAN_CLIENT_ID in .env first.")
    expiry = ""
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        from datetime import datetime, timezone

        expiry = datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()

    return finalize_token_save(
        settings,
        access_token=token,
        client_id=client_id,
        expiry=expiry,
        source="access_token_jwt",
        verify=True,
    )


def save_token_from_user_input(settings: DhanSettings, raw: str) -> dict[str, Any]:
    """Accept OAuth tokenId (UUID) or a ready-made access token JWT."""
    text = parse_oauth_callback_value(raw)
    if not text:
        raise RuntimeError("Paste tokenId from the redirect URL, or paste your Dhan access token JWT.")

    if looks_like_jwt(text):
        return save_access_token_direct(settings, text)

    if not _UUID_TOKEN_RE.match(text):
        raise RuntimeError(
            "Unrecognized value. After browser login, paste the short tokenId (UUID) from the redirect URL. "
            "If Dhan gave you a long eyJ... JWT instead, paste that whole string — do not call it through "
            "consumeApp-consent."
        )
    _reject_consent_app_id_mistake(text)
    return consume_consent(settings, text)


def consume_consent(settings: DhanSettings, token_id: str) -> dict[str, Any]:
    normalized = parse_oauth_callback_value(token_id)
    if looks_like_jwt(normalized):
        return save_access_token_direct(settings, normalized)
    if not normalized:
        raise RuntimeError(
            "Paste tokenId from Dhan's redirect URL (query param tokenId=...), not the consentAppId."
        )
    if not _UUID_TOKEN_RE.match(normalized):
        raise RuntimeError(f"tokenId must be a UUID. Got: {normalized[:80]}")
    _reject_consent_app_id_mistake(normalized)

    url = f"{settings.auth_base_url}/app/consumeApp-consent"
    with httpx.Client(timeout=30) as client:
        response = client.get(
            url,
            params={"tokenId": normalized},
            headers=_auth_headers(settings),
        )
        if response.status_code >= 400:
            hint = ""
            if response.status_code in (400, 401, 403, 404):
                hint = (
                    " tokenId is one-time and expires quickly — create a fresh Login Link, "
                    "complete login again, and save the new tokenId immediately."
                )
            try:
                _raise_dhan_http_error(response, "consumeApp-consent (step 3)")
            except RuntimeError as exc:
                raise RuntimeError(str(exc) + hint) from exc
        data = _extract_consume_payload(_parse_json_response(response))

    access_token = _extract_access_token_value(data)
    client_id = str(data.get("dhanClientId") or settings.client_id)
    expiry = str(data.get("expiryTime") or "")
    if not access_token:
        raise RuntimeError(f"Dhan consume-consent did not return accessToken: {data}")

    result = finalize_token_save(
        settings,
        access_token=access_token,
        client_id=client_id,
        expiry=expiry,
        source="oauth_consume",
        dhan_client_name=str(data.get("dhanClientName") or "") or None,
        verify=True,
    )
    _save_oauth_state({"last_token_saved_at": now_ist_iso(), "last_source": "oauth_consume"})
    return result


def _profile_headers(settings: DhanSettings) -> dict[str, str]:
    headers = {"Accept": "application/json", "access-token": settings.access_token}
    if settings.client_id:
        headers["client-id"] = settings.client_id
    return headers


def _market_headers(settings: DhanSettings) -> dict[str, str]:
    """Headers for option chain / market feed (require client-id with web tokens)."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": settings.access_token,
    }
    if settings.client_id:
        headers["client-id"] = settings.client_id
    return headers


def _settings_with_token(settings: DhanSettings, access_token: str, client_id: str) -> DhanSettings:
    return DhanSettings(
        client_id=client_id,
        access_token=access_token,
        api_base_url=settings.api_base_url,
        api_key=settings.api_key,
        api_secret=settings.api_secret,
        auth_base_url=settings.auth_base_url,
        token_expiry=settings.token_expiry,
    )


def probe_access_token(settings: DhanSettings) -> dict[str, Any]:
    """
    Validate token against APIs this app uses.

    Dhan /profile often returns misleading DH-906 for valid web tokens; option-chain is reliable.
    Intraday charts need a separate Data API subscription.
    """
    if not settings.ready:
        raise RuntimeError(_missing_token_message(settings))

    profile: dict[str, Any] = {}
    profile_ok = False
    market_ok = False
    charts_ok = False
    charts_error: str | None = None
    probe_errors: list[str] = []

    url_base = settings.api_base_url.rstrip("/")

    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(f"{url_base}/profile", headers=_profile_headers(settings))
            if response.status_code < 400:
                data = _parse_json_response(response)
                profile = data.get("data") if isinstance(data.get("data"), dict) else data
                profile_ok = bool(profile)
    except Exception as exc:
        probe_errors.append(f"profile: {exc}")

    try:
        from index_ai.instruments import get_instrument

        inst = get_instrument("NIFTY")
        if inst.underlying_security_id is None:
            raise RuntimeError("NIFTY_SECURITY_ID is not set in .env.")
        payload = {
            "UnderlyingScrip": inst.underlying_security_id,
            "UnderlyingSeg": inst.option_segment,
        }
        with httpx.Client(timeout=20) as client:
            response = client.post(
                f"{url_base}/optionchain/expirylist",
                json=payload,
                headers=_market_headers(settings),
            )
            if response.status_code >= 400:
                _raise_dhan_http_error(response, "optionchain expirylist")
            data = _parse_json_response(response)
        if not list(data.get("data") or []):
            raise RuntimeError("optionchain expirylist returned no expiries.")
        market_ok = True
    except Exception as exc:
        probe_errors.append(f"market: {exc}")

    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        from index_ai.dhan import DhanClient
        from index_ai.instruments import get_instrument

        client = DhanClient(settings)
        inst = get_instrument("NIFTY")
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
        start = now - timedelta(days=2)
        client.intraday_history(
            inst,
            from_date=start.strftime("%Y-%m-%d 09:15:00"),
            to_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            interval=candle_interval_minutes(),
        )
        charts_ok = True
    except Exception as exc:
        charts_error = str(exc)
        probe_errors.append(f"charts: {exc}")

    token_ok = market_ok
    if not token_ok:
        detail = "; ".join(probe_errors[:3])
        raise RuntimeError(
            "Access token rejected by Dhan on option-chain API. "
            f"{detail} "
            "Generate a fresh token on web.dhan.co → Access DhanHQ APIs → Generate Access Token."
        )

    return {
        "ok": token_ok and charts_ok,
        "token_ok": token_ok,
        "profile_ok": profile_ok,
        "market_ok": market_ok,
        "charts_ok": charts_ok,
        "charts_error": charts_error,
        "profile": profile,
        "probe_errors": probe_errors,
    }


def jwt_token_status(access_token: str) -> dict[str, Any]:
    """Decode JWT locally (no API call) for expiry and client id."""
    if not looks_like_jwt(access_token):
        return {"is_jwt": False}
    payload = _jwt_payload(access_token)
    client_id = str(payload.get("dhanClientId") or "").strip()
    exp = payload.get("exp")
    expired = False
    expires_ist: str | None = None
    if isinstance(exp, (int, float)):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        exp_dt = datetime.fromtimestamp(int(exp), tz=ZoneInfo("Asia/Kolkata"))
        from index_ai.market_clock import format_ist_clock_12h

        expires_ist = f"{exp_dt.strftime('%d %b %Y')}, {format_ist_clock_12h(exp_dt)} IST"
        expired = exp_dt.timestamp() < datetime.now(ZoneInfo("Asia/Kolkata")).timestamp()
    return {
        "is_jwt": True,
        "dhan_client_id": client_id or None,
        "expired": expired,
        "expires_ist": expires_ist,
        "note": (
            "JWT expiry is decoded locally only. Use Verify data access to confirm Dhan still accepts the token."
        ),
    }


def reconcile_env_with_jwt() -> dict[str, Any] | None:
    """
    If .env has a JWT, ensure DHAN_CLIENT_ID matches the token's dhanClientId.
    Fixes 401 when an old/wrong client id was left in .env.
    """
    from index_ai.config import ENV_PATH

    if not ENV_PATH.exists():
        return None
    load_dotenv = __import__("dotenv", fromlist=["load_dotenv"]).load_dotenv
    load_dotenv(ENV_PATH, override=True)
    import os

    token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
    if not looks_like_jwt(token):
        return None
    status = jwt_token_status(token)
    cid = str(status.get("dhan_client_id") or "").strip()
    if not cid:
        return status
    current = os.getenv("DHAN_CLIENT_ID", "").strip()
    if current != cid:
        update_env_values({"DHAN_CLIENT_ID": cid})
        status = {**status, "synced_client_id": cid, "previous_client_id": current or None}
    return status


def _extract_access_token_value(data: dict[str, Any]) -> str:
    """Parse access token from Dhan auth/renew responses (multiple field names)."""
    raw = data.get("accessToken") or data.get("access_token") or data.get("token")
    if isinstance(raw, dict):
        return str(raw.get("token") or raw.get("accessToken") or raw.get("access_token") or "")
    if raw:
        return str(raw)
    inner = data.get("data")
    if isinstance(inner, dict):
        nested = _extract_access_token_value(inner)
        if nested:
            return nested
    return ""


def _source_to_token_class(source: str) -> str:
    """WEB tokens support RenewToken; OAuth consent tokens do not (Dhan docs)."""
    s = str(source or "").lower()
    if s in {"oauth_consume", "oauth"}:
        return "OAUTH"
    return "WEB"


def totp_credentials_configured() -> bool:
    """True when PIN + authenticator secret are set for generateAccessToken."""
    pin = os.getenv("DHAN_PIN", "").strip()
    secret = os.getenv("DHAN_TOTP_SECRET", "").strip()
    client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
    return bool(client_id.isdigit() and len(pin) == 6 and pin.isdigit() and len(secret) >= 8)


def _totp_now(secret: str, *, period: int = 30, digits: int = 6) -> str:
    """RFC 6238 TOTP (same codes as Google Authenticator)."""
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    counter = int(time.time()) // period
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**digits).zfill(digits)


def generate_access_token_via_totp(settings: DhanSettings) -> dict[str, Any]:
    """
    Issue a fresh 24h WEB token using Dhan PIN + TOTP (no browser).

    Requires TOTP enabled on web.dhan.co → Access DhanHQ APIs → Setup TOTP.
    See https://dhanhq.co/docs/v2/authentication/ and DhanHQ-py DhanLogin.
    """
    if not totp_credentials_configured():
        raise RuntimeError(
            "Set DHAN_CLIENT_ID, DHAN_PIN (6 digits), and DHAN_TOTP_SECRET (authenticator "
            "setup code from Dhan Web) in .env — or paste a JWT from Generate Access Token."
        )
    cid = _validate_dhan_client_id(settings.client_id or os.getenv("DHAN_CLIENT_ID", ""))
    pin = os.getenv("DHAN_PIN", "").strip()
    secret = os.getenv("DHAN_TOTP_SECRET", "").strip()
    url = f"{settings.auth_base_url.rstrip('/')}/app/generateAccessToken"
    params = {
        "dhanClientId": cid,
        "pin": pin,
        "totp": _totp_now(secret),
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, params=params)
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "generateAccessToken (TOTP)")
        data = _parse_json_response(response)
    _ensure_success_payload(data, "generateAccessToken (TOTP)")
    access_token = normalize_access_token(_extract_access_token_value(data))
    if not access_token or not looks_like_jwt(access_token):
        raise RuntimeError(f"Dhan TOTP login did not return accessToken: {data}")
    client_id = str(data.get("dhanClientId") or cid)
    expiry = str(data.get("expiryTime") or "")
    clear_dhan_health_cache()
    result = finalize_token_save(
        settings,
        access_token=access_token,
        client_id=client_id,
        expiry=expiry,
        source="totp",
        dhan_client_name=str(data.get("dhanClientName") or "") or None,
        verify=True,
    )
    _save_oauth_state({"last_token_saved_at": now_ist_iso(), "last_source": "totp"})
    return {
        **result,
        "status": "totp_generated",
        "message": "Access token generated via PIN+TOTP (renewable WEB token, 24h).",
    }


def _token_source_from_env() -> str:
    raw = os.getenv("DHAN_TOKEN_SOURCE", "").strip().upper()
    if raw in {"WEB", "OAUTH"}:
        return raw
    return "WEB"


def token_renew_eligibility(settings: DhanSettings) -> dict[str, Any]:
    """Whether RenewToken applies to the current saved token."""
    source = _token_source_from_env()
    ttl = _token_seconds_left(settings) if settings.ready else None
    eligible = source == "WEB"
    reason: str | None = None
    if not settings.ready:
        reason = "no_token"
    elif source == "OAUTH":
        reason = (
            "OAuth/API-key tokens cannot use RenewToken. "
            "Use web.dhan.co → Generate Access Token for auto-renew, or Create Login Link again."
        )
    elif ttl is not None and ttl <= 0:
        reason = "Token expired — RenewToken only works while the token is still active."
    return {
        "token_source": source,
        "renewable": eligible and reason is None,
        "seconds_left": ttl,
        "reason": reason,
    }


def _renew_client_id(settings: DhanSettings, access_token: str) -> str:
    """Use dhanClientId embedded in JWT — must match RenewToken header."""
    reconcile_env_with_jwt()
    jwt_status = jwt_token_status(access_token)
    cid = str(jwt_status.get("dhan_client_id") or settings.client_id or "").strip()
    return _validate_dhan_client_id(cid)


def _renew_ineligible_message(settings: DhanSettings) -> str:
    info = token_renew_eligibility(settings)
    if info.get("reason"):
        return str(info["reason"])
    return "This token cannot be renewed automatically."


def verify_access_token(settings: DhanSettings) -> dict[str, Any]:
    """Confirm access token works against Dhan market APIs."""
    probe = probe_access_token(settings)
    return {"ok": True, "profile": probe.get("profile") or {}, **probe}


def renew_access_token(settings: DhanSettings) -> dict[str, Any]:
    """Extend token validity (Dhan Web–issued tokens only, while still active)."""
    if not settings.ready:
        raise RuntimeError(_missing_token_message(settings))

    eligibility = token_renew_eligibility(settings)
    if not eligibility.get("renewable"):
        raise RuntimeError(_renew_ineligible_message(settings))

    reconcile_env_with_jwt()
    token = normalize_access_token(settings.access_token)
    cid = _renew_client_id(settings, token)
    url = f"{settings.api_base_url.rstrip('/')}/RenewToken"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token,
        "dhanClientId": cid,
    }
    with httpx.Client(timeout=20) as client:
        response = client.get(url, headers=headers)
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "RenewToken")

        data = _parse_json_response(response)
    new_token = normalize_access_token(_extract_access_token_value(data))
    if not new_token or not looks_like_jwt(new_token):
        raise RuntimeError(f"Dhan RenewToken did not return a valid access token: {data}")

    client_id = str(data.get("dhanClientId") or cid)
    expiry = str(data.get("expiryTime") or "")
    if not expiry:
        jwt_after = jwt_token_status(new_token)
        exp = (_jwt_payload(new_token) or {}).get("exp")
        if isinstance(exp, (int, float)):
            from datetime import datetime, timezone

            expiry = datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()

    clear_dhan_health_cache()
    result = finalize_token_save(
        settings,
        access_token=new_token,
        client_id=client_id,
        expiry=expiry,
        source="renew",
        verify=False,
    )
    clear_dhan_health_cache()
    return {
        **result,
        "status": "renewed",
        "message": "Token renewed for another 24 hours (Dhan Web token).",
    }


def _auto_renew_enabled() -> bool:
    raw = os.getenv("AUTO_RENEW_DHAN_TOKEN", "true").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _auto_renew_threshold_seconds() -> int:
    raw = os.getenv("AUTO_RENEW_THRESHOLD_MINUTES", "60").strip()
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 60
    return max(1, minutes) * 60


def _auto_renew_poll_seconds() -> int:
    raw = os.getenv("AUTO_RENEW_POLL_SECONDS", "300").strip()
    try:
        sec = int(raw)
    except ValueError:
        sec = 300
    return max(60, sec)


def _token_seconds_left(settings: DhanSettings) -> int | None:
    status = jwt_token_status(settings.access_token)
    payload = status if status.get("is_jwt") else _jwt_payload(settings.access_token)
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        now_ts = time.time()
        return int(exp - now_ts)
    if settings.token_expiry:
        exp_dt = parse_ist_datetime(settings.token_expiry)
        if exp_dt is not None:
            return int(exp_dt.timestamp() - time.time())
    return None


def auto_refresh_dhan_token(
    settings: DhanSettings,
    *,
    force: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """
    Refresh Dhan token proactively (near expiry) or reactively (force=True).
    Returns current/updated settings for immediate reuse by callers.
    """
    from index_ai.config import settings as load_settings

    global _LAST_AUTO_RENEW_AT
    _AUTO_RENEW_STATUS["enabled"] = _auto_renew_enabled()
    if not settings.ready:
        if totp_credentials_configured() and (force or str(reason).startswith("startup")):
            try:
                generated = generate_access_token_via_totp(settings)
                _LAST_AUTO_RENEW_AT = time.monotonic()
                _AUTO_RENEW_STATUS["last_success_at"] = now_ist_iso()
                _AUTO_RENEW_STATUS["last_error"] = None
                cfg = load_settings()
                return {
                    "attempted": True,
                    "renewed": True,
                    "reason": "totp_generated",
                    "details": generated,
                    "trigger": reason or "startup_totp",
                    "settings": cfg.dhan,
                }
            except Exception as exc:
                _AUTO_RENEW_STATUS["last_error"] = str(exc)[:300]
                return {
                    "attempted": True,
                    "renewed": False,
                    "reason": "totp_failed",
                    "error": str(exc),
                    "settings": settings,
                }
        return {"attempted": False, "renewed": False, "reason": "not_ready", "settings": settings}
    if not _auto_renew_enabled() and not force:
        return {"attempted": False, "renewed": False, "reason": "disabled", "settings": settings}

    ttl = _token_seconds_left(settings)
    threshold = _auto_renew_threshold_seconds()
    eligibility = token_renew_eligibility(settings)

    if ttl is not None and ttl <= 0:
        if totp_credentials_configured():
            try:
                generated = generate_access_token_via_totp(settings)
                _LAST_AUTO_RENEW_AT = time.monotonic()
                _AUTO_RENEW_STATUS["last_success_at"] = now_ist_iso()
                _AUTO_RENEW_STATUS["last_error"] = None
                try:
                    from index_ai.token_scheduler import mark_startup_renew_done

                    mark_startup_renew_done()
                except Exception:
                    pass
                cfg = load_settings()
                return {
                    "attempted": True,
                    "renewed": True,
                    "reason": "totp_generated",
                    "details": generated,
                    "trigger": reason or "expired_totp",
                    "settings": cfg.dhan,
                }
            except Exception as exc:
                _AUTO_RENEW_STATUS["last_error"] = str(exc)[:300]
        msg = (
            eligibility.get("reason")
            or "Token expired — RenewToken only works while active. "
            "Use a WEB JWT from web.dhan.co, enable TOTP (DHAN_PIN + DHAN_TOTP_SECRET), or re-login."
        )
        if not totp_credentials_configured():
            _AUTO_RENEW_STATUS["last_error"] = str(msg)[:300]
        return {
            "attempted": bool(totp_credentials_configured()),
            "renewed": False,
            "reason": "expired",
            "error": msg,
            "settings": settings,
        }

    if not eligibility.get("renewable") and not force:
        _AUTO_RENEW_STATUS["last_error"] = str(eligibility.get("reason") or "not_renewable")[:300]
        return {
            "attempted": False,
            "renewed": False,
            "reason": "not_renewable",
            "error": eligibility.get("reason"),
            "settings": settings,
        }

    should_refresh = force or ttl is None or ttl <= threshold
    try:
        from index_ai.token_scheduler import daily_renew_due, startup_renew_due

        if daily_renew_due():
            should_refresh = True
            reason = reason or "daily_renew_ist"
        elif startup_renew_due() and reason.startswith("startup"):
            should_refresh = True
            reason = reason or "startup_renew"
    except Exception:
        pass

    if not should_refresh:
        return {"attempted": False, "renewed": False, "reason": "not_due", "settings": settings}

    with _AUTO_RENEW_LOCK:
        now_mono = time.monotonic()
        _AUTO_RENEW_STATUS["last_attempt_at"] = now_ist_iso()
        _AUTO_RENEW_STATUS["last_trigger"] = reason or None
        if not force and now_mono - _LAST_AUTO_RENEW_AT < 45:
            cfg = load_settings()
            return {
                "attempted": False,
                "renewed": False,
                "reason": "recently_checked",
                "settings": cfg.dhan,
            }
        try:
            renewed = renew_access_token(settings)
            _LAST_AUTO_RENEW_AT = now_mono
            _AUTO_RENEW_STATUS["last_success_at"] = now_ist_iso()
            _AUTO_RENEW_STATUS["last_error"] = None
            try:
                from index_ai.token_scheduler import mark_daily_renew_done, mark_startup_renew_done

                if str(reason or "").startswith("daily_renew"):
                    mark_daily_renew_done()
                if str(reason or "").startswith("startup"):
                    mark_startup_renew_done()
            except Exception:
                pass
            cfg = load_settings()
            return {
                "attempted": True,
                "renewed": True,
                "reason": "renewed",
                "details": renewed,
                "trigger": reason,
                "settings": cfg.dhan,
            }
        except Exception as exc:
            _LAST_AUTO_RENEW_AT = now_mono
            _AUTO_RENEW_STATUS["last_error"] = str(exc)[:300]
            cfg = load_settings()
            return {
                "attempted": True,
                "renewed": False,
                "reason": "renew_failed",
                "error": str(exc),
                "trigger": reason,
                "settings": cfg.dhan,
            }


_health_cache: tuple[float, dict[str, Any]] | None = None
_HEALTH_CACHE_SEC = 45.0
_AUTO_RENEW_LOCK = threading.Lock()
_LAST_AUTO_RENEW_AT = 0.0
_AUTO_RENEW_STATUS: dict[str, Any] = {
    "enabled": True,
    "last_attempt_at": None,
    "last_success_at": None,
    "last_error": None,
    "last_trigger": None,
}


def token_renew_status(settings: DhanSettings | None = None) -> dict[str, Any]:
    """Latest auto-renew telemetry for dashboard visibility."""
    from index_ai.config import settings as load_settings
    from index_ai.token_scheduler import schedule_status

    cfg = settings or load_settings().dhan
    eligibility = token_renew_eligibility(cfg) if cfg.ready else {}
    return {
        **_AUTO_RENEW_STATUS,
        "enabled": _auto_renew_enabled(),
        "poll_seconds": _auto_renew_poll_seconds(),
        "threshold_seconds": _auto_renew_threshold_seconds(),
        "totp_auto_login_configured": totp_credentials_configured(),
        "totp_note": (
            "PIN+TOTP can mint a fresh WEB token when expired (Dhan Web → Setup TOTP)."
            if totp_credentials_configured()
            else None
        ),
        "schedule": schedule_status(),
        **eligibility,
    }


def clear_dhan_health_cache() -> None:
    global _health_cache
    _health_cache = None


def check_dhan_health(settings: DhanSettings, *, use_cache: bool = True) -> dict[str, Any]:
    """Profile, data subscription, and a small intraday chart probe."""
    global _health_cache
    if use_cache and _health_cache is not None:
        cached_at, cached = _health_cache
        if time.monotonic() - cached_at < _HEALTH_CACHE_SEC:
            return cached

    def _done(payload: dict[str, Any]) -> dict[str, Any]:
        global _health_cache
        if use_cache:
            _health_cache = (time.monotonic(), payload)
        return payload

    issues: list[str] = []
    actions: list[str] = []
    profile: dict[str, Any] = {}

    if not settings.ready:
        actions = [_web_token_action()]
        if totp_credentials_configured():
            actions.insert(
                0,
                "DHAN_PIN + DHAN_TOTP_SECRET are set — restart server to auto-mint token, "
                "or POST /api/auth/totp-login.",
            )
        return _done(
            {
                "ok": False,
                "charts_ok": False,
                "issues": [_missing_token_message(settings)],
                "actions": actions,
            }
        )

    reconcile_env_with_jwt()
    jwt_status = jwt_token_status(settings.access_token)
    if jwt_status.get("is_jwt") and jwt_status.get("expired"):
        issues.append(f"Access token expired at {jwt_status.get('expires_ist')}.")
        refreshed = auto_refresh_dhan_token(settings, force=True, reason="health_expired")
        if refreshed.get("renewed"):
            settings = refreshed.get("settings") or settings
            jwt_status = jwt_token_status(settings.access_token)
        else:
            actions.append(_web_token_action())
            if totp_credentials_configured():
                actions.append(
                    "TOTP auto-login is configured — click Renew token or restart the server."
                )
            eligibility = token_renew_eligibility(settings)
            if eligibility.get("reason"):
                actions.append(str(eligibility["reason"]))
            elif _AUTO_RENEW_STATUS.get("last_error"):
                actions.append(f"Auto-renew: {_AUTO_RENEW_STATUS['last_error']}")
            return _done(
                {
                    "ok": False,
                    "token_ok": False,
                    "charts_ok": False,
                    "issues": issues,
                    "actions": actions,
                    "profile": profile,
                    "jwt": jwt_status,
                    "token_renew": token_renew_status(settings),
                }
            )

    if jwt_status.get("is_jwt") and not jwt_status.get("expired"):
        refreshed = auto_refresh_dhan_token(settings, reason="health_preflight")
        if refreshed.get("renewed"):
            settings = refreshed.get("settings") or settings
            jwt_status = jwt_token_status(settings.access_token)

    try:
        probe = probe_access_token(settings)
        profile = dict(probe.get("profile") or {})
        charts_ok = bool(probe.get("charts_ok"))
        charts_error = probe.get("charts_error")
        if not probe.get("profile_ok"):
            actions.append(
                "Profile API unavailable (Dhan quirk) — token verified via option-chain instead."
            )
    except Exception as exc:
        issues.append(f"Dhan token check failed: {exc}")
        actions.append(_web_token_action())
        actions.append(
            "Ensure DHAN_CLIENT_ID matches your Dhan account. Try OAuth: Create Login Link."
        )
        return _done(
            {
                "ok": False,
                "charts_ok": False,
                "token_ok": False,
                "issues": issues,
                "actions": actions,
                "profile": profile,
                "jwt": jwt_status,
                "oauth_troubleshooting": _oauth_troubleshooting(),
            }
        )

    profile_cid = str(profile.get("dhanClientId") or "").strip()
    if profile_cid and profile_cid != settings.client_id:
        update_env_values({"DHAN_CLIENT_ID": profile_cid})
        actions.append(f"Updated DHAN_CLIENT_ID in .env to {profile_cid} (must match your token).")

    data_plan = str(profile.get("dataPlan") or "").strip()
    if data_plan and data_plan.lower() != "active":
        issues.append(f"Data API subscription is '{data_plan}' (charts need Active).")
        actions.append("On web.dhan.co → My Profile → Access DhanHQ APIs → enable Data API.")

    from index_ai.market_clock import format_ist_display

    raw_validity = profile.get("tokenValidity")
    token_validity = (
        format_ist_display(str(raw_validity)) if raw_validity else None
    )

    if not charts_ok and charts_error:
        err = charts_error.lower()
        if "806" in charts_error:
            issues.append("Data API not subscribed (HTTP 806).")
            actions.append("Subscribe to Data API on web.dhan.co → Access DhanHQ APIs.")
        elif "401" in charts_error or "808" in charts_error or "809" in charts_error:
            issues.append("Access token rejected for intraday chart data.")
            actions.append(_web_token_action())
        elif "906" in charts_error or "invalid token" in err:
            issues.append(
                "Intraday charts unavailable (Dhan DH-906). This often means Data API is not active on your account."
            )
            actions.append(
                "On web.dhan.co → My Profile → Access DhanHQ APIs → enable/subscribe to Data API, then Verify again."
            )
        else:
            issues.append(f"Chart probe failed: {charts_error}")

    token_ok = True
    ok = token_ok and not issues and charts_ok
    return _done(
        {
            "ok": ok,
            "token_ok": token_ok,
            "charts_ok": charts_ok,
            "charts_error": charts_error,
            "issues": issues,
            "actions": actions,
            "profile": profile,
            "token_validity": token_validity,
            "data_plan": data_plan or None,
            "client_id": profile_cid or settings.client_id,
            "jwt": jwt_status,
            "token_renew": token_renew_status(settings),
        }
    )


def _web_token_action() -> str:
    return (
        "web.dhan.co → My Profile → Access DhanHQ APIs → Generate Access Token → "
        "paste eyJ… JWT below → Save Token (works even when OAuth fails)."
    )


def _missing_token_message(settings: DhanSettings) -> str:
    if not settings.app_credentials_ready:
        return "Set DHAN_API_KEY and DHAN_API_SECRET in .env."
    if not settings.client_id:
        return "Set DHAN_CLIENT_ID in .env."
    return "Complete Dhan login (steps 1–3) and save tokenId to .env."


def auth_setup_checklist(settings: DhanSettings) -> dict[str, Any]:
    from index_ai.market_clock import format_ist_display

    jwt_status = jwt_token_status(settings.access_token) if settings.access_token else {}
    oauth_state = _load_oauth_state()
    pending_consent = str(oauth_state.get("consentAppId") or "").strip() or None
    return {
        "api_key_set": bool(settings.api_key),
        "api_secret_set": bool(settings.api_secret),
        "client_id_set": bool(settings.client_id),
        "client_id_numeric": bool(str(settings.client_id or "").isdigit()),
        "access_token_set": bool(settings.access_token),
        "token_expiry": format_ist_display(settings.token_expiry)
        if settings.token_expiry
        else None,
        "ready": settings.ready,
        "jwt": jwt_status,
        "can_generate_consent": settings.can_generate_consent,
        "auth_base_url": settings.auth_base_url,
        "oauth_redirect_urls": oauth_redirect_urls(),
        "pending_consent_app_id": pending_consent,
        "consent_link_stale": _consent_link_stale() if pending_consent else False,
        "oauth_troubleshooting": _oauth_troubleshooting(),
        "web_token_steps": _web_token_action(),
        "totp_auto_login_configured": totp_credentials_configured(),
        "totp_steps": (
            "web.dhan.co → Access DhanHQ APIs → Setup TOTP → add DHAN_PIN + DHAN_TOTP_SECRET to .env"
            if totp_credentials_configured()
            else None
        ),
        "missing": [
            label
            for label, ok in (
                ("DHAN_API_KEY", bool(settings.api_key)),
                ("DHAN_API_SECRET", bool(settings.api_secret)),
                ("DHAN_CLIENT_ID", bool(settings.client_id)),
                ("DHAN_ACCESS_TOKEN (run login flow)", bool(settings.access_token)),
            )
            if not ok
        ],
    }
