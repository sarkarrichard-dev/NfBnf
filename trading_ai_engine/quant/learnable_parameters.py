"""
Catalog of parameter families that systematic / AIML stacks typically learn or optimize.

Maps the workstation's *existing* knobs (risk env, backtest horizon, heatmap features, etc.)
to these families so future optimizers (grid, Bayesian, RL) and Dhan-fed models share one vocabulary.
"""

from __future__ import annotations

from typing import Any

LEARNABLE_PARAMETER_CATALOG: dict[str, Any] = {
    "version": "learnable_catalog_v1",
    "families": [
        {
            "id": "entry_exit_signals",
            "label": "Entry / exit signals",
            "description": (
                "Thresholds for levels, MA-style structure, RSI-like momentum, and band-break "
                "logic used inside the structural brain and research backtest."
            ),
            "workstation_mapping": [
                {
                    "name": "research_backtest.horizon_bars",
                    "type": "int",
                    "range_hint": "1-20 trading days forward hold in walk-forward test",
                    "api": "GET /api/research/backtest?horizon=N",
                },
                {
                    "name": "brain.ml_core + fusion thresholds",
                    "type": "implicit",
                    "note": "Long/short thresholds live in research/backtest BacktestConfig; "
                    "fused action bands in brain/fusion.py (combined score cutoffs).",
                },
            ],
        },
        {
            "id": "risk_parameters",
            "label": "Risk parameters",
            "description": (
                "Stop distance, position sizing caps, daily loss budget, max trades per day, "
                "and minimum confidence to arm a paper plan."
            ),
            "workstation_mapping": [
                {"name": "TRADING_AI_RISK_PER_TRADE_PCT", "type": "float", "env": True},
                {"name": "TRADING_AI_MAX_POSITION_PCT", "type": "float", "env": True},
                {"name": "TRADING_AI_MAX_DAILY_LOSS_PCT", "type": "float", "env": True},
                {"name": "TRADING_AI_MAX_TRADES_PER_DAY", "type": "int", "env": True},
                {"name": "TRADING_AI_MIN_TRADE_CONFIDENCE", "type": "float", "env": True},
                {"name": "TRADING_AI_MIN_ABS_TRADE_SCORE", "type": "float", "env": True},
                {"name": "TRADING_AI_DEFAULT_STOP_PCT", "type": "float", "env": True},
                {"name": "TRADING_AI_REWARD_RISK", "type": "float", "env": True},
                {"name": "build_trade_plan", "type": "code", "module": "trading_ai_engine.trading.risk"},
            ],
        },
        {
            "id": "timing_parameters",
            "label": "Timing parameters",
            "description": (
                "Holding horizon in bars, session-aware gates (IST), and ingest cadence for "
                "local datasets."
            ),
            "workstation_mapping": [
                {"name": "paper_stats_current_ist_day", "type": "db", "module": "server.db"},
                {"name": "india.market_clock", "type": "code", "module": "trading_ai_engine.india.market_clock"},
            ],
        },
        {
            "id": "market_characteristics",
            "label": "Market characteristics (regime)",
            "description": (
                "Volatility, trend strength, and option-chain skew / PCR as regime proxies."
            ),
            "workstation_mapping": [
                {"name": "ml_core.regime", "type": "code", "module": "trading_ai_engine.brain.ml_core"},
                {"name": "heatmap_ml_features", "type": "vector", "module": "trading_ai_engine.market_vision.features"},
            ],
        },
        {
            "id": "alternative_data_features",
            "label": "Alternative data features",
            "description": (
                "Numerical summaries from non-OHLC sources: profiled CSV/Excel catalog digest, "
                "future news/sentiment scores when wired."
            ),
            "workstation_mapping": [
                {"name": "ml_digest / text_digest", "type": "text", "module": "trading_ai_engine.ml.ingest"},
                {"name": "heatmap_text_digest", "type": "text", "module": "trading_ai_engine.market_vision.features"},
            ],
        },
        {
            "id": "optimization_loop",
            "label": "Backtest-driven parameter search",
            "description": (
                "Walk-forward research backtest with configurable round-trip cost (bps) to "
                "stress margins, drawdown, and profit factor before promoting parameters."
            ),
            "workstation_mapping": [
                {
                    "name": "research_backtest.cost_bps",
                    "type": "float",
                    "range_hint": "0-50 bps typical sensitivity",
                    "api": "GET /api/research/backtest?cost_bps=X",
                },
                {"name": "quant.backtest_sweep.sweep_backtest_grid", "type": "code", "module": "trading_ai_engine.quant.backtest_sweep"},
            ],
        },
    ],
    "feedback_loops": [
        {
            "id": "operator_feedback",
            "description": "Per-tag EMAs from thumbs up/down on findings (learn.apply_feedback).",
        },
        {
            "id": "post_mortem_refinement",
            "description": "Forward-return check vs fused action; nudges next fused score (learning.refinement).",
        },
        {
            "id": "paper_execution_journal",
            "description": "Paper orders + evolution ledger for audit and future reward labels.",
        },
    ],
}
