"""Runs ONLY inside .venv-openbb — never imported by the main app or its venv.

Reads a JSON list of bare NSE symbols (no ".NS" suffix) from stdin, fetches
each one's quote + key fundamentals via OpenBB's free yfinance provider, and
writes ``{symbol: {...} | {"error": "..."}}`` as JSON to stdout. One bad
symbol never kills the batch.
"""

import json
import sys


def main() -> None:
    symbols = json.loads(sys.stdin.read())
    from openbb import obb

    out: dict[str, dict] = {}
    for sym in symbols:
        ticker = f"{sym}.NS"
        try:
            q = obb.equity.price.quote(ticker, provider="yfinance").results[0]
            m = obb.equity.fundamental.metrics(ticker, provider="yfinance").results[0]
            out[sym] = {
                "last_price": q.last_price,
                "pe_ratio": m.pe_ratio,
                "peg_ratio": m.peg_ratio,
                "debt_to_equity": m.debt_to_equity,
                "profit_margin": m.profit_margin,
                "revenue_growth": m.revenue_growth,
                "price_to_book": m.price_to_book,
                "year_high": q.year_high,
                "year_low": q.year_low,
            }
        except Exception as exc:
            out[sym] = {"error": str(exc)}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
