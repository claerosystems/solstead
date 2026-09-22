import json

import pytest

from solstead.health import HealthLog


def test_record_recent_counts_and_file(tmp_path):
    path = tmp_path / "health.jsonl"
    log = HealthLog(path, capacity=3)
    log.record("info", "controller", "Started")
    log.record("warning", "camera", "Stream ended", restarts=1)
    log.record("error", "storage", "Lost")
    assert [e["kind"] for e in log.recent()] == ["storage", "camera", "controller"]
    assert [e["level"] for e in log.recent(minimum_level="warning")] == ["error", "warning"]
    assert log.recent(1)[0]["message"] == "Lost"
    assert log.counts(0) == {"info": 1, "warning": 1, "error": 1}
    lines = path.read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[1])["restarts"] == 1
    with pytest.raises(ValueError):
        log.record("debug", "x", "y")


def test_file_is_capped_and_reloaded(tmp_path):
    path = tmp_path / "health.jsonl"
    log = HealthLog(path, capacity=2)
    for i in range(7):
        log.record("info", "k", f"m{i}")
    assert len(path.read_text().splitlines()) <= 4
    reloaded = HealthLog(path, capacity=2)
    assert [e["message"] for e in reloaded.recent()] == ["m6", "m5"]


def test_guard_keeps_memory_only(tmp_path):
    path = tmp_path / "missing-ssd" / "health.jsonl"
    log = HealthLog(path, guard=lambda: False)
    log.record("error", "storage", "Recording storage lost")
    assert not path.parent.exists() and log.recent()[0]["kind"] == "storage"
