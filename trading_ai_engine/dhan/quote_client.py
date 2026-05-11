from __future__ import annotations

import json
import os
from typing import Any

from trading_ai_engine.dhan.config import load_dhan_config
from trading_ai_engine.http_client import post_json


def _ltp_map() -> dict[str, Any]:
    raw = (os.environ.get("TRADING_AI_DHAN_LTP_MAP") or "").strip()
    if not raw:
        return {}
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def _batch_for_symbol(symbol: str) -> dict[str, list[int]]:
    """
    Build Dhan ``POST /v2/marketfeed/ltp`` body segment → security_id lists.

    Env ``TRADING_AI_DHAN_LTP_MAP`` example::

        {"^NSEI": {"NSE_INDEX": [26009]}, "RELIANCE": {"NSE_EQ": [2885]}}

    Keys are **brain symbols** (Yahoo tickers or labels you analyze). Values are Dhan segment buckets.
    Segments: ``NSE_EQ``, ``NSE_FNO``, ``NSE_INDEX``, ``BSE_EQ``, … per Dhan docs.
    """
    m = _ltp_map()
    entry = m.get(symbol.strip())
    if entry is None:
        return {}
    batch: dict[str, list[int]] = {}
    if isinstance(entry, dict):
        for seg, ids in entry.items():
            if isinstance(ids, list):
                batch[str(seg)] = [int(x) for x in ids if str(x).isdigit() or isinstance(x, int)]
            elif isinstance(ids, int):
                batch[str(seg)] = [int(ids)]
    return batch


def fetch_dhan_ltp_snapshot(*, symbol: str) -> dict[str, Any]:
    """
    Call Dhan LTP API when ``DHAN_CLIENT_ID`` + ``DHAN_ACCESS_TOKEN`` are set and the symbol
    exists in ``TRADING_AI_DHAN_LTP_MAP``.
    """
    cfg = load_dhan_config()
    out: dict[str, Any] = {
        "provider": "DhanHQ",
        "credentials_ready": cfg.data_ready,
        "symbol": symbol,
        "batch": {},
        "raw": None,
        "digest": "",
        "error": None,
    }
    if not cfg.data_ready:
        out["digest"] = "Dhan LTP skipped: set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN."
        return out

    batch = _batch_for_symbol(symbol)
    out["batch"] = batch
    if not batch:
        out["digest"] = (
            f"Dhan LTP skipped: add an entry for {symbol!r} in TRADING_AI_DHAN_LTP_MAP "
            "(segment → security_id list). See .env.example."
        )
        return out

    url = f"{cfg.api_base_url.rstrip('/')}/marketfeed/ltp"
    headers = {
        "access-token": str(cfg.access_token),
        "client-id": str(cfg.client_id),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        raw = post_json(url, batch, headers=headers)
        out["raw"] = raw
        lines: list[str] = ["Dhan LTP snapshot (research):"]
        data = (raw or {}).get("data") or {}
        for seg, bucket in data.items():
            if not isinstance(bucket, dict):
                continue
            for sid, row in bucket.items():
                if isinstance(row, dict) and "last_price" in row:
                    lines.append(f"  {seg} {sid}: LTP={row.get('last_price')}")
        out["digest"] = "\n".join(lines)[:6000]
    except Exception as e:
        out["error"] = str(e)[:800]
        out["digest"] = f"Dhan LTP request failed: {out['error']}"
    return out


def dhan_quote_operator_status() -> dict[str, Any]:
    cfg = load_dhan_config()
    m = _ltp_map()
    return {
        "credentials_ready": cfg.data_ready,
        "mapped_symbols": sorted(m.keys()) if m else [],
        "api_base_url": cfg.api_base_url,
        "doc": "https://dhanhq.co/docs/v2/market-quote/",
    }
