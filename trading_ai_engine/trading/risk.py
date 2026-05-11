from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from math import floor
from typing import Any

from trading_ai_engine.trading.fno_instruments import fno_contract_context


@dataclass(frozen=True)
class RiskConfig:
    starting_equity: float = 1_000_000.0
    risk_per_trade_pct: float = 0.005
    max_position_pct: float = 0.10
    max_daily_loss_pct: float = 0.02
    max_trades_per_day: int = 10
    min_confidence: float = 0.35
    min_abs_score: float = 0.18
    default_stop_pct: float = 0.015
    reward_risk: float = 2.0
    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_risk_config() -> RiskConfig:
    return RiskConfig(
        starting_equity=_env_float("TRADING_AI_PAPER_STARTING_EQUITY", 1_000_000.0),
        risk_per_trade_pct=_env_float("TRADING_AI_RISK_PER_TRADE_PCT", 0.005),
        max_position_pct=_env_float("TRADING_AI_MAX_POSITION_PCT", 0.10),
        max_daily_loss_pct=_env_float("TRADING_AI_MAX_DAILY_LOSS_PCT", 0.02),
        max_trades_per_day=_env_int("TRADING_AI_MAX_TRADES_PER_DAY", 10),
        min_confidence=_env_float("TRADING_AI_MIN_TRADE_CONFIDENCE", 0.35),
        min_abs_score=_env_float("TRADING_AI_MIN_ABS_TRADE_SCORE", 0.18),
        default_stop_pct=_env_float("TRADING_AI_DEFAULT_STOP_PCT", 0.015),
        reward_risk=_env_float("TRADING_AI_REWARD_RISK", 2.0),
        paper_trading_enabled=os.environ.get("TRADING_AI_ENABLE_PAPER_TRADING", "true").lower()
        != "false",
        live_trading_enabled=os.environ.get("TRADING_AI_ENABLE_LIVE_TRADING", "").lower() == "true",
    )


def build_trade_plan(
    *,
    symbol: str,
    brain: dict[str, Any],
    metrics: dict[str, Any],
    risk: RiskConfig | None = None,
) -> dict[str, Any]:
    from trading_ai_engine.trading.paper_gates import kill_switch_active

    cfg = risk or load_risk_config()
    action = str(brain.get("action") or "neutral")
    score = float(brain.get("score") or 0.0)
    confidence = float(brain.get("confidence") or 0.0)
    close = metrics.get("close")
    vetoes: list[str] = []
    warnings: list[str] = []

    if close is None:
        vetoes.append("missing_close")
        close_f = 0.0
    else:
        close_f = float(close)
    if action not in ("bullish", "bearish"):
        vetoes.append("neutral_action")
    if abs(score) < cfg.min_abs_score:
        vetoes.append("score_below_threshold")
    if confidence < cfg.min_confidence:
        vetoes.append("confidence_below_threshold")
    if not cfg.paper_trading_enabled:
        vetoes.append("paper_trading_disabled")
    if kill_switch_active():
        vetoes.append("kill_switch_active")

    if not vetoes:
        from trading_ai_engine.ml.pattern_context import pattern_gate_blocks_plan

        if pattern_gate_blocks_plan(metrics, action):
            vetoes.append("pattern_win_rate_below_threshold")

    side = "flat"
    if action == "bullish":
        side = "long"
    elif action == "bearish":
        side = "short"

    ret_1d = abs(float(metrics.get("ret_1d") or 0.0))
    stop_pct = max(cfg.default_stop_pct, min(0.04, ret_1d * 1.5))
    if close_f <= 0:
        stop_pct = cfg.default_stop_pct

    risk_budget = cfg.starting_equity * cfg.risk_per_trade_pct
    max_notional = cfg.starting_equity * cfg.max_position_pct
    focus = str(metrics.get("market_focus") or "")
    fno_ctx = fno_contract_context(symbol, market_focus=focus)
    instrument = str(fno_ctx.get("instrument_type") or "equity")
    lot_size = int(fno_ctx.get("lot_size") or 1)

    qty = 0
    notional = 0.0
    risk_amount = 0.0
    stop_loss: float | None = None
    target: float | None = None
    direction = 1 if side == "long" else -1 if side == "short" else 0

    if instrument == "fno" and close_f > 0 and lot_size >= 1:
        risk_per_contract = lot_size * close_f * stop_pct
        max_lots_risk = floor(risk_budget / max(risk_per_contract, 1e-9))
        contract_exposure = lot_size * close_f
        max_lots_notional = floor(max_notional / max(contract_exposure, 1e-9))
        lots = max(0, min(max_lots_risk, max_lots_notional))
        qty = int(lots)
        if qty < 1 and not vetoes:
            vetoes.append("size_below_one_lot")
        notional = qty * contract_exposure
        risk_amount = qty * risk_per_contract if qty else 0.0
        if direction:
            stop_loss = close_f * (1 - stop_pct * direction)
            target = close_f * (1 + stop_pct * cfg.reward_risk * direction)
        sym_u = symbol.upper()
        if "CE" in sym_u or "PE" in sym_u:
            warnings.append(
                "option_premium_model_stub: notional uses underlying-style exposure; refine with option price + delta when wired."
            )
    else:
        qty_by_risk = floor(risk_budget / max(close_f * stop_pct, 0.01)) if close_f > 0 else 0
        qty_by_notional = floor(max_notional / close_f) if close_f > 0 else 0
        qty = max(0, min(qty_by_risk, qty_by_notional))
        if qty < 1 and not vetoes:
            vetoes.append("size_below_one_share")
        notional = qty * close_f
        risk_amount = qty * close_f * stop_pct
        stop_loss = close_f * (1 - stop_pct * direction) if direction else None
        target = close_f * (1 + stop_pct * cfg.reward_risk * direction) if direction else None

    if cfg.live_trading_enabled:
        warnings.append("live_trading_flag_seen_but_order_router_is_blocked")

    return {
        "symbol": symbol,
        "mode": "paper",
        "action": action,
        "side": side,
        "instrument_type": instrument,
        "lot_size": lot_size if instrument == "fno" else 1,
        "lots": int(qty) if instrument == "fno" else int(qty),
        "entry_price": round(close_f, 4) if close_f else None,
        "quantity": int(qty) if not vetoes else 0,
        "notional": round(notional, 2) if not vetoes else 0.0,
        "stop_loss": round(stop_loss, 4) if stop_loss and not vetoes else None,
        "target": round(target, 4) if target and not vetoes else None,
        "risk_amount": round(risk_amount, 2) if not vetoes else 0.0,
        "risk_config": cfg.to_dict(),
        "vetoes": vetoes,
        "warnings": warnings,
        "eligible": not vetoes,
        "reason": (
            f"{side} plan from brain score={score:+.3f}, confidence={confidence:.2f}; "
            f"risk_per_trade={cfg.risk_per_trade_pct:.3%}, stop={stop_pct:.2%}; "
            f"instrument={instrument}, lot_size={lot_size}"
        ),
    }
