from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from trading_ai_engine.india.constituents import get_indices_catalog
from trading_ai_engine.india.nse_yahoo import is_nse_yahoo_symbol
from trading_ai_engine.market_yfinance import history
from trading_ai_engine.ml.candlestick_patterns import calibrate_patterns
from trading_ai_engine.ml.intraday_tf import expand_base_5m_to_timeframes
from trading_ai_engine.ml.training_set import FEATURE_COLUMNS, LabelConfig, make_supervised_frame
from trading_ai_engine.server.paths import DATA_DIR

MARKET_DATA_DIR = DATA_DIR / "Internet Market Data - 7 Years"
INTRADAY_DATA_DIR = DATA_DIR / "Internet Market Data - Intraday"
MODEL_DIR = DATA_DIR / "AI Models"
MARKET_MODEL_PATH = MODEL_DIR / "Market Brain Model.json"
TRAINING_FRAME_PATH = MARKET_DATA_DIR / "Training Rows for AI.csv"


@dataclass(frozen=True)
class InternetDatasetConfig:
    years: int = 7
    period: str = "7y"
    horizon_bars: int = 5
    up_threshold: float = 0.006
    down_threshold: float = -0.006
    max_symbols: int = 80
    intraday_symbols_cap: int = 30
    intraday_period: str = "60d"


def _yahoo_download_skip_set() -> set[str]:
    raw = os.environ.get("TRADING_AI_SKIP_YAHOO_SYMBOLS", "").strip()
    if not raw:
        return set()
    out: set[str] = set()
    for part in raw.replace("|", ",").split(","):
        s = part.strip()
        if s:
            out.add(s)
    return out


def default_indian_symbols(limit: int | None = None) -> list[str]:
    catalog = get_indices_catalog()
    skip = _yahoo_download_skip_set()
    seen: set[str] = set()
    symbols: list[str] = ["^NSEI", "^NSEBANK"]
    seen.update(symbols)
    for cat in catalog.get("categories") or []:
        for idx in cat.get("indices") or []:
            for stock in idx.get("stocks") or []:
                sym = str(stock.get("symbol") or "").strip()
                if sym and sym not in seen and sym not in skip and is_nse_yahoo_symbol(sym):
                    seen.add(sym)
                    symbols.append(sym)
    return symbols[:limit] if limit else symbols


def _safe_symbol_name(symbol: str) -> str:
    return symbol.replace("^", "INDEX_").replace(".", "_").replace("/", "_")


def download_indian_market_history(
    *,
    symbols: list[str] | None = None,
    config: InternetDatasetConfig | None = None,
) -> dict[str, Any]:
    cfg = config or InternetDatasetConfig()
    MARKET_DATA_DIR.mkdir(parents=True, exist_ok=True)
    selected = symbols or default_indian_symbols(cfg.max_symbols)
    manifest: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []

    for symbol in selected:
        try:
            df = history(symbol, period=cfg.period, interval="1d", auto_adjust=False)
        except Exception as exc:
            manifest.append({"symbol": symbol, "rows": 0, "error": str(exc)})
            continue
        if df.empty:
            manifest.append({"symbol": symbol, "rows": 0, "error": "empty_history"})
            continue
        df = df.copy()
        df["symbol"] = symbol
        out_path = MARKET_DATA_DIR / f"{_safe_symbol_name(symbol)}.csv"
        df.to_csv(out_path, index=False)
        frames.append(df)
        manifest.append(
            {
                "symbol": symbol,
                "rows": int(len(df)),
                "date_min": str(df["date"].min()),
                "date_max": str(df["date"].max()),
                "path": str(out_path.relative_to(DATA_DIR.parent)),
                "error": None,
            }
        )

    combined_rows = int(sum(len(x) for x in frames))
    manifest_path = MARKET_DATA_DIR / "manifest.json"
    manifest_payload = {
        "source": "Yahoo Finance via yfinance",
        "period": cfg.period,
        "interval": "1d",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "symbols_requested": len(selected),
        "symbols_ok": sum(1 for x in manifest if not x.get("error")),
        "rows": combined_rows,
        "files": manifest,
    }
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, default=str), encoding="utf-8")
    return manifest_payload


def _intraday_label_config(cfg: InternetDatasetConfig) -> LabelConfig:
    """Tighter return thresholds on intraday bars (same horizon count in bars)."""
    return LabelConfig(
        horizon_bars=min(cfg.horizon_bars, 10),
        up_threshold=0.002,
        down_threshold=-0.002,
    )


def download_indian_intraday_5m(
    *,
    symbols: list[str] | None = None,
    config: InternetDatasetConfig | None = None,
) -> dict[str, Any]:
    """
    Download recent 5m Yahoo bars (typically up to ~60 trading days) for multi-timeframe
    expansion (5m–3h) used in candlestick training and calibration.
    """
    cfg = config or InternetDatasetConfig()
    INTRADAY_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cap = max(1, min(int(cfg.intraday_symbols_cap), int(cfg.max_symbols)))
    selected = symbols or default_indian_symbols(cap)
    manifest: list[dict[str, Any]] = []
    for symbol in selected:
        try:
            df = history(symbol, period=cfg.intraday_period, interval="5m", auto_adjust=False)
        except Exception as exc:
            manifest.append({"symbol": symbol, "rows": 0, "error": str(exc)})
            continue
        if df.empty:
            manifest.append({"symbol": symbol, "rows": 0, "error": "empty_history"})
            continue
        df = df.copy()
        df["symbol"] = symbol
        out_path = INTRADAY_DATA_DIR / f"{_safe_symbol_name(symbol)}_5m.csv"
        df.to_csv(out_path, index=False)
        manifest.append(
            {
                "symbol": symbol,
                "rows": int(len(df)),
                "path": str(out_path.relative_to(DATA_DIR.parent)),
                "error": None,
            }
        )
    mp = INTRADAY_DATA_DIR / "intraday_manifest.json"
    payload = {
        "source": "Yahoo Finance intraday 5m",
        "period": cfg.intraday_period,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "symbols_ok": sum(1 for x in manifest if not x.get("error")),
        "files": manifest,
    }
    mp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def _build_intraday_supervised_frames(cfg: InternetDatasetConfig) -> pd.DataFrame | None:
    icfg = _intraday_label_config(cfg)
    chunks: list[pd.DataFrame] = []
    if not INTRADAY_DATA_DIR.is_dir():
        return None
    for path in sorted(INTRADAY_DATA_DIR.glob("*_5m.csv")):
        try:
            raw = pd.read_csv(path)
        except Exception:
            continue
        if raw.empty or "close" not in raw.columns:
            continue
        sym = str(raw["symbol"].iloc[0]) if "symbol" in raw.columns else ""
        expanded = expand_base_5m_to_timeframes(raw)
        for tf_label, chunk in expanded.items():
            if chunk.empty or len(chunk) < 80:
                continue
            frame = make_supervised_frame(chunk, icfg, interval=tf_label)
            if frame.empty:
                continue
            frame["symbol"] = sym or path.stem.replace("_5m", "").replace("INDEX_", "^").replace("_NS", ".NS")
            chunks.append(frame)
    if not chunks:
        return None
    return pd.concat(chunks, ignore_index=True)


def build_market_training_frame(config: InternetDatasetConfig | None = None) -> dict[str, Any]:
    cfg = config or InternetDatasetConfig()
    label_cfg = LabelConfig(
        horizon_bars=cfg.horizon_bars,
        up_threshold=cfg.up_threshold,
        down_threshold=cfg.down_threshold,
    )
    rows: list[pd.DataFrame] = []
    for path in sorted(MARKET_DATA_DIR.glob("*.csv")):
        if path.name == TRAINING_FRAME_PATH.name:
            continue
        df = pd.read_csv(path)
        if "symbol" not in df.columns:
            df["symbol"] = path.stem.replace("_NS", ".NS")
        frame = make_supervised_frame(df, label_cfg, interval="1d")
        if frame.empty:
            continue
        frame["symbol"] = str(df["symbol"].iloc[0])
        rows.append(frame)

    intra = _build_intraday_supervised_frames(cfg)
    if intra is not None and not intra.empty:
        rows.append(intra)

    if not rows:
        return {"status": "empty", "rows": 0, "path": str(TRAINING_FRAME_PATH)}

    out = pd.concat(rows, ignore_index=True)
    MARKET_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(TRAINING_FRAME_PATH, index=False)
    labels = out["label"].value_counts().sort_index()
    return {
        "status": "ok",
        "rows": int(len(out)),
        "symbols": int(out["symbol"].nunique()),
        "features": list(FEATURE_COLUMNS),
        "labels": {str(int(k)): int(v) for k, v in labels.items()},
        "path": str(TRAINING_FRAME_PATH),
        "date_min": str(out["date"].min()),
        "date_max": str(out["date"].max()),
    }


def _standardize(
    frame: pd.DataFrame, feature_cols: list[str]
) -> tuple[pd.DataFrame, dict[str, float], dict[str, float]]:
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    z = pd.DataFrame(index=frame.index)
    for col in feature_cols:
        s = frame[col].astype(float)
        mean = float(s.mean())
        scale = float(s.std(ddof=0)) or 1.0
        means[col] = mean
        scales[col] = scale
        z[col] = (s - mean) / scale
    return z, means, scales


def train_market_model() -> dict[str, Any]:
    if not TRAINING_FRAME_PATH.is_file():
        built = build_market_training_frame()
        if built.get("status") != "ok":
            return {"status": "empty", "message": "No training frame available.", "dataset": built}

    frame = pd.read_csv(TRAINING_FRAME_PATH)
    for c in FEATURE_COLUMNS:
        if c not in frame.columns:
            frame[c] = 0.0
    drop_cols = [c for c in FEATURE_COLUMNS if c in frame.columns]
    frame = frame.dropna(subset=drop_cols + ["future_return"])
    if frame.empty:
        return {"status": "empty", "message": "Training frame has no usable rows."}

    sort_cols = ["date", "symbol"] + (["interval"] if "interval" in frame.columns else [])
    frame = frame.sort_values(sort_cols).reset_index(drop=True)
    split = max(1, int(len(frame) * 0.8))
    train = frame.iloc[:split].copy()
    test = frame.iloc[split:].copy()
    feature_cols = [c for c in FEATURE_COLUMNS if c in train.columns]
    z_train, means, scales = _standardize(train, feature_cols)
    target = train["future_return"].astype(float)

    weights: dict[str, float] = {}
    for col in feature_cols:
        x = z_train[col].astype(float)
        denom = float((x * x).mean()) + 1e-9
        weights[col] = float((x * target).mean() / denom)

    abs_sum = sum(abs(v) for v in weights.values()) or 1.0
    weights = {k: v / abs_sum for k, v in weights.items()}
    intercept = float(target.mean())

    def _score(row: pd.Series) -> float:
        raw = intercept
        for col in feature_cols:
            raw += ((float(row[col]) - means[col]) / scales[col]) * weights[col]
        return math.tanh(raw * 18.0)

    scored = test.copy() if not test.empty else train.tail(max(1, min(200, len(train)))).copy()
    for c in feature_cols:
        if c not in scored.columns:
            scored[c] = 0.0
    scored["model_score"] = scored.apply(_score, axis=1)
    scored["pred"] = 0
    scored.loc[scored["model_score"] > 0.18, "pred"] = 1
    scored.loc[scored["model_score"] < -0.18, "pred"] = -1
    scored["actual"] = 0
    scored.loc[scored["future_return"] > 0, "actual"] = 1
    scored.loc[scored["future_return"] < 0, "actual"] = -1
    directional = float((scored["pred"] == scored["actual"]).mean()) if len(scored) else 0.0
    active = scored[scored["pred"] != 0]
    active_accuracy = float((active["pred"] == active["actual"]).mean()) if len(active) else 0.0

    if "interval" not in train.columns:
        train["interval"] = "1d"
    pattern_calibration = calibrate_patterns(train, min_samples=max(30, int(0.0005 * len(train))))

    prev = load_market_model()
    live_overlay = (prev or {}).get("pattern_live_overlay") if prev else None

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = {
        "version": "market_brain_linear_v3_cpr_ema",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "source": "Yahoo Finance via yfinance (daily + optional intraday 5m→3h; CPR + EMA + candles)",
        "training_rows": int(len(train)),
        "test_rows": int(len(test)),
        "features": list(feature_cols),
        "means": means,
        "scales": scales,
        "weights": weights,
        "intercept": intercept,
        "pattern_calibration": pattern_calibration,
        "metrics": {
            "directional_accuracy": round(directional, 4),
            "active_accuracy": round(active_accuracy, 4),
            "active_predictions": int(len(active)),
        },
    }
    if live_overlay:
        model["pattern_live_overlay"] = live_overlay
    MARKET_MODEL_PATH.write_text(json.dumps(model, indent=2, default=str), encoding="utf-8")
    return {"status": "ok", "model_path": str(MARKET_MODEL_PATH), "model": model}


def load_market_model() -> dict[str, Any] | None:
    if not MARKET_MODEL_PATH.is_file():
        return None
    return json.loads(MARKET_MODEL_PATH.read_text(encoding="utf-8"))


def infer_market_model_score(metrics: dict[str, Any]) -> dict[str, Any] | None:
    model = load_market_model()
    if not model:
        return None
    raw = float(model.get("intercept") or 0.0)
    missing: list[str] = []
    for col in model.get("features") or FEATURE_COLUMNS:
        value = metrics.get(col)
        if value is None and col == "ret_1":
            value = metrics.get("ret_1d")
        if value is None:
            c = str(col)
            if c.startswith("pat_") or c.startswith("cpr_") or c.startswith("ema"):
                value = 0.0
            else:
                missing.append(col)
                continue
        mean = float((model.get("means") or {}).get(col) or 0.0)
        scale = float((model.get("scales") or {}).get(col) or 1.0)
        weight = float((model.get("weights") or {}).get(col) or 0.0)
        raw += ((float(value) - mean) / scale) * weight
    score = math.tanh(raw * 18.0)
    return {
        "score": max(-1.0, min(1.0, score)),
        "raw": raw,
        "version": model.get("version"),
        "trained_at": model.get("trained_at"),
        "missing_features": missing,
        "metrics": model.get("metrics") or {},
    }


def learning_status() -> dict[str, Any]:
    manifest_path = MARKET_DATA_DIR / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    )
    model = load_market_model()
    frame_rows = 0
    if TRAINING_FRAME_PATH.is_file():
        try:
            frame_rows = sum(1 for _ in TRAINING_FRAME_PATH.open("r", encoding="utf-8")) - 1
        except OSError:
            frame_rows = 0
    intra_manifest_path = INTRADAY_DATA_DIR / "intraday_manifest.json"
    intra_manifest = (
        json.loads(intra_manifest_path.read_text(encoding="utf-8"))
        if intra_manifest_path.is_file()
        else None
    )
    return {
        "dataset": manifest,
        "intraday_manifest": intra_manifest,
        "training_frame": {
            "path": str(TRAINING_FRAME_PATH),
            "rows": max(0, frame_rows),
            "exists": TRAINING_FRAME_PATH.is_file(),
        },
        "model": model,
    }


def data_quality_report(*, frame_sample_rows: int = 80_000) -> dict[str, Any]:
    """
    Lightweight checks before trusting downloaded market data for training (roadmap:
    prove the data is clean). Does not replace a full data-audit pipeline.
    """
    from trading_ai_engine.ml.training_set import FEATURE_COLUMNS

    issues: list[str] = []
    warnings: list[str] = []
    manifest_path = MARKET_DATA_DIR / "manifest.json"
    manifest: dict[str, Any] | None = None
    if not manifest_path.is_file():
        issues.append("no_download_manifest_run_download_first")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues.append("manifest_json_corrupt")
        else:
            files = manifest.get("files") or []
            err_n = sum(1 for f in files if f.get("error"))
            if err_n:
                warnings.append(f"manifest_symbol_errors:{err_n}")
            rows = int(manifest.get("rows") or 0)
            if rows < 1_000:
                warnings.append("manifest_combined_rows_very_low")

    frame_info: dict[str, Any] = {"path": str(TRAINING_FRAME_PATH), "sampled_rows": 0}
    if TRAINING_FRAME_PATH.is_file():
        try:
            df = pd.read_csv(TRAINING_FRAME_PATH, nrows=frame_sample_rows, low_memory=False)
        except Exception as exc:
            issues.append(f"training_frame_unreadable:{exc}")
        else:
            frame_info["sampled_rows"] = int(len(df))
            base_need = ["date", "future_return", "label"]
            for col in base_need:
                if col not in df.columns:
                    issues.append(f"training_frame_missing_column:{col}")
            missing_feats = [c for c in FEATURE_COLUMNS if c not in df.columns]
            if missing_feats:
                warnings.append(f"training_frame_missing_feature_columns:{len(missing_feats)}")
            if not issues:
                feat_cols = [c for c in FEATURE_COLUMNS if c in df.columns]
                null_pct = df[feat_cols].isna().mean() if feat_cols else pd.Series(dtype=float)
                worst = float(null_pct.max()) if len(null_pct) else 0.0
                frame_info["worst_feature_null_pct"] = round(worst, 4)
                if worst > 0.08:
                    warnings.append("high_null_rate_in_feature_columns")
                if "label" in df.columns:
                    vc = df["label"].value_counts()
                    frame_info["label_counts"] = {str(k): int(v) for k, v in vc.items()}
                    if len(vc) < 2:
                        warnings.append("label_almost_constant")
    else:
        warnings.append("training_frame_missing_build_after_download")

    status = "fail" if issues else ("warn" if warnings else "ok")
    return {
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "manifest_path": str(manifest_path),
        "manifest_summary": {
            "rows": int((manifest or {}).get("rows") or 0),
            "symbols_ok": int((manifest or {}).get("symbols_ok") or 0),
        }
        if manifest
        else None,
        "training_frame": frame_info,
    }
