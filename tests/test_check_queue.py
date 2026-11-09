"""Faire Prüfreihenfolge (desk.check_queue) und Einbindung in die
Prüfkette. Kein Netzwerk: RugCheck und Holder sind ersetzt."""

import json
from unittest.mock import patch

import pytest

from desk.check_queue import (
    MAX_SCAN_EVENTS,
    QUEUE_BACK,
    QUEUE_DEFAULT,
    QUEUE_FIRST,
    SOURCE_HISTORY,
    SOURCE_NO_LOG,
    SOURCE_NO_SCANS,
    SOURCE_UNREADABLE,
    load_check_history,
    order_check_mints,
)
from desk.config import load_config
from desk.security_scan import scan_pool_security
from factories import make_security, make_snapshot


CONFIG = load_config()
LIMIT = CONFIG["scanner"]["max_security_checks_per_run"]


# --------------------------------------------------
# Hilfen
# --------------------------------------------------

def logged_pool(mint, status="rejected", check="completed", reasons=None):
    return {
        "token_address": mint,
        "status": status,
        "security_check_status": check,
        "rejection_reasons": (
            reasons
            if reasons is not None
            else (["lp_burn_or_lock_not_verified"] if status == "rejected" else [])
        ),
    }


def scan_event(pools, timestamp="2026-11-09T12:00:00+00:00"):
    return {
        "timestamp": timestamp,
        "event_type": "security_scan_completed",
        "details": {"checked_pools": pools},
    }


def write_log(path, events):
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def history_from(tmp_path, events):
    return load_check_history(write_log(tmp_path / "events.jsonl", events))


def make_pool(mint, status="incomplete", reasons=None):
    return {
        "token_address": mint,
        "token_symbol": mint,
        "status": status,
        "rejection_reasons": list(reasons or []),
        "missing_checks": ["mint_authority_revoked", "lp_burned_or_locked"],
    }


def run_scan(pools, history, security=None):
    """security: Funktion mint -> RugCheck-Ergebnis (Standard: LP nicht
    gesperrt -> Ablehnung)."""
    security = security or (lambda mint: make_security(mint=mint, lp_locked=False))

    with patch(
        "desk.security_scan.prepare_pools", return_value=pools
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=lambda mint, **kwargs: security(mint),
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=lambda mint, **kwargs: make_snapshot(mint=mint),
    ):
        result = scan_pool_security([{}], CONFIG, check_history=history)

    return result, [call.args[0] for call in mocked_security.call_args_list]


def as_log_event(result, timestamp):
    """Wie desk.__main__ den Scan protokolliert (relevante Felder)."""
    return scan_event(
        [
            {
                "token_address": pool.get("token_address"),
                "status": pool["status"],
                "security_check_status": pool["security_check_status"],
                "rejection_reasons": pool["rejection_reasons"],
            }
            for pool in result["pools"]
            if pool["security_check_status"] != "not_checked"
        ],
        timestamp=timestamp,
    )


# --------------------------------------------------
# Protokoll lesen
# --------------------------------------------------

def test_missing_log_falls_back(tmp_path):
    history = load_check_history(tmp_path / "missing.jsonl")

    assert history["source"] == SOURCE_NO_LOG
    assert history["last_rejected"] == {}


def test_empty_log_has_no_scans(tmp_path):
    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")

    assert load_check_history(tmp_path / "events.jsonl")["source"] == SOURCE_NO_SCANS


def test_log_without_scan_events_has_no_scans(tmp_path):
    history = history_from(
        tmp_path, [{"timestamp": "t", "event_type": "system_start", "details": {}}]
    )

    assert history["source"] == SOURCE_NO_SCANS


@pytest.mark.parametrize(
    "line",
    [
        "{kaputt",
        '"text"',
        "[1, 2]",
        '{"event_type": "security_scan_completed"}',
        '{"timestamp": "t", "event_type": "security_scan_completed", "details": []}',
        '{"timestamp": "t", "event_type": "security_scan_completed", "details": {}}',
        '{"timestamp": "t", "event_type": "security_scan_completed", '
        '"details": {"checked_pools": "x"}}',
        '{"timestamp": "t", "event_type": "security_scan_completed", '
        '"details": {"checked_pools": [1]}}',
        '{"timestamp": "t", "event_type": "security_scan_completed", '
        '"details": {"checked_pools": [{"token_address": "A", "status": "rejected"}]}}',
        '{"timestamp": "t", "event_type": "security_scan_completed", '
        '"details": {"checked_pools": [{"token_address": 5, "status": "rejected", '
        '"security_check_status": "completed"}]}}',
        '{"timestamp": 1, "event_type": "security_scan_completed", '
        '"details": {"checked_pools": []}}',
    ],
)
def test_damaged_log_falls_back(tmp_path, line):
    path = tmp_path / "events.jsonl"
    good = json.dumps(scan_event([logged_pool("A")]))
    path.write_text(good + "\n" + line + "\n", encoding="utf-8")

    history = load_check_history(path)

    assert history["source"] == SOURCE_UNREADABLE
    assert history["last_rejected"] == {}


def test_unreadable_bytes_fall_back(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\xff\xfe\x00kaputt")

    assert load_check_history(path)["source"] == SOURCE_UNREADABLE


def test_only_completed_and_rejected_checks_count(tmp_path):
    history = history_from(
        tmp_path,
        [
            scan_event(
                [
                    logged_pool("REJ"),
                    logged_pool("INC", status="incomplete"),
                    logged_pool("SKIP", status="incomplete", check="skipped_run_limit"),
                    logged_pool("PRE", check="skipped_pool_rejected"),
                    logged_pool("ERR", status="incomplete", check="error"),
                    logged_pool(None, status="incomplete", check="skipped_missing_mint"),
                    logged_pool("NOREASON", reasons=[]),
                ]
            )
        ],
    )

    assert history["source"] == SOURCE_HISTORY
    assert set(history["last_rejected"]) == {"REJ"}


def test_later_non_rejected_check_clears_rejection(tmp_path):
    history = history_from(
        tmp_path,
        [
            scan_event([logged_pool("A")], "2026-11-09T10:00:00+00:00"),
            scan_event([logged_pool("A", status="incomplete")], "2026-11-09T11:00:00+00:00"),
        ],
    )

    assert history["last_rejected"] == {}


def test_skipped_in_later_scan_keeps_earlier_rejection(tmp_path):
    history = history_from(
        tmp_path,
        [
            scan_event([logged_pool("A")], "2026-11-09T10:00:00+00:00"),
            scan_event(
                [logged_pool("A", status="incomplete", check="skipped_run_limit")],
                "2026-11-09T11:00:00+00:00",
            ),
        ],
    )

    assert history["last_rejected"] == {"A": (0, "2026-11-09T10:00:00+00:00")}


def test_token_with_one_non_rejected_pool_is_not_deprioritized(tmp_path):
    history = history_from(
        tmp_path,
        [scan_event([logged_pool("A"), logged_pool("A", status="incomplete")])],
    )

    assert history["last_rejected"] == {}


def test_only_recent_scans_are_read(tmp_path):
    events = [scan_event([logged_pool("OLD")], "2026-11-01T00:00:00+00:00")]
    events += [
        scan_event([], f"2026-11-09T{index % 24:02d}:00:00+00:00")
        for index in range(MAX_SCAN_EVENTS)
    ]

    history = history_from(tmp_path, events)

    assert history["scans_read"] == MAX_SCAN_EVENTS
    assert "OLD" not in history["last_rejected"]


# --------------------------------------------------
# Reihenfolge
# --------------------------------------------------

def test_without_history_order_is_unchanged():
    assert order_check_mints(["A", "B", "C"], None) == [
        ("A", QUEUE_DEFAULT, None),
        ("B", QUEUE_DEFAULT, None),
        ("C", QUEUE_DEFAULT, None),
    ]


@pytest.mark.parametrize("source", [SOURCE_NO_LOG, SOURCE_NO_SCANS, SOURCE_UNREADABLE])
def test_fallback_sources_keep_order(source):
    history = {"source": source, "last_rejected": {"A": (0, "t")}}

    assert [m for m, _, _ in order_check_mints(["A", "B"], history)] == ["A", "B"]


def test_rejected_move_back_and_groups_keep_order():
    history = {
        "source": SOURCE_HISTORY,
        "last_rejected": {"A": (1, "t1"), "C": (0, "t0"), "E": (1, "t1")},
    }

    order = order_check_mints(["A", "B", "C", "D", "E"], history)

    assert order == [
        ("B", QUEUE_FIRST, None),
        ("D", QUEUE_FIRST, None),
        ("C", QUEUE_BACK, "t0"),  # am längsten zurückliegende Ablehnung zuerst
        ("A", QUEUE_BACK, "t1"),
        ("E", QUEUE_BACK, "t1"),
    ]


# --------------------------------------------------
# Prüfkette
# --------------------------------------------------

MINTS = [f"T{index:02d}" for index in range(30)]


def test_no_history_checks_same_tokens_as_before():
    result, checked = run_scan([make_pool(m) for m in MINTS], None)

    assert checked == MINTS[:LIMIT]
    assert result["check_order_source"] == "not_provided"
    assert result["checks_attempted"] == LIMIT


def test_starvation_is_resolved_by_rotation(tmp_path):
    """Alle 30 Token werden abgelehnt (LP). Ohne Rotation würden immer
    T00-T09 geprüft; mit Rotation kommen nacheinander alle dran."""
    path = tmp_path / "events.jsonl"
    events = []
    rounds = []

    for number in range(4):
        history = load_check_history(write_log(path, events)) if events else None
        result, checked = run_scan([make_pool(m) for m in MINTS], history)
        rounds.append(checked)
        events.append(as_log_event(result, f"2026-11-09T1{number}:00:00+00:00"))

    assert rounds[0] == MINTS[0:10]
    assert rounds[1] == MINTS[10:20]
    assert rounds[2] == MINTS[20:30]
    # Abgelehnte Token sind nicht dauerhaft ausgeschlossen.
    assert rounds[3] == MINTS[0:10]
    assert sorted(sum(rounds[:3], [])) == MINTS


def test_new_tokens_are_checked_first(tmp_path):
    history = history_from(tmp_path, [scan_event([logged_pool(m) for m in MINTS[:10]])])

    result, checked = run_scan([make_pool(m) for m in MINTS[:12]], history)

    # Ausgewählt: beide neuen Token + die ersten 8 abgelehnten. Abgefragt
    # wird in Listenreihenfolge; die Rotation bestimmt nur die Auswahl.
    assert set(checked) == {"T10", "T11"} | set(MINTS[:8])
    skipped = [
        pool["token_address"]
        for pool in result["pools"]
        if pool["security_check_status"] == "skipped_run_limit"
    ]
    assert skipped == ["T08", "T09"]
    positions = {
        pool["token_address"]: pool["check_queue_position"] for pool in result["pools"]
    }
    assert positions["T10"] == 1 and positions["T11"] == 2 and positions["T00"] == 3
    assert result["deprioritized_tokens"] == 10


def test_rejected_token_is_rechecked_when_budget_allows(tmp_path):
    history = history_from(tmp_path, [scan_event([logged_pool("T00")])])

    result, checked = run_scan([make_pool(m) for m in MINTS[:3]], history)
    item = {pool["token_address"]: pool for pool in result["pools"]}["T00"]

    assert sorted(checked) == ["T00", "T01", "T02"]
    assert item["queue_reason"] == QUEUE_BACK
    assert item["check_queue_position"] == 3
    assert item["security_check_status"] == "completed"


def test_old_results_are_never_reused(tmp_path):
    """Früher abgelehnt, jetzt sauber: Ergebnis kommt nur aus der frischen
    Prüfung (hier: keine Ablehnung)."""
    history = history_from(tmp_path, [scan_event([logged_pool("T00")])])

    result, checked = run_scan(
        [make_pool("T00")],
        history,
        security=lambda mint: make_security(mint=mint, lp_locked=True),
    )
    item = result["pools"][0]

    assert checked == ["T00"]
    assert item["rejection_reasons"] == []
    assert item["previous_rejection_at"] == "2026-11-09T12:00:00+00:00"


def test_previously_clean_token_is_still_freshly_rejected(tmp_path):
    history = history_from(tmp_path, [scan_event([logged_pool("T00", status="incomplete")])])

    result, _ = run_scan([make_pool("T00")], history)

    assert result["pools"][0]["status"] == "rejected"
    assert result["pools"][0]["queue_reason"] == QUEUE_FIRST


def test_unchecked_tokens_never_pass(tmp_path):
    # Rotation darf keinen Token ungeprüft als sicher ausgeben.
    history = history_from(tmp_path, [scan_event([logged_pool(m) for m in MINTS[:10]])])

    result, checked = run_scan(
        [make_pool(m) for m in MINTS],
        history,
        security=lambda mint: make_security(mint=mint, lp_locked=True),
    )

    for pool in result["pools"]:
        if pool["token_address"] not in checked:
            assert pool["security_check_status"] == "skipped_run_limit"
            assert pool["status"] == "incomplete"
            assert pool["missing_checks"]
        assert pool["status"] != "ready_for_full_risk_screen" or (
            pool["token_address"] in checked
        )

    assert result["checks_attempted"] == LIMIT


def test_limit_is_never_exceeded(tmp_path):
    history = history_from(tmp_path, [scan_event([logged_pool(m) for m in MINTS])])

    result, checked = run_scan([make_pool(m) for m in MINTS], history)

    assert len(checked) == LIMIT
    assert result["max_checks_per_run"] == LIMIT


def test_pre_rejected_and_mintless_pools_do_not_use_budget(tmp_path):
    pools = [make_pool("PRE", status="rejected", reasons=["no_24h_trading_activity"])]
    pools.append({**make_pool(None)})
    pools += [make_pool(m) for m in MINTS[:LIMIT]]

    result, checked = run_scan(pools, history_from(tmp_path, [scan_event([])]))

    assert checked == MINTS[:LIMIT]
    assert result["pools"][0]["security_check_status"] == "skipped_pool_rejected"
    assert "queue_reason" not in result["pools"][0]
    assert result["pools"][1]["security_check_status"] == "skipped_missing_mint"


def test_pool_output_order_is_unchanged(tmp_path):
    history = history_from(tmp_path, [scan_event([logged_pool(m) for m in MINTS[:10]])])

    result, _ = run_scan([make_pool(m) for m in MINTS], history)

    assert [pool["token_address"] for pool in result["pools"]] == MINTS


def test_damaged_log_uses_default_order(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("{kaputt\n", encoding="utf-8")

    result, checked = run_scan([make_pool(m) for m in MINTS], load_check_history(path))

    assert checked == MINTS[:LIMIT]
    assert result["check_order_source"] == SOURCE_UNREADABLE
    assert result["pools"][0]["queue_reason"] == QUEUE_DEFAULT
