"""One-off: correct crypto journal rows whose funding was charged 100x.

Until 2026-09-23, crypto.charges.funding_cost_usd treated Delta's funding_rate
(quoted in percent) as a fraction, so every funding charge was 100x too big
and pnl_usd / pnl_inr were off by that amount. New rows carry
``funding_pct_units: True``; this rewrites only rows without it that have a
non-zero funding_usd, and marks them, so running it twice changes nothing.

    python -m scripts.fix_crypto_funding_units          # dry run
    python -m scripts.fix_crypto_funding_units --apply  # backup + rewrite
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime

from crypto.journal import JOURNAL_PATH


def fix_row(r: dict) -> bool:
    f = float(r.get("funding_usd") or 0.0)
    if r.get("funding_pct_units") or not f:
        return False
    true_f = f / 100.0
    r["pnl_usd"] = round(float(r.get("pnl_usd") or 0.0) + f - true_f, 4)
    if r.get("fx_usdinr"):
        r["pnl_inr"] = round(r["pnl_usd"] * float(r["fx_usdinr"]), 2)
    r["funding_usd"] = round(true_f, 6)
    r["funding_pct_units"] = True
    return True


def main(apply: bool) -> None:
    lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    out, changed, delta = [], 0, 0.0
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        before = float(r.get("pnl_usd") or 0.0)
        if fix_row(r):
            changed += 1
            delta += r["pnl_usd"] - before
        out.append(json.dumps(r))
    print(f"rows to correct: {changed}, total pnl_usd change: {delta:+.2f}")
    if not apply or not changed:
        return
    backup = JOURNAL_PATH.with_name(
        f"{JOURNAL_PATH.stem}.before-funding-fix-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    shutil.copy2(JOURNAL_PATH, backup)
    tmp = JOURNAL_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    # ponytail: the running server may append a row meanwhile -- refuse rather than lose it
    if len(JOURNAL_PATH.read_text(encoding="utf-8").splitlines()) != len(lines):
        tmp.unlink()
        sys.exit("journal changed while fixing -- run again")
    os.replace(tmp, JOURNAL_PATH)
    print(f"done; backup at {backup}")


if __name__ == "__main__":
    main("--apply" in sys.argv)
