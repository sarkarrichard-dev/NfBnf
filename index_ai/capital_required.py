"""Capital / margin estimates for buy and credit-spread setups (live chain LTPs)."""

from __future__ import annotations

from typing import Any

from index_ai.strategies.credit_spread import attach_credit_risk_metrics, net_credit_points
from index_ai.instruments import IndexInstrument
from index_ai.trade_lots import get_lots_per_trade


def _leg_flow_rupees(ltp: float, qty: int, transaction_type: str) -> float:
    flow = round(float(ltp) * qty, 2)
    return flow if str(transaction_type).upper() == "SELL" else -flow


def compute_capital_required(
    option: dict[str, Any] | None,
    instrument: IndexInstrument,
) -> dict[str, Any] | None:
    """
    Estimate cash / margin for the proposed trade using option leg LTPs × quantity.

    Buy (single leg): premium paid upfront.
    Credit spread / iron condor: net credit received; broker blocks ~max defined loss.
    """
    if not option:
        return None

    qty = int(option.get("quantity") or instrument.lot_size)
    lots = get_lots_per_trade()
    legs = list(option.get("legs") or [])

    if legs:
        enriched = attach_credit_risk_metrics({**option, "legs": legs, "quantity": qty}, instrument)
        credit_pts = float(enriched.get("net_credit_points") or net_credit_points(legs))
        max_loss = float(enriched.get("max_loss_rupees") or 0)
        max_profit = float(enriched.get("max_profit_rupees") or 0)
        net_credit = round(credit_pts * qty, 2)
        buy_debit = 0.0
        sell_credit = 0.0
        leg_rows: list[dict[str, Any]] = []
        for leg in legs:
            ltp = float(leg.get("ltp") or 0)
            tx = str(leg.get("transaction_type") or "BUY").upper()
            flow = _leg_flow_rupees(ltp, qty, tx)
            if tx == "BUY":
                buy_debit += abs(flow)
            else:
                sell_credit += flow
            leg_rows.append(
                {
                    "transaction_type": tx,
                    "option_type": str(leg.get("option_type") or "").upper(),
                    "strike": leg.get("strike"),
                    "ltp": round(ltp, 2),
                    "flow_rupees": flow,
                }
            )
        structure = str(enriched.get("structure") or option.get("structure") or "SPREAD").upper()
        kind = "iron_condor" if structure == "IRON_CONDOR" else "credit_spread"
        cap = {
            "strategy_kind": kind,
            "structure": structure,
            "lots_per_trade": lots,
            "quantity": qty,
            "legs": leg_rows,
            "entry_buy_debit_rupees": round(buy_debit, 2),
            "entry_sell_credit_rupees": round(sell_credit, 2),
            "net_credit_rupees": net_credit,
            "max_profit_rupees": max_profit,
            "max_loss_rupees": max_loss,
            "capital_required_rupees": max_loss,
            "capital_kind": "margin",
        }
        cap["summary"] = format_capital_summary(cap)
        return cap

    ltp = float(option.get("ltp") or 0)
    premium = round(ltp * qty, 2)
    tx = str(option.get("transaction_type") or "BUY").upper()
    leg_row = {
        "transaction_type": tx,
        "option_type": str(option.get("option_type") or "").upper(),
        "strike": option.get("strike"),
        "ltp": round(ltp, 2),
        "flow_rupees": _leg_flow_rupees(ltp, qty, tx),
    }
    cap = {
        "strategy_kind": "buy",
        "structure": None,
        "lots_per_trade": lots,
        "quantity": qty,
        "legs": [leg_row],
        "entry_buy_debit_rupees": premium if tx == "BUY" else 0.0,
        "entry_sell_credit_rupees": premium if tx == "SELL" else 0.0,
        "net_credit_rupees": None,
        "max_profit_rupees": None,
        "max_loss_rupees": premium,
        "capital_required_rupees": premium,
        "capital_kind": "premium",
    }
    cap["summary"] = format_capital_summary(cap)
    return cap


def capital_deployed_rupees(option: dict[str, Any] | None, qty: int) -> tuple[float | None, str]:
    """Rupees committed to a *recorded* trade, computed after the fact from the
    stored entry prices (not live LTPs).

    - buy-only     → premium paid = entry premium × qty              (kind "premium")
    - sell + hedge → combined capital at risk = the spread's defined max loss
                     (hedge debit + short-leg margin net of the hedge benefit
                     ≈ strike width − net credit, per unit)          (kind "margin")

    ``qty`` is the effective total contract count (NSE lot size × lots).
    """
    if not option:
        return None, "premium"
    q = max(0, int(qty or 0))
    legs = [leg for leg in (option.get("legs") or []) if isinstance(leg, dict)]

    if legs:
        ml = option.get("max_loss_rupees")
        try:
            if ml is not None and float(ml) > 0:
                return round(float(ml), 2), "margin"
        except (TypeError, ValueError):
            pass
        # fallback from the legs themselves: (strike width − net credit) × qty
        strikes = [float(leg["strike"]) for leg in legs if leg.get("strike") is not None]
        if len(strikes) >= 2 and q:
            width = max(strikes) - min(strikes)
            credit_per_unit = 0.0
            for leg in legs:
                px = leg.get("entry_ltp") or leg.get("ltp") or leg.get("entry_option_ltp")
                if px is None:
                    return None, "margin"
                sign = 1.0 if str(leg.get("transaction_type") or "").upper() == "SELL" else -1.0
                credit_per_unit += sign * float(px)
            return round(max(0.0, width - credit_per_unit) * q, 2), "margin"
        return None, "margin"

    px = option.get("entry_ltp") or option.get("ltp") or option.get("entry_option_ltp")
    try:
        return (round(float(px) * q, 2), "premium") if px and q else (None, "premium")
    except (TypeError, ValueError):
        return None, "premium"


def format_capital_summary(cap: dict[str, Any]) -> str:
    """One-line label for dashboard heatmap / plan panels."""
    amt = float(cap.get("capital_required_rupees") or 0)
    qty = int(cap.get("quantity") or 0)
    lots = int(cap.get("lots_per_trade") or 1)
    kind = cap.get("capital_kind")
    legs = cap.get("legs") or []
    if kind == "margin":
        credit = cap.get("net_credit_rupees")
        credit_part = f" · credit ₹{credit:,.0f}" if credit is not None else ""
        leg_n = len(legs)
        return f"Margin ~₹{amt:,.0f}{credit_part} · {leg_n} legs · {lots} lot(s) · qty {qty}"
    return f"Premium ~₹{amt:,.0f} · {lots} lot(s) · qty {qty}"


if __name__ == "__main__":  # self-check — capital_deployed_rupees branches
    assert capital_deployed_rupees({"entry_ltp": 95.0}, 75) == (7125.0, "premium")
    _spread = {
        "legs": [
            {"transaction_type": "SELL", "strike": 24000, "entry_ltp": 120},
            {"transaction_type": "BUY", "strike": 23800, "entry_ltp": 60},
        ]
    }
    assert capital_deployed_rupees({**_spread, "max_loss_rupees": 10500.0}, 75) == (
        10500.0,
        "margin",
    )
    assert capital_deployed_rupees(_spread, 75) == (10500.0, "margin")  # (200 − 60) × 75
    assert capital_deployed_rupees(None, 75) == (None, "premium")
    assert capital_deployed_rupees({"ltp": None}, 75) == (None, "premium")
    print("capital_required self-check ok — premium vs combined-margin")
