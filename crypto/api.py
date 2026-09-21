"""Crypto section HTTP surface — mounted at /api/crypto by index_ai.server.

All handlers are plain ``def`` so Starlette runs them in a threadpool: they do
blocking I/O (httpx to Delta, an ``.env`` write) which must never touch the
event loop (CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from index_ai.admin_auth import require_admin_secret

from crypto import charges, executor, journal, lanes, sizing
from crypto._util import num
from crypto.ml import gate as ml_gate
from crypto.ml import model as ml_model
from crypto.config import crypto_settings
from crypto.delta import market_data, products
from crypto.delta import client as delta_client
from crypto.delta.client import DeltaClient, DeltaError
from crypto.live import (
    CRYPTO_ARM_PHRASE,
    arm_crypto_live,
    disarm_crypto_live,
    set_crypto_mode,
)
from crypto.session import crypto_day, ny_session_date

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crypto", tags=["crypto"])


def _mask(secret: str) -> str:
    s = secret or ""
    if len(s) <= 8:
        return "set" if s else ""
    return f"{s[:4]}…{s[-2:]}"


@router.get("/status", include_in_schema=False)
def crypto_status() -> dict:
    s = crypto_settings()
    return {
        "credentials_ready": s.credentials_ready,
        "api_key_preview": _mask(s.api_key),
        "base_url": s.base_url,
        "lanes": {
            "ny_n_break": s.ny_nbreak_enabled,
            "ichimoku": s.ichimoku_enabled,
            "ak_roxx_pro": s.ak_roxx_enabled,
            "cpr_trend": s.cpr_trend_enabled,
            "rsi_adx_trend": s.rsi_adx_trend_enabled,
        },
        "sizing": {
            "margin_per_position_usd": s.margin_per_position_usd,
            "deploy_cap_usd": s.deploy_usd,
            "leverage": s.leverage,
            "max_concurrent": s.max_concurrent,  # per strategy
            "max_open_total": s.max_open_total,  # 0 = unlimited
            "max_hold_days": s.max_hold_days,
            "paper_bankroll_usd": s.paper_bankroll_usd,
        },
        "lane_session_ist": {"start": s.session_start, "end": s.session_end},
        "session_ist": {"start": s.ny_start, "end": s.ny_end},
        "nbreak_allround": s.nbreak_allround,
        "ichimoku_tf": s.ichimoku_tf,
        "trailing": {
            "stop_pnl_pct": s.stop_pnl_pct,
            "ratchet_step_pnl_pct": s.ratchet_step_pnl_pct,
            "tp_trigger_pnl_pct": s.tp_trigger_pnl_pct,
            "peak_trail_pnl_pct": s.peak_trail_pnl_pct,
        },
        "symbols": list(s.symbols),
        "available_symbols": products.available_symbols(),
        "half_spread_bps": {
            sym: {
                "measured": charges.measured_half_spread_bps(sym),
                "fallback": charges._HALF_SPREAD_BPS.get(sym, 2.0),
            }
            for sym in s.symbols
        },
        # --- live execution (Phase 4) ---
        "trading_mode": s.trading_mode,
        "live_armed": s.live_armed,
        "live_orders_enabled": s.live_orders_enabled,
        "arm_phrase": CRYPTO_ARM_PHRASE,
        "egress": _egress(s),
        "kill_switch": _kill_switch_state(s),
        "ml": {**ml_gate.status(), "tuning": _tuning_status()},
        "events": lanes.recent_events(),
    }


def _tuning_status() -> dict:
    try:
        from crypto.ml.optimize import status as _s

        return _s()
    except Exception:
        return {}


def _egress(s) -> dict:
    """IPv4 + IPv6 this machine presents, plus the address Delta last rejected.
    Delta traffic is pinned to IPv4 (``CRYPTO_FORCE_IPV4``), so whitelist the
    IPv4 on the Delta key."""
    ips: dict = {"ipv4": None, "ipv6": None}
    try:
        from index_ai.dhan_network import fetch_public_ips

        ips = fetch_public_ips()
    except Exception:
        pass
    blocked = dict(delta_client.LAST_IP_BLOCK)
    return {
        "ipv4": ips.get("ipv4"),
        "ipv6": ips.get("ipv6"),
        "forcing_ipv4": s.force_ipv4,
        "delta_sees_ip": blocked.get("ip") or None,
        "whitelist_ok": not blocked.get("ip"),
    }


def _kill_switch_state(s) -> dict:
    tripped, why = executor.kill_switch(s)
    rows = executor._today_live_rows()
    return {
        "tripped": tripped,
        "reason": why,
        "today_live_net_usd": round(sum(float(r.get("pnl_usd") or 0.0) for r in rows), 2),
        "today_live_trades": len(rows),
        "max_daily_loss_usd": s.max_daily_loss_usd,
        "max_consec_losses": s.max_consec_losses,
    }


def _health_blocking() -> dict:
    s = crypto_settings()
    out: dict = {
        "connected": False,
        "credentials_ready": s.credentials_ready,
        "base_url": s.base_url,
        "wallet_usd": None,
        "wallet_inr": None,
        "quotes": {},
        "errors": [],
    }
    client = DeltaClient(s)

    for sym in s.symbols:
        try:
            tk = market_data.ticker(sym, client=client)
            q = tk.get("quotes") or {}
            out["quotes"][sym] = {
                "mark": _num(tk.get("mark_price")),
                "bid": _num(q.get("best_bid")),
                "ask": _num(q.get("best_ask")),
            }
        except DeltaError as exc:
            out["errors"].append(f"{sym} quote: {exc}")

    if not s.credentials_ready:
        out["errors"].append("Delta API key/secret not set — add them in the Crypto Setup panel.")
        return out

    try:
        bal = client.wallet()
        out["wallet_usd"] = round(sum(_num(w.get("balance")) or 0.0 for w in bal), 2)
        out["wallet_inr"] = round(sum(_num(w.get("balance_inr")) or 0.0 for w in bal), 2)
        out["connected"] = True
    except DeltaError as exc:
        out["errors"].append(f"wallet: {exc}")
    return out


def _num(v) -> float | None:
    return num(v, None)


@router.get("/health", include_in_schema=False)
def crypto_health() -> dict:
    return _health_blocking()


@router.get("/lots", include_in_schema=False)
def crypto_lots() -> dict:
    """Per selected symbol: what the configured $ margin budget buys — one
    contract's cost (coin size, notional, margin in $ and ₹), how many
    contracts that budget covers at the current mark, and the $ actually
    deployed at that count. Uses Delta's own margin_required when keys are
    set, else the local estimate. Best-effort; a symbol with no mark yields
    null fields, never a fake 0."""
    s = crypto_settings()
    client = DeltaClient(s)
    fx = _usd_inr(client, s)
    try:
        contracts = products.all_contracts(client)
    except DeltaError:
        contracts = {}
    rows: list[dict] = []
    for sym in s.symbols:
        c = contracts.get(sym)
        if c is None:
            rows.append(
                {
                    "symbol": sym,
                    "coin_per_lot": None,
                    "notional_per_lot_usd": None,
                    "margin_per_lot_usd": None,
                    "margin_per_lot_inr": None,
                    "lots": None,
                    "deployed_usd": None,
                    "note": "not a live Delta perp",
                }
            )
            continue
        try:
            mark = _num(market_data.ticker(sym, client=client).get("mark_price")) or 0.0
        except Exception:
            mark = 0.0
        econ = sizing.lot_economics(c, mark, leverage=s.leverage, fx_usdinr=fx)
        if s.credentials_ready and mark > 0:
            try:
                mr = client.margin_required(c.product_id, 1, "buy")
                per_lot = _num(mr.get("initial_margin")) or _num(mr.get("required_margin"))
                if per_lot:
                    econ["margin_per_lot_usd"] = round(per_lot, 2)
                    econ["margin_per_lot_inr"] = round(per_lot * fx, 0) if fx else None
                    econ["source"] = "delta"
            except DeltaError:
                pass
        per_lot_usd = econ.get("margin_per_lot_usd")
        if per_lot_usd:
            lots = sizing.lots_from_margin_budget(
                c, mark, margin_usd=s.margin_per_position_usd, leverage=s.leverage
            )
            econ["lots"] = lots
            econ["deployed_usd"] = round(lots * per_lot_usd, 2)
        else:
            econ["lots"] = None
            econ["deployed_usd"] = None
        rows.append({"symbol": sym, **econ})
    return {
        "margin_per_position_usd": s.margin_per_position_usd,
        "leverage": s.leverage,
        "deploy_cap_usd": s.deploy_usd,
        "fx_usdinr": round(fx, 4),
        "table": rows,
    }


@router.get("/contracts", include_in_schema=False)
def crypto_contracts() -> dict:
    try:
        cs = products.all_contracts()
    except DeltaError as exc:
        raise HTTPException(502, f"Delta products fetch failed: {exc}") from exc
    return {
        sym: {
            "product_id": c.product_id,
            "contract_value": c.contract_value,
            "tick_size": c.tick_size,
            "min_size": c.min_size,
            "max_leverage": c.max_leverage,
            "usable": c.usable,
        }
        for sym, c in cs.items()
    }


def _usd_inr(client: DeltaClient, s) -> float:
    if s.credentials_ready:
        try:
            bal = client.wallet() or []
            usd = sum(_num(w.get("balance")) or 0.0 for w in bal if isinstance(w, dict))
            inr = sum(_num(w.get("balance_inr")) or 0.0 for w in bal if isinstance(w, dict))
            if usd > 0 and inr > 0:
                return inr / usd
        except DeltaError:
            pass
    return _num(os.getenv("CRYPTO_USDINR", "88")) or 88.0


@router.get("/positions", include_in_schema=False)
def crypto_positions() -> dict:
    st = journal.load_state()
    s = crypto_settings()
    client = DeltaClient(s)
    fx = _usd_inr(client, s)
    marks: dict[str, float] = {}
    open_pos: list[dict] = []
    open_pnl_usd = 0.0

    for k, v in st.items():
        if ":" not in k or not isinstance(v, dict) or not v.get("position"):
            continue
        p = dict(v["position"])
        sym = p.get("asset", "")
        if sym and sym not in marks:
            try:
                marks[sym] = _num(market_data.ticker(sym, client=client).get("mark_price")) or 0.0
            except Exception:
                marks[sym] = 0.0
        mark = marks.get(sym, 0.0)
        direction = 1 if p.get("side") == "long" else -1
        coins = float(p.get("size") or 0) * float(p.get("contract_value") or 0)
        if mark > 0 and coins > 0:
            gross = (mark - float(p.get("entry_price") or 0)) * direction * coins
            notional = float(p.get("notional_usd") or 0)
            # Same deductions a real close applies (charges.round_trip_cost_usd,
            # funding_cost_usd) — a position can be gross-positive and still
            # net-negative once accrued funding is counted, and until this the
            # dashboard never showed that: it displayed pure price movement,
            # so a close could land far worse than what was on screen a moment
            # before (confirmed 2026-09-21 against a real "closed all, screen
            # said profit, journal said loss" report — funding accrued the
            # whole hold and was invisible until the trade actually closed).
            try:
                cost = charges.round_trip_cost_usd(
                    notional,
                    sym,
                    mark,
                    float(p.get("size") or 0),
                    float(p.get("contract_value") or 0),
                )
            except Exception:
                cost = 0.0
            try:
                funding = charges.funding_cost_usd(
                    symbol=sym,
                    side=str(p.get("side") or "long"),
                    notional_usd=notional,
                    entry_time=p.get("entry_time"),
                    exit_time=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                funding = 0.0
            upnl = gross - cost - funding
            p["mark"] = round(mark, 2)
            p["unrealized_usd"] = round(upnl, 2)
            p["unrealized_inr"] = round(upnl * fx, 0)
            p["unrealized_pct"] = round(upnl / notional * 100.0, 2) if notional else None
            p["unrealized_gross_usd"] = round(gross, 2)
            p["accrued_cost_usd"] = round(cost + funding, 2)
            open_pnl_usd += upnl
        else:  # no live mark — show the position but not a fake $0 P&L
            p["mark"] = None
            p["unrealized_usd"] = None
            p["unrealized_inr"] = None
            p["unrealized_pct"] = None
        open_pos.append({"key": k, **p})

    live: list = []
    if s.credentials_ready:
        try:
            for lp in client.positions():
                sz = _num(lp.get("size")) or 0.0
                if sz:
                    live.append(
                        {
                            "symbol": lp.get("product_symbol"),
                            "size": sz,
                            "entry_price": _num(lp.get("entry_price")),
                            "unrealized_pnl": _num(lp.get("unrealized_pnl")),
                        }
                    )
        except DeltaError as exc:
            live = [{"error": str(exc)}]

    return {
        "paper": open_pos,
        "live": live,
        "open_unrealized_usd": round(open_pnl_usd, 2),
        "open_unrealized_inr": round(open_pnl_usd * fx, 0),
        "fx_usdinr": round(fx, 4),
    }


@router.post("/positions/close", include_in_schema=False)
def crypto_close_position(key: str = Body(..., embed=True)) -> dict:
    """Manual "Close" button on the dashboard — force-exit one open position
    now, at the current mark. `key` is the "{strategy}:{symbol}" id shown in
    /positions (e.g. "ak_roxx_pro:BNBUSD"). Plain def — threadpooled; does a
    live price fetch and, for a LIVE position, a real close order."""
    from crypto.lanes import close_position_manual

    return close_position_manual(key)


@router.post("/positions/close-all", include_in_schema=False)
def crypto_close_all_positions() -> dict:
    """Manual "Close all" button — force-exit every open position now, each at
    its own current mark. Same machinery as the single Close button, run once
    per open position; one failure doesn't stop the rest."""
    from crypto.lanes import close_all_positions_manual

    return close_all_positions_manual()


def _blocked_as_journal_row(r: dict) -> dict:
    """Shape a crypto_blocked.jsonl row into the same fields a real closed
    trade carries, so the dashboard's trade-log table can render it with no
    special-casing: no fill happened, so price/size/pnl are all null and the
    reason takes the Reason column real trades use for their exit reason."""
    at = r.get("opened_at") or r.get("closed_at")
    return {
        "exit_id": f"blocked:{r.get('asset')}:{at}",
        "strategy": r.get("strategy"),
        "asset": r.get("asset"),
        "mode": r.get("mode", "live"),
        "side": r.get("side"),
        "size": None,
        "entry_price": None,
        "exit_price": None,
        "opened_at": at,
        "entry_time": at,
        "exit_time": at,
        "closed_at": at,
        "pnl_inr": None,
        "pnl_usd": None,
        "exit_reason": r.get("reason") or "order not placed",
    }


@router.get("/journal", include_in_schema=False)
def crypto_journal(limit: int = 100) -> dict:
    n = max(1, min(500, limit))
    trades = journal.recent(n)
    blocked = [_blocked_as_journal_row(r) for r in journal.recent_blocked(n)]
    merged = sorted(
        trades + blocked, key=lambda r: str(r.get("opened_at") or r.get("entry_time") or "")
    )
    return {"trades": merged[-n:]}


@router.post("/ml/train", include_in_schema=False)
def crypto_ml_train(force: bool = Body(False, embed=True)) -> dict:
    """Retrain the crypto model from the journal. Plain def — threadpooled."""
    return ml_model.train(force=bool(force))


@router.get("/day-review", include_in_schema=False)
def crypto_day_review(refresh: bool = False) -> dict:
    """Today's crypto summary + advisory AI review. Plain def — threadpooled."""
    from crypto.day_review import build_crypto_review

    return build_crypto_review(refresh=bool(refresh))


@router.post("/ml/optimize", include_in_schema=False)
def crypto_ml_optimize() -> dict:
    """Walk-forward re-tune the video strategies' parameters. Slow — plain def,
    Starlette threadpools it; the dashboard button shows a spinner."""
    from crypto.ml.optimize import retune_all

    return retune_all()


@router.get("/day", include_in_schema=False)
def crypto_today() -> dict:
    s = crypto_settings()
    ny = ny_session_date(s.ny_start, s.ny_end)
    utc = crypto_day()

    def _sum(rows: list) -> dict:
        return {
            "trades": len(rows),
            "wins": sum(1 for r in rows if float(r.get("pnl_usd") or 0) > 0),
            "losses": sum(1 for r in rows if float(r.get("pnl_usd") or 0) < 0),
            "net_usd": round(sum(float(r.get("pnl_usd") or 0) for r in rows), 2),
            "net_inr": round(sum(float(r.get("pnl_inr") or 0) for r in rows), 0),
        }

    return {
        "ny_session_date": ny,
        "utc_date": utc,
        "ny_n_break": _sum(journal.day_rows(ny, strategy="ny_n_break")),
        "ichimoku": _sum(journal.day_rows(utc, strategy="ichimoku")),
    }


@router.post("/config", include_in_schema=False)
def set_config(
    margin_per_position_usd: float | None = Body(None, embed=True),
    deploy_cap_usd: float | None = Body(None, embed=True),
    deploy_usd: float | None = Body(None, embed=True),  # legacy alias for deploy_cap_usd
    max_concurrent: int | None = Body(None, embed=True),
    max_open_total: int | None = Body(None, embed=True),
    max_hold_days: int | None = Body(None, embed=True),
    ny_n_break_enabled: bool | None = Body(None, embed=True),
    ichimoku_enabled: bool | None = Body(None, embed=True),
    ak_roxx_enabled: bool | None = Body(None, embed=True),
    cpr_trend_enabled: bool | None = Body(None, embed=True),
    rsi_adx_trend_enabled: bool | None = Body(None, embed=True),
    nbreak_allround: bool | None = Body(None, embed=True),
    session_start: str | None = Body(None, embed=True),
    session_end: str | None = Body(None, embed=True),
    symbols: list[str] | None = Body(None, embed=True),
) -> dict:
    """Non-financial-in-paper knobs — plain write, no confirm (Delta keys are
    the only crypto setting that needs the money-path treatment)."""
    from index_ai.config import update_env_values

    values: dict[str, str] = {}
    if symbols is not None:
        want = [str(x).strip().upper() for x in symbols if str(x).strip()]
        listed = set(products.available_symbols())
        if not listed:
            raise HTTPException(502, "Delta contract master unavailable — try again shortly.")
        keep = [x for i, x in enumerate(want) if x not in want[:i] and x in listed]
        if not keep:
            raise HTTPException(400, "None of those symbols are live Delta perpetuals.")
        values["CRYPTO_SYMBOLS"] = ",".join(keep)
    if margin_per_position_usd is not None:
        values["CRYPTO_MARGIN_PER_POSITION_USD"] = str(max(1.0, float(margin_per_position_usd)))
    cap = deploy_cap_usd if deploy_cap_usd is not None else deploy_usd
    if cap is not None:
        values["CRYPTO_DEPLOY_USD"] = str(max(0.0, float(cap)))
    if max_concurrent is not None:
        values["CRYPTO_MAX_CONCURRENT"] = str(min(10, max(1, int(max_concurrent))))
    if max_open_total is not None:
        values["CRYPTO_MAX_OPEN_TOTAL"] = str(min(50, max(0, int(max_open_total))))
    if max_hold_days is not None:
        values["CRYPTO_MAX_HOLD_DAYS"] = str(min(7, max(1, int(max_hold_days))))
    if ny_n_break_enabled is not None:
        values["CRYPTO_NY_NBREAK_ENABLED"] = "true" if ny_n_break_enabled else "false"
    if ichimoku_enabled is not None:
        values["CRYPTO_ICHIMOKU_ENABLED"] = "true" if ichimoku_enabled else "false"
    if ak_roxx_enabled is not None:
        values["CRYPTO_AK_ROXX_ENABLED"] = "true" if ak_roxx_enabled else "false"
    if cpr_trend_enabled is not None:
        values["CRYPTO_CPR_TREND_ENABLED"] = "true" if cpr_trend_enabled else "false"
    if rsi_adx_trend_enabled is not None:
        values["CRYPTO_RSI_ADX_TREND_ENABLED"] = "true" if rsi_adx_trend_enabled else "false"
    if nbreak_allround is not None:
        values["CRYPTO_NBREAK_ALLROUND"] = "true" if nbreak_allround else "false"
    for name, raw in (("CRYPTO_SESSION_START", session_start), ("CRYPTO_SESSION_END", session_end)):
        if raw is None:
            continue
        try:
            hh, mm = str(raw).strip().split(":")
            assert 0 <= int(hh) < 24 and 0 <= int(mm) < 60
        except (ValueError, AssertionError):
            raise HTTPException(400, f"{name} must be 24h HH:MM.") from None
        values[name] = f"{int(hh):02d}:{int(mm):02d}"
    start_v = values.get("CRYPTO_SESSION_START")
    end_v = values.get("CRYPTO_SESSION_END")
    if start_v is not None and end_v is not None and start_v == end_v:
        raise HTTPException(400, "Session start and end must differ (equal = crypto never trades).")
    if not values:
        raise HTTPException(400, "No settings provided.")
    update_env_values(values)
    for k, v in values.items():
        os.environ[k] = v
    return {"saved": list(values.keys()), "status": crypto_status()}


@router.post("/mode", include_in_schema=False)
def crypto_mode(
    mode: str = Body(..., embed=True),
    _admin: None = Depends(require_admin_secret),
) -> dict:
    """PAPER | LIVE. LIVE alone places no orders — arming is a separate step.
    Switching to PAPER always disarms."""
    try:
        m = set_crypto_mode(mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    os.environ["CRYPTO_TRADING_MODE"] = m
    if m == "PAPER":
        os.environ["CRYPTO_ALLOW_LIVE"] = "false"
    _reset_reconcile_stamp()
    return {"trading_mode": m, "status": crypto_status()}


def _reset_reconcile_stamp() -> None:
    """Force a fresh position reconcile on the next live scan after any
    mode/arm change."""
    try:
        st = journal.load_state()
        if st.pop("_live_reconciled", None) is not None:
            journal.save_state(st)
    except OSError:
        pass


@router.post("/arm-live", include_in_schema=False)
def crypto_arm_live(
    confirm: str = Body("", embed=True),
    disarm: bool = Body(False, embed=True),
    _admin: None = Depends(require_admin_secret),
) -> dict:
    """Arm real Delta orders (needs the exact phrase) or disarm (one click)."""
    if disarm:
        disarm_crypto_live()
        os.environ["CRYPTO_ALLOW_LIVE"] = "false"
    else:
        try:
            arm_crypto_live(confirm)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        os.environ["CRYPTO_ALLOW_LIVE"] = "true"
    _reset_reconcile_stamp()
    s = crypto_settings()
    return {
        "live_orders_enabled": s.live_orders_enabled,
        "live_armed": s.live_armed,
        "confirm_phrase": CRYPTO_ARM_PHRASE,
        "status": crypto_status(),
    }


@router.post("/credentials", include_in_schema=False)
def set_credentials(
    api_key: str = Body(..., embed=True),
    api_secret: str = Body(..., embed=True),
    confirm: bool = Body(False, embed=True),
    _admin: None = Depends(require_admin_secret),
) -> dict:
    """Save Delta API credentials to .env. Money-path — requires confirm=true.

    These keys can place real orders once live execution ships, so they do NOT
    go through the generic feature-toggle endpoint — same treatment as the Dhan
    login.
    """
    if not confirm:
        raise HTTPException(400, "Set confirm=true to save Delta credentials.")
    key, sec = api_key.strip(), api_secret.strip()
    if not key or not sec:
        raise HTTPException(400, "Both api_key and api_secret are required.")

    from index_ai.config import update_env_values

    update_env_values({"DELTA_API_KEY": key, "DELTA_API_SECRET": sec})
    os.environ["DELTA_API_KEY"] = key
    os.environ["DELTA_API_SECRET"] = sec

    check = _health_blocking()
    return {
        "saved": True,
        "api_key_preview": _mask(key),
        "connected": check["connected"],
        "wallet_usd": check["wallet_usd"],
        "wallet_inr": check["wallet_inr"],
        "errors": check["errors"],
    }
