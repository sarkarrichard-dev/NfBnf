# Testing Patterns

**Analysis Date:** 2026-09-30

## Test Framework

**Runner:**
- pytest >=8 (from `pyproject.toml`)
- Config: `pyproject.toml` `[tool.pytest.ini_options]` section
- Test paths: `testpaths = ["tests"]`
- Excludes: `norecursedirs = ["reference"]` (vendor code never tested)

**Assertion Library:**
- Python built-in `assert` statements (no external library)
- `assert condition` or `assert a == b` style
- FastAPI testing: `TestClient` for response assertions

**Run Commands:**
```bash
python -m pytest -q                     # Run all tests, quiet (test count + result)
python -m pytest tests/test_<name>.py   # Run single test file
python -m pytest -k <pattern>           # Run tests matching pattern
python -m pytest --tb=short             # Verbose failure output
python -m pytest -v                     # Very verbose (each test name)
```

**Full suite:** 660+ tests across 119 files, takes ~7-8 min per `CLAUDE.md`

## Test File Organization

**Location:**
- All tests live in `tests/` directory at repo root
- Mirrored structure to source: test files named `test_<module_name>.py`
- Examples:
  - `index_ai/buy_strategy.py` → `tests/test_buy_strategy.py`
  - `index_ai/credit_spread.py` → `tests/test_credit_spread.py`
  - `index_ai/server.py` → `tests/test_api.py` (routes), `tests/test_routes.py`

**Naming:**
- `test_<verb>_<noun>()` or `test_<what_is_tested>()`
- Examples: `test_status_starts_in_safe_paper_mode()`, `test_net_credit_and_pnl()`, `test_dedup_drops_the_second_send_of_a_key()`
- Descriptive, not abbreviated; the function name is the docstring

**Structure:**
```
tests/
├── conftest.py                  # Autouse fixtures for all tests
├── test_admin_auth.py
├── test_analytics.py
├── test_api.py                  # FastAPI endpoint tests
├── test_strategy_performance.py
├── test_notify.py
├── test_crypto_*.py             # 12+ crypto-specific tests
├── test_commodities.py
├── test_backtest*.py
└── ... (119 files total)
```

## Test Structure

**Suite Organization:**
- One test file = one module (e.g., `test_charges.py` tests `index_ai/charges.py`)
- No class-based test suites; functions only
- No pytest class fixtures or inheritance; use module-level fixtures via `conftest.py`
- One test ≈ one assertion (or 2–3 related assertions for a single behavior)

**Setup/Teardown Pattern:**
```python
@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Autouse fixture in conftest.py — runs for every test."""
    monkeypatch.setenv("EMA_FAST_PERIOD", "8")
    monkeypatch.setenv("EMA_SLOW_PERIOD", "20")
    # ... more setup ...
    yield
    # ... teardown if needed ...
```

**Key Fixture (conftest.py):**
- `_test_env`: Isolates `.env` and databases to `tmp_path` for every test
  - Prevents real `.env` from leaking into tests
  - Clears Telegram creds per-test (only `test_notify.py` re-sets them deliberately) to prevent real Telegram notifications
  - Monkeypatches config paths: `ENV_PATH`, `DB_PATH`, `market_log.DB_PATH`, `spread_calib.SAMPLES_PATH`
  - Re-initializes strategy params for test isolation
  
**Assertion Pattern:**
```python
assert response.status_code == 200
data = response.json()
assert data["trading_mode"] == "PAPER"
assert {item["key"] for item in data["symbols"]} == {"NIFTY", "BANKNIFTY", "SENSEX"}
```

## Mocking

**Framework:** pytest's built-in `monkeypatch` (no `unittest.mock`)

**Patterns:**
```python
def test_send_threads_through_to_post(sent):
    """sent fixture stubs notify._post and captures calls."""
    notify.send("hello", key="k1", window_s=42.0)
    import time
    for _ in range(50):
        if sent:
            break
        time.sleep(0.01)
    assert sent == [("hello", "k1", 42.0)]

def test_dedup_drops_the_second_send_of_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notify, "_stamp_path", lambda: str(tmp_path / "seen.json"))
    
    import httpx
    calls: list[int] = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: calls.append(1) or type("R", (), {"status_code": 200})(),
    )
    assert notify._post("x", "exit:99", 3600.0) is True
    assert calls == [1]
```

**What to Mock:**
- External APIs: `httpx.post`, `httpx.get` (stubs for Dhan, Telegram, etc.)
- File paths: env file path, DB path, cache paths (all in `conftest.py`)
- Environment variables: strategy parameters, feature flags, secrets (via `monkeypatch.setenv`)
- Time-dependent behavior: `time.sleep`, `time.time` for dedup windows
- Broker responses: API response shapes (e.g., test_backtest.py's `_settings()`)

**What NOT to Mock:**
- Core business logic (buy/sell strategy evaluation — test with real data)
- Data structures (candles, options chains — generate synthetic but real)
- In-memory caches: let them run; they're part of the test
- Pydantic validation: exercise it, don't skip
- Database operations (but use in-memory/tmp-path DB, never production DB)

## Fixtures and Factories

**Test Data:**
```python
def _candles(days: int = 2, bars_per_day: int = 30) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    ist = ZoneInfo("Asia/Kolkata")
    rows = []
    base = datetime(2026, 6, 2, 9, 15, tzinfo=ist)
    price = 100.0
    for d in range(days):
        for b in range(bars_per_day):
            ts = base + timedelta(days=d, minutes=5 * b)
            price += 0.5
            rows.append({
                "datetime": ts,
                "open": price - 0.2,
                "high": price + 0.3,
                "low": price - 0.3,
                "close": price,
                "volume": 1000,
            })
    return pd.DataFrame(rows)

def _settings() -> AppSettings:
    """Return a test AppSettings with safe defaults."""
    return AppSettings(
        dhan=DhanSettings(...),
        risk=RiskSettings(
            trading_mode="PAPER",
            allow_live_trading=False,
            ...
        ),
    )

def _bull_put_legs() -> list[dict]:
    """Standard bull put spread option legs for spread math tests."""
    return [
        {"transaction_type": "SELL", "option_type": "PUT", "strike": 24000, "ltp": 80.0},
        {"transaction_type": "BUY", "option_type": "PUT", "strike": 23900, "ltp": 40.0},
    ]
```

**Location:**
- Helper functions at top of test file (before test functions), prefixed with `_`
- Autouse fixtures in `conftest.py` (run once per test, no import needed)
- Custom fixtures in same file if used by only that test file

**Custom Fixture Example (test_notify.py):**
```python
@pytest.fixture
def sent(monkeypatch):
    """Capture what would be posted; force enabled()."""
    out: list[tuple[str, str, float]] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    
    def fake_post(text, key, window_s):
        out.append((text, key, window_s))
        return True
    
    monkeypatch.setattr(notify, "_post", fake_post)
    return out
```

## Coverage

**Requirements:** None enforced (no CI gate)

**View Coverage:**
```bash
python -m pytest --cov=index_ai --cov-report=html
# Opens htmlcov/index.html in a browser
```

**Status:** Full suite runs but coverage is not a blocking metric. The 660 tests across 119 files provide good coverage by inspection.

## Test Types

**Unit Tests:**
- ~70% of test suite
- Single function in isolation: `test_net_credit_and_pnl()`, `test_max_loss_bull_put()`
- Use synthetic data from `_candles()` or `_settings()` factories
- Test one behavior per function
- Examples: `test_charges.py`, `test_credit_spread.py`, `test_breakout.py`

**Integration Tests:**
- ~25% of test suite
- Multiple modules working together: strategy evaluation → pnl calculation → learning
- Example: `test_strategy_performance.py` tests strategy scoring across the full pipeline
- Example: `test_backtest.py` replays candles through the whole executor

**API/E2E Tests:**
- ~5% of test suite
- `test_api.py`: FastAPI endpoint tests via `TestClient`
- Example: `test_status_starts_in_safe_paper_mode()` hits `/api/status` and verifies response shape
- Example: `test_admin_auth.py`, `test_dashboard_password.py` test auth + state mutations
- No browser/Selenium; HTTP-only

**Example (test_api.py):**
```python
def test_status_starts_in_safe_paper_mode() -> None:
    client = TestClient(app)
    response = client.get("/api/status")
    
    assert response.status_code == 200
    data = response.json()
    assert data["trading_mode"] == "PAPER"
    assert data["live_allowed"] is False
```

## Common Patterns

**Async Testing:**
- Test handlers: no special syntax; `TestClient` is sync wrapper
- Async helpers: use `await` inside test if needed (rare; most tests are sync)
- Example from crypto: crypto tests import the route handlers and call them directly, tests are sync

**Error Testing:**
```python
def test_a_send_that_never_succeeds_leaves_the_key_free_for_next_time(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    
    import httpx
    def always_fails(*a, **k):
        raise httpx.ConnectError("getaddrinfo failed")
    
    monkeypatch.setattr(httpx, "post", always_fails)
    assert notify._post("x", "preopen:2026-09-15", 3600.0) is False
    # Verify state after failure: key NOT stamped → retry allowed
    assert notify._already_sent("preopen:2026-09-15", 3600.0) is False
```

**Parametrized Tests:**
- Rare in this codebase; prefer separate test functions (more readable, easier to isolate failures)
- When used: `@pytest.mark.parametrize("param", [value1, value2])`

**Money-path Testing:**
- `test_charges.py`: Exact rupee amounts with statutory component breakdown
- `test_credit_spread.py`: P&L math with spreads and quantities
- `test_backtest_options.py`: Full backtest replay with live journal comparison
- Tests use real charge rates from `ChargeRates` (not mocked) to verify net-of-cost accuracy

**Strategy-specific Testing:**
- `test_buy_strategy.py`: Signal evaluation on synthetic candles
- `test_credit_spread.py`: 2-leg spread math and P&L
- `test_trailing.py`: Stop and profit trail exit triggers
- Tests run with paper mode default; no live orders ever sent

**Isolation Guarantee:**
- Every test runs in its own:
  - Temp directory (`tmp_path` fixture)
  - Isolated `.env` file (no real credentials)
  - Isolated SQLite databases (no trade history pollution)
  - Separate strategy params (reloaded per test)
- Monkeypatch automatically undoes all changes after test completes

---

*Testing analysis: 2026-09-30*
