"""
Taxonomy of common algorithmic strategy *styles*, aligned with public educational material.

Primary reference: Groww — "Top 7 Algorithmic Trading Strategies with Examples and Risks"
https://groww.in/blog/algorithmic-trading-strategies

This module does not reproduce Groww's proprietary content; it maps strategy *classes* to how
this workstation implements or plans them (research backtest, risk, heatmaps, learning loops).
"""

from __future__ import annotations

from typing import Any

GROWW_STRATEGY_TAXONOMY: dict[str, Any] = {
    "reference_title": "Top 7 Algorithmic Trading Strategies (examples & risks)",
    "reference_url": "https://groww.in/blog/algorithmic-trading-strategies",
    "notes": (
        "Educational taxonomy only — not investment advice. Live arbitrage / HFT / index "
        "rebalance front-running require venue-specific infrastructure not included here. "
        "Workstation design center: Indian F&O and intraday session decisions (see "
        "``trading_ai_engine.trading.derivatives_focus``)."
    ),
    "strategies": [
        {
            "id": "mean_reversion",
            "groww_name": "Mean Reversion",
            "idea": "Price stretched vs a local mean may snap back; watch trend regimes.",
            "workstation": {
                "status": "research_proxy",
                "implementation": (
                    "Walk-forward research backtest with ``signal_mode=mean_reversion_z`` "
                    "(z-score vs rolling mean on closes). Tune ``z_lookback``, ``z_entry`` via API."
                ),
            },
        },
        {
            "id": "arbitrage",
            "groww_name": "Arbitrage",
            "idea": "Exploit temporary mispricings across venues or correlated baskets.",
            "workstation": {
                "status": "planned",
                "implementation": "Needs multi-venue quotes and latency-aware execution (e.g. Dhan + others).",
            },
        },
        {
            "id": "index_rebalance",
            "groww_name": "Index Fund Rebalancing",
            "idea": "Trade around predictable index composition / weight changes.",
            "workstation": {
                "status": "planned",
                "implementation": "Requires index event calendar and constituent flow model.",
            },
        },
        {
            "id": "trend_following",
            "groww_name": "Trend Following",
            "idea": "Ride sustained moves; weak in chop.",
            "workstation": {
                "status": "research_proxy",
                "implementation": (
                    "``signal_mode=trend_ma`` uses fast vs slow SMA on past-only closes. "
                    "Tune ``fast_ma`` / ``slow_ma`` and thresholds via ``GET /api/research/backtest``."
                ),
            },
        },
        {
            "id": "market_timing",
            "groww_name": "Market Timing",
            "idea": "Participate selectively using macro/technical filters.",
            "workstation": {
                "status": "partial",
                "implementation": (
                    "IST session gates, readiness checklist, optional LLM narrative; "
                    "no dedicated macro series feed yet."
                ),
            },
        },
        {
            "id": "vwap_twap_execution",
            "groww_name": "VWAP / TWAP",
            "idea": "Slice large orders to reduce impact cost.",
            "workstation": {
                "status": "planned",
                "implementation": "Execution layer not wired; ``cost_bps`` in backtest approximates friction.",
            },
        },
        {
            "id": "quant_ml",
            "groww_name": "Mathematical / ML-Based",
            "idea": "Models over many inputs; watch overfit and explainability.",
            "workstation": {
                "status": "partial",
                "implementation": (
                    "Market brain linear model (``market_learn``), structural ``ml_core``, "
                    "heatmap features, file digest, post-mortem refinement loop."
                ),
            },
        },
    ],
    "success_elements_from_article": [
        {
            "topic": "Risk management",
            "workstation": "TRADING_AI_* env, paper gates, kill switch, ``build_trade_plan``.",
        },
        {
            "topic": "Backtesting",
            "workstation": "/api/research/backtest, /api/quant/backtest-sweep, costs via cost_bps.",
        },
        {
            "topic": "Continuous monitoring",
            "workstation": "/api/trading/readiness, evolution ledger, post-mortem, learning/loops.",
        },
        {
            "topic": "Transaction costs",
            "workstation": "cost_bps in research; slippage not modelled tick-by-tick.",
        },
    ],
}
