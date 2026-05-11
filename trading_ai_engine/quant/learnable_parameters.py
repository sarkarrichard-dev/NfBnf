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
                    "name": "research_backtest.signal_mode",
                    "type": "enum",
                    "range_hint": "structural | trend_ma | mean_reversion_z",
                    "api": "GET /api/research/backtest?signal_mode=trend_ma&fast_ma=20&slow_ma=50",
                },
                {
                    "name": "research_backtest.mean_reversion_z",
                    "type": "float+int",
                    "api": "GET /api/research/backtest?signal_mode=mean_reversion_z&z_lookback=20&z_entry=1.0",
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
                "Holding horizon in bars, session-aware gates (IST), ingest cadence for "
                "local datasets, and **intraday bar size** when ``market_focus=derivatives_intraday``."
            ),
            "workstation_mapping": [
                {
                    "name": "TRADING_AI_MARKET_FOCUS",
                    "type": "enum",
                    "env": True,
                    "range_hint": "balanced | derivatives_intraday (default brain chart + readiness blurb)",
                },
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
                "Non-OHLC text: option-chain heatmap digest; optional local SQLite catalog "
                "(off by default). Primary online path is Hugging Face Hub streaming + Yahoo global pack."
            ),
            "workstation_mapping": [
                {"name": "heatmap_text_digest", "type": "text", "module": "trading_ai_engine.market_vision.features"},
                {
                    "name": "TRADING_AI_ALLOW_LOCAL_FILE_DIGEST_FOR_BRAIN",
                    "type": "bool",
                    "env": True,
                    "note": "Must be true for include_ml_digest to surface SQLite file-catalog text in the LLM.",
                },
            ],
        },
        {
            "id": "online_hub_learning",
            "label": "Online Hub learning (Hugging Face)",
            "description": (
                "Streaming row samples from configured Hub datasets feed the remote LLM as "
                "``online_hf_hub_digest`` — online only, no local upload required for this path."
            ),
            "workstation_mapping": [
                {"name": "TRADING_AI_HF_LEARNING_DATASETS", "type": "string", "env": True},
                {"name": "HF_TOKEN / HUGGING_FACE_HUB_TOKEN", "type": "secret", "env": True},
                {"name": "ml.hf_online_digest.build_hf_online_learning_digest", "type": "code", "module": "trading_ai_engine.ml.hf_online_digest"},
                {"name": "GET /api/ml/online-learning/status", "type": "api"},
                {"name": "GET /api/ml/online-learning/preview", "type": "api"},
            ],
        },
        {
            "id": "global_cross_asset_context",
            "label": "Global cross-asset context (Yahoo)",
            "description": (
                "Parallel Yahoo pulls for indices/FX/commodities; numeric ``global_*`` metrics + digest for LLM."
            ),
            "workstation_mapping": [
                {"name": "TRADING_AI_GLOBAL_CONTEXT_SYMBOLS", "type": "string", "env": True},
                {"name": "TRADING_AI_GLOBAL_CONTEXT_PERIOD", "type": "string", "env": True},
                {"name": "TRADING_AI_GLOBAL_CONTEXT_INTERVAL", "type": "string", "env": True},
                {"name": "market_context.global_pack.fetch_global_context_snapshot", "type": "code", "module": "trading_ai_engine.market_context.global_pack"},
            ],
        },
        {
            "id": "multi_strategy_features",
            "label": "Multi-strategy feature proxies",
            "description": (
                "``strat_*`` columns derived on the primary symbol OHLC (trend ratio, MR z, vol) merged before ml_core."
            ),
            "workstation_mapping": [
                {"name": "market_context.strategy_features.extra_strategy_metrics", "type": "code", "module": "trading_ai_engine.market_context.strategy_features"},
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
                {
                    "name": "quant.backtest_sweep",
                    "type": "code",
                    "module": "trading_ai_engine.quant.backtest_sweep",
                    "note": "Grid over signal_mode × horizon × cost_bps (default three strategy proxies).",
                },
                {
                    "name": "quant.strategy_taxonomy",
                    "type": "json",
                    "api": "GET /api/quant/strategy-taxonomy",
                },
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
