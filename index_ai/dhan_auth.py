from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from index_ai.config import DhanSettings, update_env_values

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


def generate_consent(settings: DhanSettings) -> dict[str, Any]:
    if not settings.client_id:
        raise RuntimeError(
            "Add your numeric Dhan Client ID to DHAN_CLIENT_ID in .env first "
            "(web.dhan.co → profile; not the API key)."
        )
    url = f"{settings.auth_base_url}/app/generate-consent"
    with httpx.Client(timeout=30) as client:
        response = client.post(
            url,
            params={"client_id": settings.client_id},
            headers=_auth_headers(settings),
        )
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "generate-consent (step 1)")
        data = _parse_json_response(response)

    _ensure_success_payload(data, "generate-consent (step 1)")
    consent_id = data.get("consentAppId")
    if not consent_id:
        raise RuntimeError(f"Dhan generate-consent did not return consentAppId: {data}")

    login_url = f"{settings.auth_base_url}/login/consentApp-login?consentAppId={consent_id}"
    return {
        "status": "success",
        "consentAppId": consent_id,
        "consentAppStatus": data.get("consentAppStatus"),
        "login_url": login_url,
        "client_id_used": settings.client_id,
        "instructions": (
            "1. Open login_url immediately (link expires in a few minutes).\n"
            "2. Complete Dhan login + 2FA in the same browser.\n"
            "3. After redirect, copy only tokenId=... from the address bar (UUID, not consentAppId).\n"
            "4. Paste tokenId below and click Save Token.\n\n"
            "If Dhan shows 'Details not found': your DHAN_CLIENT_ID or API key/redirect URL "
            "does not match the Dhan account — use Method A (Dhan Web token) instead."
        ),
        "oauth_troubleshooting": _oauth_troubleshooting(),
    }


def _oauth_troubleshooting() -> str:
    return (
        "OAuth 'Details not found' usually means:\n"
        "• DHAN_CLIENT_ID in .env is not your numeric Dhan Client ID for this API key.\n"
        "• API key was created on a different Dhan account.\n"
        "• Redirect URL on the API key does not match the browser redirect (e.g. http://127.0.0.1:3000/).\n"
        "• Login link was opened too late (create a fresh link).\n"
        "Recommended: skip OAuth — on web.dhan.co → My Profile → Access DhanHQ APIs → "
        "Generate Access Token → paste the eyJ… JWT here → Save Token."
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

    clear_dhan_health_cache()
    update_env_values(
        {
            "DHAN_CLIENT_ID": client_id,
            "DHAN_ACCESS_TOKEN": token,
            "DHAN_TOKEN_EXPIRY": expiry,
        }
    )
    clear_dhan_health_cache()
    from index_ai.config import settings as load_settings

    fresh = load_settings().dhan
    try:
        verify_access_token(fresh)
    except Exception as exc:
        raise RuntimeError(
            f"Token saved to .env but Dhan rejected it: {exc} "
            "Generate a fresh access token on web.dhan.co (old JWTs are invalidated when you generate a new one)."
        ) from exc
    return {
        "status": "saved",
        "source": "access_token_jwt",
        "dhanClientId": client_id,
        "expiryTime": expiry or None,
        "message": "Access token verified with Dhan /profile and saved to .env.",
    }


def save_token_from_user_input(settings: DhanSettings, raw: str) -> dict[str, Any]:
    """Accept OAuth tokenId (UUID) or a ready-made access token JWT."""
    text = normalize_access_token(raw)
    if not text:
        raise RuntimeError("Paste tokenId from the redirect URL, or paste your Dhan access token JWT.")

    normalized = normalize_token_id(text)
    if looks_like_jwt(normalized) or looks_like_jwt(text):
        return save_access_token_direct(settings, normalized if looks_like_jwt(normalized) else text)

    candidate = normalized or text
    if looks_like_jwt(candidate):
        return save_access_token_direct(settings, candidate)

    if not _UUID_TOKEN_RE.match(candidate):
        raise RuntimeError(
            "Unrecognized value. After browser login, paste the short tokenId (UUID) from the redirect URL. "
            "If Dhan gave you a long eyJ... JWT instead, paste that whole string — do not call it through "
            "consumeApp-consent."
        )
    return consume_consent(settings, candidate)


def consume_consent(settings: DhanSettings, token_id: str) -> dict[str, Any]:
    normalized = normalize_token_id(token_id) or (token_id or "").strip()
    if looks_like_jwt(normalized):
        return save_access_token_direct(settings, normalized)
    if not normalized:
        raise RuntimeError(
            "Paste tokenId from Dhan's redirect URL (query param tokenId=...), not the consentAppId."
        )
    url = f"{settings.auth_base_url}/app/consumeApp-consent"
    with httpx.Client(timeout=30) as client:
        response = client.get(
            url,
            params={"tokenId": normalized},
            headers=_auth_headers(settings),
        )
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "consumeApp-consent (step 3)")
        data = _parse_json_response(response)

    access_token = str(data.get("accessToken") or "")
    client_id = str(data.get("dhanClientId") or settings.client_id)
    expiry = str(data.get("expiryTime") or "")
    if not access_token:
        raise RuntimeError(f"Dhan consume-consent did not return accessToken: {data}")

    update_env_values(
        {
            "DHAN_CLIENT_ID": client_id,
            "DHAN_ACCESS_TOKEN": access_token,
            "DHAN_TOKEN_EXPIRY": expiry,
        }
    )
    return {
        "status": "saved",
        "dhanClientId": client_id,
        "dhanClientName": data.get("dhanClientName"),
        "expiryTime": expiry,
        "message": "Access token saved to .env. Dhan status should show Ready after refresh.",
    }


def _profile_headers(settings: DhanSettings) -> dict[str, str]:
    # Dhan docs: profile uses access-token only. A wrong client-id causes DH-901 / 401.
    return {"Accept": "application/json", "access-token": settings.access_token}


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
        expires_ist = exp_dt.strftime("%d %b %Y, %H:%M IST")
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


def verify_access_token(settings: DhanSettings) -> dict[str, Any]:
    """Call Dhan /profile to confirm the stored access token works."""
    if not settings.ready:
        raise RuntimeError(_missing_token_message(settings))
    url = f"{settings.api_base_url}/profile"
    with httpx.Client(timeout=20) as client:
        response = client.get(url, headers=_profile_headers(settings))
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "profile")
        data = _parse_json_response(response)
    profile = data.get("data") if isinstance(data.get("data"), dict) else data
    return {"ok": True, "profile": profile}


def renew_access_token(settings: DhanSettings) -> dict[str, Any]:
    """Extend token validity (Dhan Web–issued tokens only)."""
    if not settings.ready:
        raise RuntimeError(_missing_token_message(settings))
    url = f"{settings.api_base_url}/RenewToken"
    with httpx.Client(timeout=20) as client:
        response = client.get(
            url,
            headers={
                "Accept": "application/json",
                "access-token": settings.access_token,
                "dhanClientId": settings.client_id,
            },
        )
        if response.status_code >= 400:
            _raise_dhan_http_error(response, "RenewToken")
        data = _parse_json_response(response)
    token = str(data.get("accessToken") or data.get("access_token") or settings.access_token)
    client_id = str(data.get("dhanClientId") or settings.client_id)
    expiry = str(data.get("expiryTime") or "")
    update_env_values(
        {
            "DHAN_CLIENT_ID": client_id,
            "DHAN_ACCESS_TOKEN": token,
            "DHAN_TOKEN_EXPIRY": expiry,
        }
    )
    return {
        "status": "renewed",
        "dhanClientId": client_id,
        "expiryTime": expiry or None,
        "message": "Token renewed for another 24 hours.",
    }


_health_cache: tuple[float, dict[str, Any]] | None = None
_HEALTH_CACHE_SEC = 45.0


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
        return _done(
            {
                "ok": False,
                "charts_ok": False,
                "issues": [_missing_token_message(settings)],
                "actions": [_web_token_action()],
            }
        )

    reconcile_env_with_jwt()
    jwt_status = jwt_token_status(settings.access_token)
    if jwt_status.get("is_jwt") and jwt_status.get("expired"):
        issues.append(f"Access token expired at {jwt_status.get('expires_ist')}.")
        actions.append(_web_token_action())
        return _done(
            {
                "ok": False,
                "charts_ok": False,
                "issues": issues,
                "actions": actions,
                "profile": profile,
                "jwt": jwt_status,
            }
        )

    try:
        verified = verify_access_token(settings)
        profile = dict(verified.get("profile") or {})
    except Exception as exc:
        issues.append(f"Profile check failed: {exc}")
        actions.append(_web_token_action())
        actions.append(
            "If using API login: fix DHAN_CLIENT_ID + API key, or see oauth_troubleshooting after Create Login Link."
        )
        return _done(
            {
                "ok": False,
                "charts_ok": False,
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

    token_validity = profile.get("tokenValidity")
    charts_ok = False
    charts_error: str | None = None

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
            interval="5",
        )
        charts_ok = True
    except Exception as exc:
        charts_error = str(exc)
        if "401" in charts_error or "808" in charts_error or "809" in charts_error:
            issues.append("Access token rejected for chart data (HTTP 401).")
            actions.append(
                "Paste a fresh tokenId from today's Dhan login, or click Renew Token if you used Dhan Web token."
            )
        elif "806" in charts_error:
            issues.append("Data API not subscribed (HTTP 806).")
            actions.append("Subscribe to Data API on Dhan Web.")
        else:
            issues.append(f"Chart probe failed: {charts_error}")

    ok = not issues and charts_ok
    return _done(
        {
            "ok": ok,
            "charts_ok": charts_ok,
            "charts_error": charts_error,
            "issues": issues,
            "actions": actions,
            "profile": profile,
            "token_validity": token_validity,
            "data_plan": data_plan or None,
            "client_id": profile_cid or settings.client_id,
            "jwt": jwt_status,
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
    jwt_status = jwt_token_status(settings.access_token) if settings.access_token else {}
    return {
        "api_key_set": bool(settings.api_key),
        "api_secret_set": bool(settings.api_secret),
        "client_id_set": bool(settings.client_id),
        "access_token_set": bool(settings.access_token),
        "token_expiry": settings.token_expiry,
        "ready": settings.ready,
        "jwt": jwt_status,
        "can_generate_consent": settings.can_generate_consent,
        "auth_base_url": settings.auth_base_url,
        "oauth_troubleshooting": _oauth_troubleshooting(),
        "web_token_steps": _web_token_action(),
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
