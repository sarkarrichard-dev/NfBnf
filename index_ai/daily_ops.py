"""
Unattended daily work: measure during the session, review after it.

Everything here runs from the scanner loop without anyone watching, because the
things this platform most needs are things you cannot do retrospectively:

  * **Spread sampling** must happen while the book is live. It used to run only
    inside the options paper tick, which meant that with paper lanes disabled the
    single most consequential number in the cost model was never measured. It is
    now its own stage, independent of whether any lane is trading.
  * **The brain retrains after the close**, on the day's closed trades, so the
    next session starts from everything known so far rather than from whenever
    someone last clicked a button.
  * **An end-of-day report is written to disk**, so a session's findings survive
    without anyone reading raw JSON.

All of it is best-effort: a failure here records itself and never touches trading.
"""

from __future__ import annotations

import json
import os
from typing import Any

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import now_ist, today_ist_date

STATE_PATH = MEMORY_DIR / "daily_ops.json"
REPORT_DIR = MEMORY_DIR / "daily_reports"


def _state() -> dict[str, Any]:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(state: dict[str, Any]) -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def sample_spreads_enabled() -> bool:
    """On by default — measuring the book costs one chain fetch per index."""
    return os.getenv("ENABLE_SPREAD_SAMPLING", "true").strip().lower() in {"1", "true", "yes", "on"}


def sampling_instruments() -> list[str]:
    from index_ai.instruments import index_paused

    raw = os.getenv("SPREAD_SAMPLE_INSTRUMENTS", "NIFTY,BANKNIFTY,SENSEX")
    return [x.strip().upper() for x in raw.split(",") if x.strip() and not index_paused(x.strip())]


def sample_spreads(client: Any) -> dict[str, Any]:
    """Sample the live option book for every configured index.

    Independent of the paper lanes: the cost model needs measuring whether or not
    anything is trading, and the book only exists during market hours.
    """
    from index_ai.instruments import get_instrument
    from index_ai.market_clock import is_market_open
    from index_ai.market_context import oi_flow
    from index_ai.market_context.spread_calib import observe
    from index_ai.strategies.options_cpr.config import config_for
    from index_ai.strategies.options_cpr.live_chain import ChainBook

    out: dict[str, Any] = {"at": now_ist().isoformat(timespec="seconds"), "sampled": {}}
    if not (sample_spreads_enabled() and is_market_open()):
        out["skipped"] = "sampling disabled" if not sample_spreads_enabled() else "market closed"
        return out

    for key in sampling_instruments():
        try:
            inst = get_instrument(key)
            book = ChainBook.fetch(client, inst)
            if book is None:
                out["sampled"][key] = {"error": "no chain"}
                continue
            spot = float(client.index_ltp(inst)["last_price"])
            cfg = config_for(key)
            step = cfg.strike_step
            atm = round(spot / step) * step
            wing_dn = round(spot * (1 - cfg.sell_wing_pct) / step) * step
            wing_up = round(spot * (1 + cfg.sell_wing_pct) / step) * step
            n = observe(
                book,
                key,
                spot,
                strikes=[
                    (atm, True),
                    (atm, False),
                    (atm + step, True),
                    (atm - step, False),
                    (wing_dn, False),
                    (wing_up, True),
                ],
            )
            out["sampled"][key] = {"samples": n, "spot": round(spot, 2)}
            rows = getattr(book, "_rows", None)
            if rows:
                oi_flow.record_snapshot(key, getattr(book, "expiry", ""), rows, spot)
            _log_observation(key, book, spot, rows)
        except Exception as exc:
            out["sampled"][key] = {"error": str(exc)[:160]}
    return out


def _log_observation(key: str, book: Any, spot: float, rows: Any) -> None:
    """One market snapshot per index per cycle into the time-series log."""
    try:
        from index_ai.market_context import context as mkt
        from index_ai.market_context.oi_flow import chain_oi, max_pain
        from index_ai.market_context.spread_calib import bucket_half_spreads
        from index_ai.market_log import record_observation
        from index_ai.strategies.options_cpr.config import config_for

        cfg = config_for(key)
        atm = round(spot / cfg.strike_step) * cfg.strike_step
        q = book.quote(atm, True)
        near, wing = bucket_half_spreads(key)
        ce = pe = pcr = mp = None
        if rows:
            oi = chain_oi(rows)
            ce, pe = sum(oi["ce"].values()), sum(oi["pe"].values())
            pcr = (pe / ce) if ce else None
            mp = max_pain(rows)
        ctx = mkt.latest() or {}
        record_observation(
            key,
            spot=spot,
            atm_iv=(q.iv if q else None),
            pcr=pcr,
            max_pain=mp,
            ce_oi=ce,
            pe_oi=pe,
            near_half_spread=near,
            wing_half_spread=wing,
            vix=(ctx.get("vix") or {}).get("last"),
            regime=((ctx.get("conditions") or {}).get("allow_selling") and "sell_ok")
            or "sell_blocked",
            expiry=getattr(book, "expiry", None),
        )
    except Exception:
        pass


def eod_due() -> bool:
    """At Indian market close (15:30 IST), once per trading day. The summary
    goes out at the close whether or not the book is flat — if the square-off
    left anything open it is listed in the message (day_review carries
    ``open_trades``)."""

    from index_ai.market_clock import is_trading_day, session_times

    if not is_trading_day():
        return False
    if now_ist().time() < session_times()["market_close"]:
        return False
    return _state().get("eod_date") != today_ist_date()


def run_eod() -> dict[str, Any]:
    """Retrain the brain on today's closed trades, then write the day's report."""
    today = today_ist_date()
    report: dict[str, Any] = {
        "date": today,
        "generated_at_ist": now_ist().isoformat(timespec="seconds"),
    }

    try:
        from index_ai.brain.model import train

        report["brain"] = train()
    except Exception as exc:
        report["brain"] = {"trained": False, "reason": str(exc)[:200]}

    try:
        from index_ai.market_context.spread_calib import status as spread_status

        report["spreads"] = spread_status()
    except Exception as exc:
        report["spreads"] = {"error": str(exc)[:200]}

    try:
        from index_ai.strategies.options_cpr.viability import report as viability_report

        report["viability"] = viability_report()
    except Exception as exc:
        report["viability"] = {"error": str(exc)[:200]}

    for name, path in (
        ("options_cpr", "index_ai.strategies.options_cpr.paper:options_cpr_paper_status"),
        ("futures", "index_ai.strategies.futures.paper:futures_paper_status"),
    ):
        try:
            mod_name, attr = path.split(":")
            mod = __import__(mod_name, fromlist=[attr])
            st = getattr(mod, attr)()
            report[name] = {k: st.get(k) for k in ("enabled", "today", "all_time")}
        except Exception as exc:
            report[name] = {"error": str(exc)[:160]}

    try:
        from index_ai.brain.commentary import generate

        report["commentary"] = generate("eod")["text"]
    except Exception as exc:
        report["commentary"] = f"(unavailable: {exc})"

    try:  # today's trade log + AI review, ready before anyone opens the dashboard
        from index_ai.day_review import build_day_review

        dr = build_day_review(refresh=True)
        report["day_review"] = {"summary": dr["summary"], "review": dr["review"]}
        try:
            from index_ai.notify import day_report

            day_report(dr["summary"])
        except Exception:
            pass
    except Exception as exc:
        report["day_review"] = {"error": str(exc)[:200]}

    # Off-machine backup before the report is written, so the snapshot it uploads
    # is of a settled memory/ dir; the report's own backup status is one run behind.
    try:
        from index_ai.cloud_backup import run_backup

        report["backup"] = run_backup()
    except Exception as exc:
        report["backup"] = {"error": str(exc)[:200]}

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"{today}.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        (REPORT_DIR / f"{today}.md").write_text(render_markdown(report), encoding="utf-8")
    except Exception:
        pass

    # Runs at the close now, so it's a one-shot — stamp the day and never rebuild
    # it. Anything the square-off left open is in the summary as `open_trades`.
    summ = (report.get("day_review") or {}).get("summary") or {}
    st = _state()
    st["eod_date"] = today
    st["last_eod"] = {
        "at": report["generated_at_ist"],
        "brain_trained": bool((report.get("brain") or {}).get("trained")),
        "open_at_close": int(summ.get("open") or 0),
    }
    _save(st)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    b = report.get("brain") or {}
    lines = [f"# Session report — {report.get('date')}\n"]
    if report.get("commentary"):
        lines += [str(report["commentary"]), ""]

    lines.append("## Spread calibration\n")
    for key, d in ((report.get("spreads") or {}).get("instruments") or {}).items():
        near = (d.get("near") or {}).get("median_pts")
        wing = (d.get("wing") or {}).get("median_pts")
        lines.append(
            f"- **{key}**: {d.get('samples', 0)} samples, source `{d.get('source')}`"
            + (f", near {near}pt / wing {wing}pt" if near is not None else "")
            + (f" — skips: {d.get('skipped')}" if d.get("skipped") else "")
        )

    lines.append("\n## Lane viability (measured cost floor vs measured edge)\n")
    for key, lanes in ((report.get("viability") or {}).get("instruments") or {}).items():
        for lane, d in lanes.items():
            lines.append(
                f"- **{key} {lane}**: floor ₹{d.get('friction_floor_rupees')}, "
                f"gross/trade ₹{d.get('gross_per_trade_rupees')} → **{d.get('verdict')}**"
            )

    lines.append("\n## Brain\n")
    if b.get("trained"):
        wf = b.get("walk_forward") or {}
        lines.append(
            f"- Retrained on {b.get('rows')} rows ({b.get('live_rows')} live). "
            f"Gate {'ARMED' if b.get('gate_armed') else 'not armed'}; "
            f"walk-forward OOS {wf.get('oos_static_rupees')} → {wf.get('oos_gated_rupees')}."
        )
    else:
        lines.append(f"- Not retrained: {b.get('reason')}")

    for lane in ("options_cpr", "futures"):
        d = report.get(lane) or {}
        if d.get("enabled"):
            t, a = d.get("today") or {}, d.get("all_time") or {}
            lines.append(
                f"\n## {lane}\n- Today: {t.get('closed', 0)} closed, ₹{t.get('net_rupees', 0):,.0f}"
                f"\n- All time: {a.get('closed', 0)} trades, ₹{a.get('net_rupees', 0):,.0f}"
            )
    return "\n".join(lines) + "\n"


def latest_report() -> dict[str, Any] | None:
    if not REPORT_DIR.is_dir():
        return None
    files = sorted(REPORT_DIR.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":  # ponytail self-check
    md = render_markdown(
        {
            "date": "2026-08-31",
            "commentary": "Quiet session.",
            "spreads": {
                "instruments": {
                    "NIFTY": {
                        "samples": 48,
                        "source": "observed (48 samples)",
                        "near": {"median_pts": 0.2},
                        "wing": {"median_pts": 0.6},
                    }
                }
            },
            "viability": {
                "instruments": {
                    "NIFTY": {
                        "sell": {
                            "friction_floor_rupees": 154,
                            "gross_per_trade_rupees": 211,
                            "verdict": "VIABLE",
                        }
                    }
                }
            },
            "brain": {
                "trained": True,
                "rows": 200,
                "live_rows": 200,
                "gate_armed": False,
                "walk_forward": {"oos_static_rupees": 100, "oos_gated_rupees": 50},
            },
            "options_cpr": {
                "enabled": True,
                "today": {"closed": 2, "net_rupees": 300.0},
                "all_time": {"closed": 9, "net_rupees": -120.0},
            },
        }
    )
    assert "NIFTY" in md and "VIABLE" in md and "near 0.2pt" in md
    assert "Gate not armed" in md
    assert (
        render_markdown({"date": "x", "brain": {"trained": False, "reason": "too few rows"}}).count(
            "too few rows"
        )
        == 1
    )
    assert sampling_instruments() == ["NIFTY", "BANKNIFTY", "SENSEX"]

    class _NoClient:
        pass

    out = sample_spreads(_NoClient())  # market closed -> clean skip, no raise
    assert "skipped" in out or out["sampled"] == {}
    print("daily_ops.py self-check ok")
