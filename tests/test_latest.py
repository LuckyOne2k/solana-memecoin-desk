"""Tests für die Kurzübersicht latest.json (desk/latest.py)."""

import copy
import json

import pytest

from desk.latest import build_latest, main, write_latest


def pool(symbol, status, reasons=None, missing=None):
    return {
        "token_symbol": symbol,
        "token_name": symbol.lower(),
        "token_address": f"mint_{symbol}",
        "pool_address": f"pool_{symbol}",
        "dex_name": "PumpSwap",
        "status": status,
        "security_check_status": "completed",
        "rejection_reasons": reasons or [],
        "missing_checks": missing or [],
        "liquidity_check": {"status": "confirmed"},
    }


BASE_DETAILS = {
    "total_pools": 6,
    "rejected_pools": 2,
    "incomplete_pools": 2,
    "ready_for_full_risk_screen": 2,
    "checked_pools": [
        pool("TTP", "ready_for_full_risk_screen"),
        pool("BRUH", "ready_for_full_risk_screen"),
        pool("BAD", "rejected", reasons=["lp_burn_or_lock_not_verified"]),
        pool("OPEN", "incomplete", missing=["market_cap_usd"]),
    ],
}


def scan_event(details=None):
    return {
        "timestamp": "2026-11-09T20:30:43+00:00",
        "event_type": "security_scan_completed",
        "details": copy.deepcopy(BASE_DETAILS if details is None else details),
    }


def write_log(tmp_path, *events, raw_tail=None):
    path = tmp_path / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    if raw_tail is not None:
        lines.append(raw_tail)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def assert_nothing_ready(data, scan_status):
    assert data["scan_status"] == scan_status
    assert data["ready_tokens"] == []
    assert data["pools"] == []
    assert data["counts"] is None
    assert data["live_trading"] is False


def test_successful_scan_is_copied_one_to_one(tmp_path):
    path = write_log(tmp_path, {"event_type": "system_start"}, scan_event())

    data = build_latest(path, "success", {"run_id": "1"})

    assert data["scan_status"] == "ok"
    assert data["scan_at"] == "2026-11-09T20:30:43+00:00"
    assert data["counts"] == {
        "total_pools": 6,
        "rejected_pools": 2,
        "incomplete_pools": 2,
        "ready_for_full_risk_screen": 2,
    }
    assert data["pools_not_listed"] == 2
    assert [t["token_symbol"] for t in data["ready_tokens"]] == ["TTP", "BRUH"]
    assert [p["status"] for p in data["pools"]] == [
        "ready_for_full_risk_screen",
        "ready_for_full_risk_screen",
        "rejected",
        "incomplete",
    ]
    assert data["pools"][2]["rejection_reasons"] == ["lp_burn_or_lock_not_verified"]
    assert data["pools"][3]["missing_checks"] == ["market_cap_usd"]
    assert "liquidity_check" not in data["pools"][0]
    assert data["run"] == {"run_id": "1"}


def test_only_ready_status_appears_as_ready(tmp_path):
    path = write_log(tmp_path, scan_event())

    data = build_latest(path, "success")

    ready = {t["token_symbol"] for t in data["ready_tokens"]}
    assert ready == {"TTP", "BRUH"}
    assert "OPEN" not in ready and "BAD" not in ready


@pytest.mark.parametrize("outcome", ["failure", "skipped", "cancelled", "", None])
def test_failed_scan_never_shows_old_results(tmp_path, outcome):
    # Altes, erfolgreiches Ergebnis im Protokoll darf nicht übernommen werden.
    path = write_log(tmp_path, scan_event())

    data = build_latest(path, outcome)

    assert_nothing_ready(data, "failed")
    assert data["scan_at"] is None


def test_error_event_after_old_scan_is_not_ok(tmp_path):
    path = write_log(
        tmp_path,
        scan_event(),
        {"event_type": "market_data_error", "details": {"error": "x"}},
    )

    data = build_latest(path, "success")

    assert_nothing_ready(data, "log_unreadable")
    assert data["status_reason"] == "last_event_not_scan_result"


def test_corrupt_last_line(tmp_path):
    path = write_log(tmp_path, scan_event(), raw_tail='{"event_type": "secu')

    data = build_latest(path, "success")

    assert_nothing_ready(data, "log_unreadable")
    assert data["status_reason"] == "last_line_not_json"


@pytest.mark.parametrize("create", [False, True])
def test_missing_or_empty_log(tmp_path, create):
    path = tmp_path / "events.jsonl"
    if create:
        path.write_text("\n\n", encoding="utf-8")

    data = build_latest(path, "success")

    assert_nothing_ready(data, "log_unreadable")
    assert data["status_reason"] == "log_missing_or_empty"


def _mutated(change):
    details = copy.deepcopy(BASE_DETAILS)
    change(details)
    return details


@pytest.mark.parametrize(
    "change, reason",
    [
        (lambda d: d.update(ready_for_full_risk_screen=3, total_pools=7),
         "ready_count_mismatch"),
        (lambda d: d.update(rejected_pools=3), "counts_do_not_add_up"),
        (lambda d: d.update(total_pools="6"), "count_invalid:total_pools"),
        (lambda d: d.update(incomplete_pools=None),
         "count_invalid:incomplete_pools"),
        (lambda d: d.update(rejected_pools=True), "count_invalid:rejected_pools"),
        (lambda d: d["checked_pools"][2].update(status="passed"),
         "unknown_status"),
        (lambda d: d["checked_pools"][0].update(missing_checks=["dev_share_pct"]),
         "ready_pool_with_open_checks"),
        (lambda d: d["checked_pools"][1].update(rejection_reasons=["x"]),
         "ready_pool_with_open_checks"),
        (lambda d: d["checked_pools"][0].update(missing_checks="dev_share_pct"),
         "missing_checks_invalid"),
        (lambda d: d.pop("checked_pools"), "checked_pools_missing"),
        (lambda d: d.update(total_pools=3, rejected_pools=0, incomplete_pools=1),
         "more_pools_than_total"),
        (lambda d: d.update(rejected_pools=0, incomplete_pools=4),
         "rejected_count_mismatch"),
    ],
)
def test_inconsistent_data_is_never_ready(tmp_path, change, reason):
    path = write_log(tmp_path, scan_event(_mutated(change)))

    data = build_latest(path, "success")

    assert_nothing_ready(data, "inconsistent")
    assert data["status_reason"] == reason


def test_events_log_is_not_modified(tmp_path):
    path = write_log(tmp_path, scan_event())
    before = path.read_bytes()

    main([str(path), str(tmp_path / "latest.json"), "--scan-outcome", "success"])
    main([str(path), str(tmp_path / "latest.json"), "--scan-outcome", "failure"])

    assert path.read_bytes() == before


def test_cli_writes_valid_json_and_replaces_old_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_SHA", "abc")
    path = write_log(tmp_path, scan_event())
    output = tmp_path / "out" / "latest.json"

    assert main([str(path), str(output), "--scan-outcome", "success"]) == 0
    first = json.loads(output.read_text(encoding="utf-8"))
    assert first["scan_status"] == "ok"
    assert first["run"]["run_id"] == "42"
    assert first["run"]["commit"] == "abc"

    assert main([str(path), str(output), "--scan-outcome", "failure"]) == 0
    second = json.loads(output.read_text(encoding="utf-8"))
    assert_nothing_ready(second, "failed")
    assert not (output.parent / "latest.json.tmp").exists()


def test_write_latest_keeps_umlauts(tmp_path):
    output = tmp_path / "latest.json"

    write_latest({"text": "unvollständig"}, output)

    assert "unvollständig" in output.read_text(encoding="utf-8")
