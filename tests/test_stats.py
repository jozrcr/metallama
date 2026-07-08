"""Tests for metallama.app.stats — SQLite persistence and overview."""
import time
from pathlib import Path

from metallama.app import stats


def _use_tmp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("METALLAMA_STATS_DB", str(tmp_path / "stats.db"))


def test_record_and_overview_roundtrip(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    stats.record_request(
        "m1",
        {"prompt_tokens": 100, "completion_tokens": 50},
        {"prompt_per_second": 800.0, "predicted_per_second": 40.0},
        1234,
        stream=False,
    )
    stats.record_request("m1", {"prompt_tokens": 10, "completion_tokens": 5}, None, 200, stream=True)
    stats.record_request("m2", None, None, 50, stream=False)
    stats.record_sample("m1", 4096.0)
    stats.record_event("m1", "recycle", "RSS over limit")

    ov = stats.overview("m1", since_ms=0)
    assert ov["requests"] == 2
    assert ov["prompt_tokens"] == 110
    assert ov["completion_tokens"] == 55
    assert ov["avg_gen_tps"] == 40.0
    assert len(ov["series"]["requests"]) == 2
    assert ov["series"]["rss"][0][1] == 4096.0
    assert ov["series"]["events"][0][1] == "recycle"

    ov_all = stats.overview(None, since_ms=0)
    assert ov_all["requests"] == 3
    assert ov_all["series"]["rss"] == []  # per-server only


def test_since_filter(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    stats.record_request("m1", {"completion_tokens": 1}, None, 10, stream=False)
    future = int(time.time() * 1000) + 60_000
    assert stats.overview("m1", since_ms=future)["requests"] == 0
    assert stats.overview("m1", since_ms=0)["requests"] == 1


def test_record_never_raises(tmp_path, monkeypatch):
    # Unwritable DB path → all record functions swallow the failure
    monkeypatch.setenv("METALLAMA_STATS_DB", "/proc/definitely/not/writable/stats.db")
    stats.record_request("m1", None, None, 1, stream=False)
    stats.record_sample("m1", 1.0)
    stats.record_event("m1", "start", "")
