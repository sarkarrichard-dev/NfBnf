import sqlite3
from pathlib import Path

from index_ai import cloud_backup


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_S3_BACKUP", raising=False)
    assert cloud_backup.backup_enabled() is False
    out = cloud_backup.run_backup()
    assert out["skipped"] == "ENABLE_S3_BACKUP is off"
    assert out["uploaded"] == 0


def test_enabled_but_no_bucket_is_a_clean_skip(monkeypatch):
    monkeypatch.setenv("ENABLE_S3_BACKUP", "true")
    monkeypatch.delenv("S3_BACKUP_BUCKET", raising=False)
    out = cloud_backup.run_backup()
    assert out["skipped"] == "S3_BACKUP_BUCKET is not set"


def test_staging_snapshots_sqlite_and_skips_bulk(monkeypatch, tmp_path):
    mem = tmp_path / "memory"
    (mem / "models").mkdir(parents=True)
    (mem / "candles").mkdir()
    con = sqlite3.connect(mem / "trade_memory.sqlite")
    con.execute("create table t (x int)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    (mem / "models" / "brain.joblib").write_bytes(b"model")
    (mem / "vix.json").write_text("{}")
    (mem / "server.log").write_text("noise")
    (mem / "candles" / "NIFTY.csv").write_text("big")

    monkeypatch.setattr(cloud_backup, "MEMORY_DIR", mem)
    staging = tmp_path / "staging"
    staging.mkdir()
    pairs = cloud_backup._files_to_upload(staging)
    keys = {rel for _, rel in pairs}

    assert "trade_memory.sqlite" in keys  # snapshotted
    assert "models/brain.joblib" in keys
    assert "vix.json" in keys
    assert "server.log" not in keys  # excluded
    assert not any("candles" in k for k in keys)  # excluded
    assert all(Path(p).is_file() for p, _ in pairs)

    # the snapshot is a real, readable copy
    snap = next(p for p, rel in pairs if rel == "trade_memory.sqlite")
    scon = sqlite3.connect(snap)
    assert scon.execute("select x from t").fetchone() == (1,)
    scon.close()
