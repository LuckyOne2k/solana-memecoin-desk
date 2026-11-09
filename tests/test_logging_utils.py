import json

from desk import logging_utils


def test_log_file_can_be_redirected(tmp_path, monkeypatch):
    target = tmp_path / "desk-data" / "events.jsonl"
    monkeypatch.setenv("DESK_LOG_FILE", str(target))

    logging_utils.log_event("test_event", {"value": 1})

    lines = target.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])

    assert event["event_type"] == "test_event"
    assert event["details"] == {"value": 1}


def test_default_log_file_is_used_without_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_LOG_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    logging_utils.log_event("test_event", {})

    assert (tmp_path / "data" / "events.jsonl").is_file()


def test_events_are_appended(tmp_path, monkeypatch):
    target = tmp_path / "events.jsonl"
    monkeypatch.setenv("DESK_LOG_FILE", str(target))

    logging_utils.log_event("first", {})
    logging_utils.log_event("second", {})

    lines = target.read_text(encoding="utf-8").splitlines()

    assert [json.loads(line)["event_type"] for line in lines] == [
        "first",
        "second",
    ]
