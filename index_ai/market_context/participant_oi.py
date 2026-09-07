"""
NSE participant-wise open interest — FII / DII / Pro / Client positioning.

NSE publishes ``fao_participant_oi_DDMMYYYY.csv`` after the close (~19:00-20:00
IST). **This is end-of-day data, not intraday**: at 09:00 the freshest file is
the *previous* session's. That is still worth having — positioning is a state
that carries overnight and FII index-futures net is one of the few genuinely
non-price inputs available free — but nothing here is a live intraday signal,
and the ``as_of`` date is always reported so a stale read is visible.

The number most watched is FII net index futures (long - short): institutions
carry directional index risk there, and a large net swing is real positioning
rather than the noise of a single session's price action.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist

CACHE_PATH = MEMORY_DIR / "participant_oi.json"
_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
_LOOKBACK_DAYS = 6


@dataclass(frozen=True)
class ParticipantOI:
    as_of: str                    # date the file covers (YYYY-MM-DD)
    fetched_at_ist: str
    stale_days: int               # sessions between as_of and today
    fii_index_fut_net: int        # + = net long index futures
    dii_index_fut_net: int
    pro_index_fut_net: int
    client_index_fut_net: int
    fii_index_call_net: int       # long calls - short calls
    fii_index_put_net: int
    fii_net_options_bias: int     # call net - put net; + = bullish option posture
    rows: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(v: Any) -> int:
    try:
        return int(float(str(v).strip().replace(",", "") or 0))
    except (TypeError, ValueError):
        return 0


def parse_csv(text: str, as_of: date) -> ParticipantOI | None:
    """Parse NSE's participant-OI CSV (first line is a quoted title, then a header)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return None
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    rows: dict[str, dict[str, int]] = {}
    for raw in reader:
        key = str(raw.get("Client Type") or "").strip().upper()
        if not key:
            continue
        rows[key] = {
            (k or "").strip(): _num(v)
            for k, v in raw.items()
            if k and k.strip() != "Client Type"
        }
    if not {"FII", "DII", "PRO", "CLIENT"} & set(rows):
        return None

    def net(who: str, long_col: str, short_col: str) -> int:
        r = rows.get(who, {})
        return _num(r.get(long_col)) - _num(r.get(short_col))

    fii_call = net("FII", "Option Index Call Long", "Option Index Call Short")
    fii_put = net("FII", "Option Index Put Long", "Option Index Put Short")
    today = now_ist().date()
    return ParticipantOI(
        as_of=as_of.isoformat(),
        fetched_at_ist=now_ist().isoformat(timespec="seconds"),
        stale_days=(today - as_of).days,
        fii_index_fut_net=net("FII", "Future Index Long", "Future Index Short"),
        dii_index_fut_net=net("DII", "Future Index Long", "Future Index Short"),
        pro_index_fut_net=net("PRO", "Future Index Long", "Future Index Short"),
        client_index_fut_net=net("CLIENT", "Future Index Long", "Future Index Short"),
        fii_index_call_net=fii_call,
        fii_index_put_net=fii_put,
        fii_net_options_bias=fii_call - fii_put,
        rows=rows,
    )


def fetch(*, on: date | None = None, lookback: int = _LOOKBACK_DAYS) -> ParticipantOI | None:
    """Most recent published file at or before ``on`` (walks back over holidays)."""
    start = on or now_ist().date()
    with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as client:
        for back in range(lookback + 1):
            day = start - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                r = client.get(_URL.format(ddmmyyyy=day.strftime("%d%m%Y")))
            except Exception:
                continue
            if r.status_code != 200 or len(r.content) < 200:
                continue
            parsed = parse_csv(r.text, day)
            if parsed is not None:
                _save(parsed)
                return parsed
    return None


def _save(p: ParticipantOI) -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
    except Exception:
        pass


def cached() -> dict[str, Any] | None:
    if not CACHE_PATH.is_file():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def load(*, max_stale_days: int = 4, refresh: bool = False) -> dict[str, Any] | None:
    """Cached read, refetching when missing/stale. Never raises."""
    if not refresh:
        c = cached()
        if c:
            try:
                age = (now_ist().date() - date.fromisoformat(c["as_of"])).days
                if age <= max_stale_days:
                    return {**c, "stale_days": age}
            except Exception:
                pass
    try:
        p = fetch()
    except Exception:
        p = None
    return p.to_dict() if p else cached()


def features(snapshot: dict[str, Any] | None) -> dict[str, float]:
    """Model features. Scaled to lakhs of contracts so they sit near unit range."""
    if not snapshot:
        return {"fii_fut_net_lakh": 0.0, "fii_opt_bias_lakh": 0.0,
                "client_fut_net_lakh": 0.0, "participant_oi_stale": 1.0}
    return {
        "fii_fut_net_lakh": round(snapshot.get("fii_index_fut_net", 0) / 1e5, 4),
        "fii_opt_bias_lakh": round(snapshot.get("fii_net_options_bias", 0) / 1e5, 4),
        "client_fut_net_lakh": round(snapshot.get("client_index_fut_net", 0) / 1e5, 4),
        "participant_oi_stale": 1.0 if int(snapshot.get("stale_days", 9)) > 4 else 0.0,
    }


if __name__ == "__main__":  # ponytail self-check
    sample = (
        '""Participant wise Open Interest as on Aug 28, 2026"",,,,,,,,,,,,,,\n'
        "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short,"
        "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
        "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
        "Total Long Contracts,Total Short Contracts\n"
        "Client,240128,63169,3366585,205567,3303540,2438836,3212343,3163902,1583362,661864,1011551,1002934,11594314,8659466\n"
        "DII,43377,23164,287023,4451007,4676,34529,326,40,4567,39726,211516,10497,413898,4696550\n"
        "FII,24157,226790,3433551,2884325,536783,1018674,766803,424314,74429,163476,177472,86926,5251070,4566630\n"
        "Pro,41362,35901,806481,352741,1056433,967418,921960,871201,691079,814550,952898,579259,4377323,3713960\n"
    )
    p = parse_csv(sample, date(2026, 8, 28))
    assert p is not None
    assert p.fii_index_fut_net == 24157 - 226790          # FII net short index futures
    assert p.client_index_fut_net == 240128 - 63169       # retail net long, the usual mirror
    assert p.fii_index_call_net == 536783 - 766803
    assert p.fii_index_put_net == 1018674 - 424314
    assert p.fii_net_options_bias == p.fii_index_call_net - p.fii_index_put_net
    f = features(p.to_dict())
    assert abs(f["fii_fut_net_lakh"] - round(p.fii_index_fut_net / 1e5, 4)) < 1e-9
    assert features(None)["participant_oi_stale"] == 1.0
    assert parse_csv("junk", date(2026, 8, 28)) is None
    print("participant_oi.py self-check ok — FII net index futures:", p.fii_index_fut_net)
