from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from nbnf.server.paths import REPO_ROOT

OPTION_FILE_RE = re.compile(
    r"^(?P<underlying>[A-Z]+)_(?P<strike>\d+)_(?P<kind>CE|PE)_(?P<day>\d{2})_(?P<mon>[A-Z]{3})_(?P<year>\d{2})\.CSV$"
)
MONTHS = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}


@dataclass(frozen=True)
class OptionContractFile:
    path: Path
    underlying: str
    strike: int
    kind: str
    expiry: str
    trade_date: str


def _option_root(underlying: str = "nifty") -> Path:
    return REPO_ROOT / "data for ml" / underlying.lower()


def parse_option_filename(path: Path, trade_date: str) -> OptionContractFile | None:
    m = OPTION_FILE_RE.match(path.name.upper())
    if not m:
        return None
    mon = MONTHS.get(m.group("mon"))
    if not mon:
        return None
    expiry = f"20{m.group('year')}-{mon}-{m.group('day')}"
    return OptionContractFile(
        path=path,
        underlying=m.group("underlying"),
        strike=int(m.group("strike")),
        kind=m.group("kind"),
        expiry=expiry,
        trade_date=trade_date,
    )


def available_trade_dates(underlying: str = "nifty", *, limit: int = 40) -> list[str]:
    root = _option_root(underlying)
    if not root.is_dir():
        return []
    dates = [p.name for p in root.iterdir() if p.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", p.name)]
    return sorted(dates, reverse=True)[:limit]


def _read_last_bar(path: Path) -> dict[str, Any] | None:
    try:
        df = pd.read_csv(path, usecols=["timestamp", "close", "volume", "oi"])
    except Exception:
        return None
    if df.empty:
        return None
    last = df.dropna(subset=["close"]).tail(1)
    if last.empty:
        return None
    row = last.iloc[0]
    return {
        "timestamp": str(row.get("timestamp") or ""),
        "close": float(row["close"]) if pd.notna(row.get("close")) else None,
        "volume": int(row["volume"]) if pd.notna(row.get("volume")) else 0,
        "oi": int(row["oi"]) if pd.notna(row.get("oi")) else 0,
    }


def local_option_chain_heatmap(
    underlying: str = "nifty",
    *,
    trade_date: str | None = None,
    max_files: int = 800,
) -> dict[str, Any]:
    dates = available_trade_dates(underlying, limit=200)
    if not dates:
        return {
            "source": "local_csv",
            "underlying": underlying.upper(),
            "trade_date": trade_date,
            "available_dates": [],
            "rows": [],
            "summary": {"status": "no_local_option_data"},
        }
    chosen = trade_date if trade_date in dates else dates[0]
    folder = _option_root(underlying) / chosen
    contracts: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.csv"))[:max_files]:
        meta = parse_option_filename(path, chosen)
        if not meta:
            continue
        last = _read_last_bar(path)
        if not last:
            continue
        contracts.append({**asdict(meta), "path": str(path), **last})

    by_strike: dict[int, dict[str, Any]] = {}
    for c in contracts:
        strike = int(c["strike"])
        row = by_strike.setdefault(strike, {"strike": strike, "CE": None, "PE": None})
        row[str(c["kind"])] = {
            "close": c["close"],
            "volume": c["volume"],
            "oi": c["oi"],
            "timestamp": c["timestamp"],
            "expiry": c["expiry"],
        }

    rows = [by_strike[k] for k in sorted(by_strike)]
    total_ce_oi = sum((r.get("CE") or {}).get("oi") or 0 for r in rows)
    total_pe_oi = sum((r.get("PE") or {}).get("oi") or 0 for r in rows)
    pcr = round(total_pe_oi / total_ce_oi, 4) if total_ce_oi else None
    return {
        "source": "local_csv",
        "underlying": underlying.upper(),
        "trade_date": chosen,
        "available_dates": dates[:40],
        "contract_count": len(contracts),
        "rows": rows,
        "summary": {
            "status": "ok",
            "strikes": len(rows),
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "pcr_oi": pcr,
            "note": "Local historical option-chain snapshot from the last bar in each contract CSV.",
        },
    }
