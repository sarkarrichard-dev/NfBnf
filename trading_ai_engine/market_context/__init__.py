"""Cross-asset and multi-strategy **context** for the brain (Yahoo + derived features)."""

from trading_ai_engine.market_context.global_pack import fetch_global_context_snapshot
from trading_ai_engine.market_context.strategy_features import extra_strategy_metrics

__all__ = ["extra_strategy_metrics", "fetch_global_context_snapshot"]
