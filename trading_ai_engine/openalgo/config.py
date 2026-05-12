from __future__ import annotations

import os
from dataclasses import dataclass

from trading_ai_engine.openalgo.url import normalize_openalgo_base_url
from trading_ai_engine.secrets_bridge import env_or_local


@dataclass(frozen=True)
class OpenAlgoConfig:
    base_url: str
    api_key: str | None
    strategy: str
    default_product_equity: str
    default_product_fno: str

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.base_url)


def load_openalgo_config() -> OpenAlgoConfig:
    raw_url = env_or_local("OPENALGO_BASE_URL", "http://127.0.0.1:5000") or "http://127.0.0.1:5000"
    try:
        base = normalize_openalgo_base_url(raw_url)
    except ValueError:
        base = ""
    return OpenAlgoConfig(
        base_url=base,
        api_key=env_or_local("OPENALGO_API_KEY"),
        strategy=(env_or_local("OPENALGO_STRATEGY", "TradingAIML") or "TradingAIML").strip() or "TradingAIML",
        default_product_equity=(env_or_local("TRADING_AI_OPENALGO_PRODUCT", "MIS") or "MIS").strip().upper(),
        default_product_fno=(env_or_local("TRADING_AI_OPENALGO_FNO_PRODUCT", "NRML") or "NRML")
        .strip()
        .upper(),
    )


def openalgo_orders_disabled_by_env() -> bool:
    return os.environ.get("TRADING_AI_OPENALGO_DISABLE", "").lower() in ("1", "true", "yes")
