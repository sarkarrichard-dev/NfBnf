"""MCX commodity futures — a separate section, evening-active, on the Dhan broker.

Runs alongside the index and crypto lanes but on its own scan task (MCX trades
09:00–23:30 IST, well past the 15:30 equity close). Reuses the index directional
signal (`index_ai.strategies.futures.engine` — CPR + EMA + Supertrend), the Dhan
client, and the trailing-stop idea; brings its own session clock, contract specs,
MCX charge schedule, and JSONL journal.

Paper only. Never wired to live orders — like the index futures paper lane, the
backtest verdict on this signal is negative and this is a forward-record lane.
"""
