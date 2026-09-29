# Coding Conventions

**Analysis Date:** 2026-09-30

## Naming Patterns

**Files:**
- Python: `snake_case.py` (e.g., `buy_strategy.py`, `dhan_orders.py`, `candle_cache.py`)
- React/TypeScript: `PascalCase.tsx` for components (e.g., `ExecutionPanel.tsx`, `DhanAuthPanel.tsx`)
- Test files: `test_<module_name>.py` matching the module being tested
- Utility/hook files: `use<Name>.ts` for React hooks (e.g., `useDashboardData`, `usePollMs`, `useStickyTab`)

**Functions:**
- All lowercase with underscores: `snake_case`
- Private functions prefixed with single underscore: `_internal_function()`
- Strategy/entry functions: `evaluate_<signal_type>()`, `plan_<entity>()`, `execute_<action>()`
- Boolean predicates: `is_<condition>()`, `can_<action>()`, `_<verb>_enabled()`
- Examples: `evaluate_buy_signal()`, `plan_instrument()`, `execute_plan()`, `leg_charge_rupees()`, `strategy_tuning_summary()`, `crypto_backtest_autotune_enabled()`

**Variables:**
- Local/module vars: `snake_case`
- Constants: `UPPERCASE_WITH_UNDERSCORES` (e.g., `_DEFAULT_HALF_SPREAD_POINTS`, `VALID_CANDLE_INTERVALS`, `MAX_CPR_ENTRY_EXTENSION_PCT`)
- Private module-level: leading underscore (e.g., `_env_mtime_loaded`, `_ENV_FROZEN`, `_ENV_WRITE_LOCK`)
- Pandas DataFrames: plural or `df` for current (e.g., `frame`, `previous_day`, `combined`)

**Types:**
- TypeScript generics: `PascalCase` (e.g., `StatusResponse`, `NavGroup`, `DateRange`, `PeriodKey`)
- Python dataclasses: `PascalCase` (e.g., `DhanSettings`, `RiskSettings`, `AppSettings`, `ChargeRates`, `StrategySignal`)
- Type hints always used in function signatures: `param: Type -> ReturnType`
- Optional/Union: `Type | None` (Python 3.10+ syntax preferred)

## Code Style

**Formatting:**
- Ruff is the primary formatter
- Line length: 100 characters (set in `pyproject.toml`)
- Python target version: 3.11
- Auto-applied on every file edit via `.claude/hooks/post_edit.py`

**Linting:**
- Ruff for Python linting (auto-fix + format)
- Unfixed linting issues reported to user: ~8 pre-existing cosmetic errors (unused locals, ambiguous `l`), OK to ignore for now
- TypeScript type checking: `tsc --noEmit` (strict mode via `tsconfig.app.json`)
- TypeScript strictness: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch`

**Special:** `from __future__ import annotations` used in all Python files for forward-reference type hints without quotes.

## Import Organization

**Order (Python):**
1. `__future__` imports (always first)
2. Standard library (`os`, `sys`, `typing`, `dataclasses`, `functools`, etc.)
3. Third-party packages (`pandas`, `numpy`, `fastapi`, `httpx`, `pydantic`, etc.)
4. Local imports from `index_ai`, `crypto`, `commodities`, `scripts`

**Examples from codebase:**
```python
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import uvicorn
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request

from index_ai.admin_auth import require_admin_secret
from index_ai.analytics import build_analytics
from index_ai.config import ARM_LIVE_PHRASE, DASHBOARD_DIR, MEMORY_DIR
```

**Path Aliases (TypeScript):**
- No path aliases configured; imports use relative paths or full module paths from `src/`

**Local imports:** dot-notation for same-directory imports (`from . import module`) or relative-path imports are NOT used; always prefer absolute imports from package root

## Error Handling

**Patterns:**
- Catch specific exceptions by type, not broad `except Exception:`
- Common patterns: `except (TypeError, ValueError):`, `except HTTPException:`, `except PermissionError:`, `except OSError:`
- Fallback to broad `Exception` only when genuinely catching everything (e.g., parsing untrusted input, external API resilience)
- Examples:
  - `charges.py`: `except (TypeError, ValueError):` when parsing env vars
  - `atomic_io.py`: `except OSError:` for file operations
  - `server.py`: `except Exception: pass` (resilience, intentional)

**Strategy/Money-path code:** Extra specificity
- `executor.py`, `dhan_orders.py`, `exit.py`, `risk_manager.py`: Log exceptions, handle gracefully without losing state
- Always trace exceptions that touch live orders or trade state

**CLI/Background tasks:**
- Log.exception() when catching at task boundaries so the full stack is captured
- In request handlers, convert to HTTPException with appropriate status code

## Logging

**Framework:** Python's built-in `logging` module

**Patterns:**
- `logging.getLogger(__name__)` at module scope; fetch once, use many times
- Use `.info()` for state changes and business logic (`Strategy boot`, `Telegram disabled`)
- Use `.warning()` for recoverable issues
- Use `.error()` for failures; use `.exception()` at exception handlers to include traceback
- Avoid logging credentials or sensitive auth tokens; the `_quiet_http_loggers()` hook in `server.py` sets `httpx` and `httpcore` to WARNING to suppress sensitive token logs
- No structured logging keys; use f-strings for readability

**Example:**
```python
logging.getLogger(__name__).info(
    "Strategy boot: style=%s intelligent_routing=%s loss_guard=%s",
    os.getenv("STRATEGY_STYLE", "AUTO"),
    sp.auto_intelligent_routing,
    os.getenv("LOSS_GUARD_ENABLED", "true"),
)
```

## Comments

**When to Comment:**
- Explain WHY, not WHAT (the code says WHAT)
- Complex algorithms (trail logic, CPR regimes, charge breakdowns): one-line summary + step-by-step why
- Non-obvious business rules (e.g., 15-min force square-off in `config.py`, 1-day max-hold in crypto)
- Gotchas and warnings for future maintainers (e.g., conftest.py's comment on Telegram token leaks)

**Examples from codebase:**
```python
# Per-index default half-spread, in *points of option premium*, paid each side.
# Deliberately conservative — calibrate against the paper journal once live.
_DEFAULT_HALF_SPREAD_POINTS: dict[str, float] = { ... }

# Never load the user's real .env into a test run. _load_env() already skips
# while a test is running, but test modules are imported during collection,
# before that -- and importing index_ai.server loads .env (so the dashboard
# password and other real settings would leak into every test).
```

**JSDoc/TSDoc:**
- Minimal use in TypeScript; not a standard in this codebase
- React components document props only if non-obvious (most component purpose is clear from name and usage)
- Python: Docstrings on public functions; follow the pattern:
  ```python
  def evaluate_buy_signal(...) -> StrategySignal:
      """
      Option buying from OHLC patterns + support/resistance.

      S/R is the real option-chain OI walls (max-put/max-call OI strikes) when
      ``oi`` gives a clean read — the same walls the sell lane already uses —
      else the candle-range guess, same as before.
      """
  ```

## Function Design

**Size:** 
- Prefer functions < 50 lines
- Strategy evaluation functions often 30-50 lines due to multi-step signal logic
- Handlers/executors are permitted to be longer (up to ~100 lines) if each block is a clear step

**Parameters:**
- Use `*` separator to force keyword-only args for optional parameters: `def foo(required, *, optional=None)`
- Default parameters for env-driven config: `params: StrategyParams | None = None` with fallback to `get_strategy_params()`
- Dataclass receivers: pass whole objects, not scattered fields (e.g., `params: StrategyParams` not `ema_fast, ema_slow, ...`)

**Return Values:**
- Single values when logic is clear
- Dictionaries for multiple related values: `{ "profit_rupees": X, "win_rate": Y }` (used in reporting)
- Dataclasses when the object is reused: `StrategySignal`, `OptionOiContext`
- Tuples for pairs only: rare, prefer dict or class
- Functions that might fail return `dict | None` or raise exceptions (not bool False)

**Example (buy_strategy.py):**
```python
def evaluate_buy_signal(
    frame: pd.DataFrame,
    previous_day: pd.DataFrame,
    regime: CprRegime,
    *,
    params: StrategyParams | None = None,
    oi: OptionOiContext | None = None,
) -> StrategySignal:
```

## Module Design

**Exports:**
- No `__all__` declarations; all public functions are importable by default
- Private functions (leading `_`) are import-able but signal "don't use this from other modules"

**Barrel Files:**
- Not used; import directly from leaf modules
- Example: `from index_ai.strategies.buy_strategy import evaluate_buy_signal`, not `from index_ai.strategies import evaluate_buy_signal`

**Dataclass Use:**
- `@dataclass(frozen=True)` for immutable config objects: `ChargeRates`, `RiskSettings`, `DhanSettings`
- Regular `@dataclass` for mutable signal objects: `StrategySignal`
- Dataclasses chosen over TypedDict for strong typing and default values

**Environment Access:**
- All env var reads centralized in `config.py`, `strategy_params.py`, or module-level loaders
- Avoid repeated `os.getenv()` calls; cache in a module-level var or via `@lru_cache`
- Example: `ChargeRates.load()` is `@lru_cache(maxsize=1)`, so multiple calls reuse the same object
- Type conversion done at the boundary; pass typed config objects throughout

## Special Patterns

**Money-path code:**
- `executor.py`: Plans → Orders
- `charges.py`: Cost breakdown by leg, with exchange and side awareness
- `risk_manager.py`: Global loss tracking across all instruments
- Extra validation and logging at money boundaries; fail safely with clear errors

**Strategy parameters:**
- Use `StrategyParams` dataclass (`strategy_params.py`) as the configuration holder
- Reload via `reload_strategy_params()` in tests to isolate test env from global state
- All numeric tuning parameters (EMA lengths, ATR multipliers, stop %), use env var with fallback: `_f("PARAM_NAME", default_value)`

**Async/threading:**
- FastAPI handlers are async
- Never blocking I/O (file writes, `.env` updates) in async handlers; use `asyncio.to_thread`
- Telegram notifications spawn daemon threads in `notify.py` so responses return immediately
- Background scan loop: separate async task (`_auto_renew_loop()` in `server.py` lifespan)

---

*Convention analysis: 2026-09-30*
