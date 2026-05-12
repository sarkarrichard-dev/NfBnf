from __future__ import annotations

import json
import os
import re
from itertools import islice
from typing import Any

from trading_ai_engine.secrets_bridge import env_or_local

_MAX_HF_SPEC_LEN = 256
_HF_SPEC_SAFE = re.compile(r"^[A-Za-z0-9_./:\-]+$")


def _sanitize_hf_learning_dataset_spec(spec: str) -> str | None:
    """
    Reject path traversal, control chars, and oversized env values before ``load_dataset``.
    """
    s = spec.strip()
    if not s or len(s) > _MAX_HF_SPEC_LEN:
        return None
    if ".." in s or "\n" in s or "\r" in s or "\x00" in s:
        return None
    if not _HF_SPEC_SAFE.match(s):
        return None
    return s


def online_learning_status() -> dict[str, Any]:
    """Operator snapshot for Hugging Face Hub streaming (no local uploads)."""
    spec = (os.environ.get("TRADING_AI_HF_LEARNING_DATASETS") or "").strip()
    token = bool((env_or_local("HF_TOKEN") or env_or_local("HUGGING_FACE_HUB_TOKEN") or "").strip())
    deps = False
    try:
        import datasets  # noqa: F401

        deps = True
    except ImportError:
        pass
    return {
        "hf_learning_datasets_env": spec or None,
        "hub_token_configured": token,
        "datasets_package_installed": deps,
        "parse_format": "repo OR repo:config OR repo:config:split (comma-separated list, max 4)",
        "install_hint": 'Install optional deps: pip install -e ".[hf]"',
    }


def parse_hub_dataset_spec(spec: str) -> tuple[str, str | None, str]:
    """Return (repo_id, config_name_or_none, split_name)."""
    parts = [p.strip() for p in spec.split(":") if p.strip()]
    if len(parts) == 1:
        return parts[0], None, "train"
    if len(parts) == 2:
        second = parts[1].lower()
        if second in ("train", "validation", "validation_matched", "test", "all"):
            return parts[0], None, parts[1]
        return parts[0], parts[1], "train"
    return parts[0], parts[1], parts[2]


def build_hf_online_learning_digest(
    *,
    max_rows_per_dataset: int = 20,
    max_total_chars: int = 8000,
) -> tuple[str | None, dict[str, Any]]:
    """
    Stream a **small** row sample from each configured Hub dataset (online only).

    ``TRADING_AI_HF_LEARNING_DATASETS`` — comma-separated entries, each
    ``namespace/name``, ``namespace/name:config``, or ``namespace/name:config:split``.

    Requires ``pip install -e ".[hf]"``. Private repos need ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN``.
    """
    raw = (os.environ.get("TRADING_AI_HF_LEARNING_DATASETS") or "").strip()
    meta: dict[str, Any] = {"datasets": [], "status": "ok"}
    if not raw:
        meta["status"] = "not_configured"
        meta["hint"] = (
            "Set TRADING_AI_HF_LEARNING_DATASETS (e.g. ag_news for a smoke test, or your org/dataset:split). "
            "Avoid legacy script-only datasets on newer `datasets` builds."
        )
        return None, meta

    try:
        from datasets import load_dataset
    except ImportError:
        meta["status"] = "missing_dependency"
        meta["hint"] = 'Run: pip install -e ".[hf]"'
        return None, meta

    chunks: list[str] = []
    specs_in: list[str] = []
    for part in raw.split(","):
        cleaned = _sanitize_hf_learning_dataset_spec(part)
        if cleaned:
            specs_in.append(cleaned)
        elif part.strip():
            meta.setdefault("skipped_invalid_specs", []).append(part.strip()[:80])
    for spec in specs_in[:4]:
        repo, config, split = parse_hub_dataset_spec(spec)
        entry: dict[str, Any] = {"spec": spec, "repo": repo, "config": config, "split": split}
        try:
            if config:
                ds = load_dataset(
                    repo,
                    config,
                    split=split,
                    streaming=True,
                    trust_remote_code=False,
                )
            else:
                ds = load_dataset(
                    repo,
                    split=split,
                    streaming=True,
                    trust_remote_code=False,
                )
            rows: list[dict[str, Any]] = []
            for row in islice(iter(ds), max_rows_per_dataset):
                if isinstance(row, dict):
                    keys = list(row.keys())[:24]
                    rows.append({k: row[k] for k in keys})
                else:
                    try:
                        keys = list(row.keys())[:24]  # type: ignore[attr-defined]
                        rows.append({k: row[k] for k in keys})  # type: ignore[index]
                    except Exception:
                        rows.append({"_row": repr(row)[:400]})
            entry["rows_sampled"] = len(rows)
            entry["ok"] = True
            blob = json.dumps(rows, default=str, ensure_ascii=False)
            chunks.append(f"=== Hub dataset {spec} (n={len(rows)}, streaming sample) ===\n{blob}")
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)[:500]
        meta["datasets"].append(entry)

    text = "\n\n".join(chunks)[:max_total_chars]
    if not text.strip():
        meta["status"] = "empty_or_all_failed"
        return None, meta
    return text, meta
