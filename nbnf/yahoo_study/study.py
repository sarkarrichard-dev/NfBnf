from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

from nbnf.market_yfinance import _normalize_ohlcv, history

# Free Yahoo/yfinance does not expose full 5 years of 1-minute bars. These attempts
# maximize coverage; whatever returns is best-effort (see yahoo_limits in output).
_INTRADAY_TRIES: tuple[tuple[str, str], ...] = (
    ("2m", "5d"),
    ("5m", "60d"),
    ("15m", "60d"),
    ("30m", "60d"),
    ("60m", "730d"),
    ("1h", "730d"),
)


def _summarize_ohlc(df: pd.DataFrame, *, annualize_vol: bool = True) -> dict[str, Any]:
    if df is None or df.empty:
        return {"bars": 0}
    d = df.dropna(subset=["close"]).copy()
    if d.empty:
        return {"bars": 0}
    c = d["close"].astype(float)
    r = c.pct_change()
    first_d = d["date"].iloc[0]
    last_d = d["date"].iloc[-1]
    tot = float(c.iloc[-1] / c.iloc[0] - 1) if len(c) > 1 else None
    vol = None
    if annualize_vol and len(r) > 5:
        vol = float(r.std() * (252**0.5))
    return {
        "bars": int(len(d)),
        "first_date": str(first_d),
        "last_date": str(last_d),
        "last_close": float(c.iloc[-1]),
        "window_total_return": tot,
        "window_ann_vol_proxy": vol,
    }


def _intraday_slices(symbol: str) -> list[dict[str, Any]]:
    t = yf.Ticker(symbol)
    out: list[dict[str, Any]] = []
    for interval, period in _INTRADAY_TRIES:
        row: dict[str, Any] = {"interval": interval, "period": period}
        try:
            raw = t.history(period=period, interval=interval, auto_adjust=False, prepost=False)
            df = _normalize_ohlcv(raw)
            row["summary"] = _summarize_ohlc(df, annualize_vol=False)
            row["error"] = None
        except Exception as e:
            row["summary"] = {"bars": 0}
            row["error"] = str(e)
        out.append(row)
    return out


def _underlying_spot(symbol: str) -> float | None:
    try:
        d = history(symbol, period="10d", interval="1d")
        d = d.dropna(subset=["close"])
        if d.empty:
            return None
        return float(d["close"].iloc[-1])
    except Exception:
        return None


def _options_snapshot(symbol: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "available": False,
        "expiries": [],
        "front_expiry": None,
        "note": None,
    }
    try:
        t = yf.Ticker(symbol)
        exps = list(t.options) if t.options is not None else []
        if not exps:
            out["note"] = "No option expiries returned (illiquid or unsupported on Yahoo)."
            return out
        out["expiries"] = exps[:8]
        out["front_expiry"] = exps[0]
        spot = _underlying_spot(symbol)
        chain = t.option_chain(exps[0])
        calls = chain.calls
        puts = chain.puts
        if calls is None or calls.empty:
            out["note"] = "Empty calls table for front expiry."
            return out
        calls = calls.reset_index(drop=True)
        strikes = calls["strike"].astype(float)
        if spot is not None and len(strikes):
            j = int((strikes - spot).abs().to_numpy().argmin())
        else:
            j = len(strikes) // 2
        c_row = calls.iloc[j].to_dict()
        strike = float(c_row.get("strike") or 0)
        p_row = None
        if puts is not None and not puts.empty:
            mp = puts[puts["strike"].astype(float) == strike]
            if not mp.empty:
                p_row = mp.iloc[0].to_dict()

        def _pick(d: dict, keys: list[str]) -> dict[str, Any]:
            r: dict[str, Any] = {}
            for k in keys:
                v = d.get(k)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    r[k] = None
                else:
                    r[k] = float(v) if isinstance(v, (int, float)) else v
            return r

        keys = ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "volume", "openInterest"]
        out["available"] = True
        out["underlying_spot"] = spot
        out["atm_call"] = _pick(c_row, keys)
        out["atm_put"] = _pick(p_row, keys) if p_row else None
        c_oi = float(calls["openInterest"].fillna(0).sum()) if "openInterest" in calls else None
        p_oi = (
            float(puts["openInterest"].fillna(0).sum())
            if puts is not None and not puts.empty and "openInterest" in puts
            else None
        )
        out["put_call_oi_ratio"] = (p_oi / c_oi) if c_oi and p_oi and c_oi > 0 else None
    except Exception as e:
        out["note"] = str(e)
    return out


def _text_block(
    daily_summary: dict[str, Any],
    daily_err: str | None,
    intraday: list[dict[str, Any]],
    options: dict[str, Any],
) -> str:
    lines = ["Yahoo deep study (automated pull)"]
    if daily_err:
        lines.append(f"Daily 5y ERROR: {daily_err}")
    else:
        lines.append(
            f"Daily 5y: bars={daily_summary.get('bars')} "
            f"last={daily_summary.get('last_close')} "
            f"5y total return (approx)={daily_summary.get('window_total_return')}"
        )
    ok_iv = [x for x in intraday if x.get("summary", {}).get("bars", 0) > 0]
    if ok_iv:
        lines.append("Intraday windows (Yahoo caps length; use for intraday context only):")
        for x in ok_iv[:6]:
            s = x["summary"]
            lines.append(
                f"  {x['interval']} / {x['period']}: bars={s.get('bars')} "
                f"from {s.get('first_date')} to {s.get('last_date')}"
            )
    else:
        lines.append("Intraday: no slices returned (limits or symbol).")
    if options.get("available"):
        lines.append(
            f"Options front expiry {options.get('front_expiry')}: "
            f"ATM call OI={options.get('atm_call', {}).get('openInterest')} "
            f"put/call OI ratio={options.get('put_call_oi_ratio')}"
        )
    else:
        lines.append(f"Options: not available — {options.get('note') or 'n/a'}")
    return "\n".join(lines)


def yahoo_deep_study(symbol: str) -> dict[str, Any]:
    """
    Pull 5y daily, best-effort intraday grid, and front-expiry options snapshot.
    Intended to run on each analyze/sweep before Dhan wiring.
    """
    sym = symbol.strip()
    yahoo_limits = (
        "Yahoo Finance limits intraday depth (e.g. 1m ~ days, 5m ~ weeks-months). "
        "5y continuous 1m data is not available from this API; we stack multiple windows instead."
    )
    daily_err: str | None = None
    try:
        daily_df = history(sym, period="5y", interval="1d")
        daily_summary = _summarize_ohlc(daily_df)
    except Exception as e:
        daily_df = pd.DataFrame()
        daily_summary = {"bars": 0}
        daily_err = str(e)

    intraday = _intraday_slices(sym)
    options = _options_snapshot(sym)
    text = _text_block(daily_summary, daily_err, intraday, options)

    return {
        "symbol": sym,
        "yahoo_limits": yahoo_limits,
        "daily_5y": {"summary": daily_summary, "error": daily_err},
        "intraday": intraday,
        "options": options,
        "text_block": text,
    }
