"""Hugging Face learning: FinBERT sentiment on trade setups + exportable outcome dataset."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from index_ai.config import MEMORY_DIR
from index_ai.market_clock import format_ist_display, now_ist_iso

_log = logging.getLogger(__name__)

HF_DIR = MEMORY_DIR / "hf"
DATASET_PATH = HF_DIR / "outcomes.jsonl"
META_PATH = HF_DIR / "hf_meta.json"

DEFAULT_SENTIMENT_MODEL = "ProsusAI/finbert"
INFERENCE_URL = "https://api-inference.huggingface.co/models"
DEFAULT_MIN_POSITIVE_PROB = 0.42
NEGATIVE_BLOCK_SCORE = 0.72


def _hf_token() -> str:
    return os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACE_TOKEN", "").strip()


def _sentiment_model() -> str:
    return (
        os.getenv("HF_SENTIMENT_MODEL", DEFAULT_SENTIMENT_MODEL).strip() or DEFAULT_SENTIMENT_MODEL
    )


def _dataset_repo() -> str:
    return os.getenv("HF_DATASET_REPO", "").strip()


def _bucket_uri() -> str:
    """HF Buckets path (hf://buckets/owner/name), not a Datasets repo."""
    raw = os.getenv("HF_BUCKET", "").strip()
    if not raw:
        return ""
    if raw.startswith("hf://"):
        return raw
    return f"hf://buckets/{raw.strip('/')}"


def build_setup_narrative(
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    instrument_key: str,
) -> str:
    """Text description of a setup for FinBERT / dataset rows."""
    opt = option or {}
    action = str(signal.get("action") or "NO_TRADE")
    side = str(opt.get("option_type") or ("CALL" if "CALL" in action else "PUT"))
    strike = opt.get("strike")
    pcr = opt.get("chain_pcr") or opt.get("pcr")
    parts = [
        f"{instrument_key} index options setup.",
        f"Signal {action} with strategy confidence {float(signal.get('confidence') or 0):.0%}.",
        str(signal.get("reason") or "").strip(),
        f"Underlying near {float(signal.get('price') or 0):.0f},",
        f"CPR top {float(signal.get('tc') or 0):.0f}, bottom {float(signal.get('bc') or 0):.0f}.",
        f"EMA fast {float(signal.get('ema_fast') or 0):.2f} vs slow {float(signal.get('ema_slow') or 0):.2f}.",
        f"Selected {side} strike {strike}." if strike else f"Selected {side}.",
    ]
    if pcr is not None:
        parts.append(f"Put-call ratio {float(pcr):.2f}.")
    if opt.get("chain_bias"):
        parts.append(f"OI bias {opt['chain_bias']}.")
    return " ".join(p for p in parts if p)


def _load_meta() -> dict[str, Any]:
    if not META_PATH.is_file():
        return {}
    try:
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}  # a torn write must not crash callers — it rebuilds on next sync


def _save_meta(meta: dict[str, Any]) -> None:
    HF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = META_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, META_PATH)


def _parse_sentiment_result(raw: Any) -> dict[str, Any]:
    """Normalize HF pipeline / Inference API output to positive/negative probabilities."""
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        if raw and isinstance(raw[0], list):
            items = raw[0]
        else:
            items = raw
    if not items:
        return {"label": "unknown", "score": 0.0, "positive_prob": 0.5, "negative_prob": 0.5}

    scored: dict[str, float] = {}
    for row in items:
        label = str(row.get("label") or "").lower()
        scored[label] = float(row.get("score") or 0.0)

    pos = scored.get("positive") or scored.get("bullish") or scored.get("label_1") or 0.0
    neg = scored.get("negative") or scored.get("bearish") or scored.get("label_0") or 0.0
    if pos == 0.0 and neg == 0.0:
        first = items[0]
        label = str(first.get("label") or "").lower()
        score = float(first.get("score") or 0.5)
        if "pos" in label or label == "bullish":
            pos, neg = score, 1.0 - score
        else:
            neg, pos = score, 1.0 - score
    total = pos + neg or 1.0
    pos_p = pos / total
    neg_p = neg / total
    label = "positive" if pos_p >= neg_p else "negative"
    return {
        "label": label,
        "score": round(max(pos_p, neg_p), 3),
        "positive_prob": round(pos_p, 3),
        "negative_prob": round(neg_p, 3),
    }


def _local_sentiment(text: str, model_id: str) -> dict[str, Any]:
    from transformers import pipeline

    pipe = pipeline("sentiment-analysis", model=model_id, truncation=True, max_length=512)
    raw = pipe(text[:2000])
    parsed = _parse_sentiment_result(raw)
    parsed["provider"] = "local_transformers"
    parsed["model"] = model_id
    return parsed


def _api_sentiment(text: str, model_id: str, token: str) -> dict[str, Any]:
    url = f"{INFERENCE_URL}/{model_id}"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=45.0) as client:
        response = client.post(url, headers=headers, json={"inputs": text[:2000]})
        if response.status_code == 503:
            raise RuntimeError("Hugging Face model is loading — retry in ~30 seconds.")
        response.raise_for_status()
        raw = response.json()
    parsed = _parse_sentiment_result(raw)
    parsed["provider"] = "huggingface_inference_api"
    parsed["model"] = model_id
    return parsed


def score_setup_hf(
    signal: dict[str, Any],
    option: dict[str, Any] | None,
    instrument_key: str,
    *,
    text: str | None = None,
) -> dict[str, Any]:
    """Score setup narrative with FinBERT (HF API or local transformers)."""
    narrative = text or build_setup_narrative(signal, option, instrument_key)
    model_id = _sentiment_model()
    token = _hf_token()
    use_local = os.getenv("HF_USE_LOCAL", "").strip().lower() in {"1", "true", "yes"}

    try:
        if use_local:
            parsed = _local_sentiment(narrative, model_id)
        elif token:
            parsed = _api_sentiment(narrative, model_id, token)
        else:
            return {
                "ready": False,
                "status": "no_token",
                "message": (
                    "Set HF_TOKEN in .env for Hugging Face Inference API, "
                    "or HF_USE_LOCAL=true with transformers installed."
                ),
                "narrative_preview": narrative[:240],
            }
    except ImportError:
        if not token:
            return {
                "ready": False,
                "status": "missing_deps",
                "message": "pip install transformers torch — or set HF_TOKEN for cloud inference.",
            }
        parsed = _api_sentiment(narrative, model_id, token)
    except Exception as exc:
        return {
            "ready": False,
            "status": "error",
            "message": str(exc)[:300],
            "narrative_preview": narrative[:240],
        }

    min_pos = float(os.getenv("HF_MIN_POSITIVE_PROB", str(DEFAULT_MIN_POSITIVE_PROB)))
    pos_p = float(parsed["positive_prob"])
    neg_p = float(parsed["negative_prob"])
    block = neg_p >= NEGATIVE_BLOCK_SCORE or pos_p < min_pos

    return {
        "ready": True,
        "status": "scored",
        "model": model_id,
        "label": parsed["label"],
        "score": parsed["score"],
        "positive_prob": pos_p,
        "negative_prob": neg_p,
        "min_positive_prob": min_pos,
        "passes_hf_gate": not block,
        "block_setup": block,
        "provider": parsed.get("provider"),
        "narrative_preview": narrative[:240],
        "scored_at": now_ist_iso(),
    }


def sync_hf_dataset() -> dict[str, Any]:
    """Export closed trades to JSONL for HF datasets / future fine-tuning."""
    from index_ai.learning import connect, _is_test_trade_id, _row_to_trade

    HF_DIR.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    with connect() as db:
        trade_rows = db.execute(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY created_at ASC"
        ).fetchall()

    with DATASET_PATH.open("w", encoding="utf-8") as handle:
        for raw in trade_rows:
            trade = _row_to_trade(raw)
            tid = str(trade.get("id") or "")
            if _is_test_trade_id(tid):
                continue
            signal = trade.get("signal") or {}
            option = trade.get("option") or {}
            inst = str(trade.get("instrument") or "NIFTY")
            text = build_setup_narrative(signal, option, inst)
            pnl = float(trade.get("pnl") or 0)
            record = {
                "trade_id": tid,
                "text": text,
                "label": 1 if pnl > 0 else 0,
                "win": pnl > 0,
                "pnl": pnl,
                "instrument": inst,
                "action": trade.get("action"),
                "created_at": trade.get("created_at"),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            rows_written += 1

    meta = _load_meta()
    meta.update(
        {
            "dataset_path": str(DATASET_PATH),
            "dataset_rows": rows_written,
            "dataset_synced_at": now_ist_iso(),
            "dataset_synced_at_ist": format_ist_display(now_ist_iso()),
        }
    )
    _save_meta(meta)
    return {
        "rows": rows_written,
        "path": str(DATASET_PATH),
        "synced_at_ist": meta["dataset_synced_at_ist"],
    }


def upload_bucket_to_hub() -> dict[str, Any]:
    """Sync memory/hf/ to an HF Bucket (hf://buckets/owner/name)."""
    token = _hf_token()
    bucket = _bucket_uri()
    if not token:
        return {"ok": False, "message": "HF_TOKEN is not set in .env."}
    if not bucket:
        return {
            "ok": False,
            "message": "HF_BUCKET is not set (e.g. SarkarRichard/Bnf for hf://buckets/SarkarRichard/Bnf).",
        }
    sync = sync_hf_dataset()
    if sync["rows"] < 1 and not DATASET_PATH.is_file():
        return {"ok": False, "message": "No closed trades to upload — close trades first."}

    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        return {"ok": False, "message": "pip install huggingface-hub", "detail": str(exc)}

    api = HfApi(token=token)
    plan = api.sync_bucket(str(HF_DIR), bucket, token=token)
    meta = _load_meta()
    meta["bucket_uri"] = bucket
    meta["bucket_uploaded_at"] = now_ist_iso()
    meta["bucket_uploaded_at_ist"] = format_ist_display(now_ist_iso())
    meta["bucket_last_sync"] = {
        "uploaded": getattr(plan, "uploaded", None),
        "updated": getattr(plan, "updated", None),
    }
    _save_meta(meta)
    uploaded = getattr(plan, "uploaded", 0) or 0
    updated = getattr(plan, "updated", 0) or 0
    return {
        "ok": True,
        "bucket": bucket,
        "rows": sync["rows"],
        "files_uploaded": uploaded,
        "files_updated": updated,
        "message": (
            f"Synced {sync['rows']} trade outcomes to bucket {bucket} "
            f"({uploaded} new, {updated} updated file(s))."
        ),
    }


def upload_dataset_to_hub() -> dict[str, Any]:
    """Push outcomes to HF Bucket (if HF_BUCKET set) and/or Datasets repo (HF_DATASET_REPO)."""
    token = _hf_token()
    if not token:
        return {"ok": False, "message": "HF_TOKEN is not set in .env."}

    sync = sync_hf_dataset()
    if sync["rows"] < 1:
        return {"ok": False, "message": "No closed trades to upload — close trades first."}

    bucket = _bucket_uri()
    repo = _dataset_repo()
    if not bucket and not repo:
        return {
            "ok": False,
            "message": (
                "Set HF_BUCKET=SarkarRichard/Bnf (Buckets UI) and/or "
                "HF_DATASET_REPO=you/repo (classic dataset)."
            ),
        }

    results: list[dict[str, Any]] = []
    if bucket:
        results.append(upload_bucket_to_hub())
    if repo:
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            results.append(
                {"ok": False, "message": "pip install huggingface-hub", "detail": str(exc)}
            )
        else:
            api = HfApi(token=token)
            api.create_repo(repo_id=repo, repo_type="dataset", exist_ok=True, private=True)
            api.upload_file(
                path_or_fileobj=str(DATASET_PATH),
                path_in_repo="outcomes.jsonl",
                repo_id=repo,
                repo_type="dataset",
                commit_message=f"Sync {sync['rows']} trade outcomes",
            )
            meta = _load_meta()
            meta["hub_repo"] = repo
            meta["hub_uploaded_at"] = now_ist_iso()
            meta["hub_uploaded_at_ist"] = format_ist_display(now_ist_iso())
            _save_meta(meta)
            results.append(
                {
                    "ok": True,
                    "repo": repo,
                    "rows": sync["rows"],
                    "message": f"Uploaded {sync['rows']} rows to dataset {repo}",
                }
            )

    ok = all(r.get("ok") for r in results)
    messages = [r.get("message") for r in results if r.get("message")]
    meta = _load_meta()
    return {
        "ok": ok,
        "rows": sync["rows"],
        "bucket": bucket or meta.get("bucket_uri"),
        "repo": repo or meta.get("hub_repo"),
        "message": " ".join(messages) if messages else "Upload finished.",
        "details": results,
    }


def update_hf_learning() -> dict[str, Any]:
    """Sync dataset + summarize HF availability for the learning panel."""
    token = _hf_token()
    model_id = _sentiment_model()
    dataset = sync_hf_dataset()
    meta = _load_meta()

    status: dict[str, Any] = {
        "enabled": bool(token)
        or os.getenv("HF_USE_LOCAL", "").strip().lower() in {"1", "true", "yes"},
        "token_configured": bool(token),
        "model": model_id,
        "dataset_rows": dataset["rows"],
        "dataset_path": dataset["path"],
        "dataset_synced_at_ist": dataset.get("synced_at_ist"),
        "hub_repo": _dataset_repo() or meta.get("hub_repo"),
        "hub_last_upload_ist": meta.get("hub_uploaded_at_ist"),
        "bucket_uri": _bucket_uri() or meta.get("bucket_uri"),
        "bucket_last_upload_ist": meta.get("bucket_uploaded_at_ist"),
    }

    if not status["enabled"]:
        status["ready"] = False
        status["status"] = "configure"
        status["message"] = (
            "Add HF_TOKEN to .env (free at huggingface.co/settings/tokens) "
            "to score setups with FinBERT, or set HF_USE_LOCAL=true."
        )
        return status

    status["ready"] = True
    status["status"] = "active"
    status["message"] = (
        f"Hugging Face active — {model_id} scores each new setup; "
        f"{dataset['rows']} closed trades in local dataset."
    )
    return status


def load_hf_status() -> dict[str, Any]:
    meta = _load_meta()
    base = update_hf_learning()
    return {**base, **{k: v for k, v in meta.items() if k not in base}}
