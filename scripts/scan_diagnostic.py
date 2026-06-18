"""One-shot scan diagnostic — run during market hours for live signal/plan state."""
from __future__ import annotations

from index_ai.config import settings
from index_ai.dhan import DhanClient
from index_ai.instruments import configured_index_keys
from index_ai.planner import plan_instrument
from index_ai.strategy_params import get_strategy_params


def main() -> None:
    cfg = settings()
    params = get_strategy_params()
    print("Strategy flags:")
    print(f"  auto_trend_buy_first={params.auto_trend_buy_first}")
    print(f"  auto_credit_sideways_only={params.auto_credit_sideways_only}")
    print(f"  entry_confirmation_bars={params.entry_confirmation_bars}")
    print(f"  require_ema_cross_for_credit={params.require_ema_cross_for_credit}")
    print()

    client = DhanClient(cfg.dhan)
    for key in configured_index_keys():
        try:
            r = plan_instrument(client=client, app_settings=cfg, instrument_key=key)
            sig = r.get("signal") or {}
            plan = r.get("plan") or {}
            cpr = r.get("cpr_regime") or {}
            buy = r.get("buy_signal") or {}
            sell = r.get("sell_signal") or {}
            print(f"=== {key} ===")
            print(f"  primary={sig.get('action')} conf={sig.get('confidence')} cpr={cpr.get('day_bias')}")
            print(f"  BUY  lane: {buy.get('action')} — {(buy.get('reason') or '')[:120]}")
            print(f"  SELL lane: {sell.get('action')} — {(sell.get('reason') or '')[:120]}")
            print(f"  plan_allowed={plan.get('allowed')}")
            print(f"  opportunities={len(r.get('opportunities') or [])}")
            if r.get("error"):
                print(f"  error: {r['error']}")
            print()
        except Exception as exc:
            print(f"=== {key} ERROR === {exc}\n")


if __name__ == "__main__":
    main()
