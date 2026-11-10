"""Faire Prüfreihenfolge (desk.check_queue) und Einbindung in die
Prüfkette. Kein Netzwerk: RugCheck und Holder sind ersetzt."""

import json
from unittest.mock import patch

import pytest

from desk.check_queue import (
    MAX_SCAN_EVENTS,
    QUEUE_AFTER_INCOMPLETE,
    QUEUE_AFTER_READY,
    QUEUE_AFTER_REJECTION,
    QUEUE_DEFAULT,
    QUEUE_NEW,
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
    # Prüfversuche: abgeschlossen oder Fehler; übersprungene nicht
    assert {m: r for m, (_, _, r) in history["last_checked"].items()} == {
        "REJ": "rejected",
        "INC": "incomplete",
        "ERR": "incomplete",
        "NOREASON": "incomplete",
    }
    # Erster Auftritt zählt auch ungeprüft
    assert set(history["first_seen"]) == {"REJ", "INC", "SKIP", "PRE", "ERR", "NOREASON"}


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
    history = {
        "source": source,
        "scans_read": 1,
        "last_rejected": {"A": (0, "t")},
        "last_checked": {"A": (0, "t", "rejected")},
        "first_seen": {"A": 0},
    }

    assert [m for m, _, _ in order_check_mints(["A", "B"], history)] == ["A", "B"]


def history_dict(scans, checked=None, first_seen=None):
    return {
        "source": SOURCE_HISTORY,
        "scans_read": scans,
        "last_checked": checked or {},
        "first_seen": first_seen or {},
        "last_rejected": {
            m: (n, t) for m, (n, t, r) in (checked or {}).items() if r == "rejected"
        },
    }


def test_longest_waiting_first_and_ties_are_stable():
    history = history_dict(
        3,
        checked={
            "A": (2, "t2", "rejected"),    # Wartezeit 1
            "H": (0, "t0", "rejected"),    # Wartezeit 3, trotzdem hinten
            "B": (0, "t0", "ready"),       # Wartezeit 3
            "C": (1, "t1", "incomplete"),  # Wartezeit 2
            "D": (2, "t2", "incomplete"),  # Wartezeit 1
            "E": (2, "t2", "ready"),       # Wartezeit 1
        },
        first_seen={"F": 1},               # nie geprüft, Wartezeit 3
    )

    order = order_check_mints(["A", "B", "C", "D", "E", "F", "G", "H"], history)

    assert order == [
        ("F", QUEUE_NEW, None),            # Wartezeit 3, nie geprüft vor B
        ("B", QUEUE_AFTER_READY, None),
        ("C", QUEUE_AFTER_INCOMPLETE, None),
        ("G", QUEUE_NEW, None),            # ganz neu, Wartezeit 1
        ("D", QUEUE_AFTER_INCOMPLETE, None),
        ("E", QUEUE_AFTER_READY, None),
        # Abgelehnte hinten (PR #13), älteste Prüfung zuerst
        ("H", QUEUE_AFTER_REJECTION, "t0"),
        ("A", QUEUE_AFTER_REJECTION, "t2"),
    ]


def test_same_input_gives_same_order():
    history = history_dict(2, checked={"A": (1, "t", "ready")}, first_seen={"B": 0})

    first = order_check_mints(["A", "B", "C"], history)

    assert first == order_check_mints(["A", "B", "C"], history)


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
    assert item["queue_reason"] == QUEUE_AFTER_REJECTION
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
    assert result["pools"][0]["queue_reason"] == QUEUE_AFTER_INCOMPLETE


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


# --------------------------------------------------
# Fairness über mehrere Läufe
# --------------------------------------------------

def simulate(rounds, pools_per_round, security, logged_ready=frozenset()):
    """Mehrere Läufe nacheinander; Protokoll wie desk.__main__.
    pools_per_round: Funktion Rundennummer -> Liste der Token.
    logged_ready: Token, die im Protokoll als bereit erscheinen, wenn sie
    geprüft und nicht abgelehnt wurden (die Reihenfolge liest nur das
    Protokoll; echte Bereitschaft bräuchte Mint-Info und Holder)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        events = []
        checked_rounds = []

        for number in range(rounds):
            history = load_check_history(write_log(path, events)) if events else None
            result, checked = run_scan(
                [make_pool(m) for m in pools_per_round(number)], history, security
            )
            checked_rounds.append(checked)
            event = as_log_event(result, f"2026-11-10T{number:02d}:00:00+00:00")
            for pool in event["details"]["checked_pools"]:
                if (
                    pool["token_address"] in logged_ready
                    and pool["security_check_status"] == "completed"
                    and pool["status"] != "rejected"
                ):
                    pool["status"] = "ready_for_full_risk_screen"
            events.append(event)

    return checked_rounds


def not_rejected_for(mints):
    """RugCheck: Token aus mints nicht abgelehnt (LP gesperrt), alle
    anderen abgelehnt (LP nicht gesperrt)."""
    def security(mint):
        return make_security(mint=mint, lp_locked=mint in mints)
    return security


def test_new_candidates_are_preferred_over_last_checked(tmp_path):
    # Lauf 1: T00-T09 geprüft. Lauf 2: zwei ganz neue Token kommen dazu.
    history = history_from(
        tmp_path,
        [scan_event([logged_pool(m, status="incomplete") for m in MINTS[:10]])],
    )

    _, checked = run_scan([make_pool(m) for m in ["N1", "N2"] + MINTS[:10]], history)

    assert {"N1", "N2"} <= set(checked)
    assert len(checked) == LIMIT


def test_incomplete_candidates_are_checked_again():
    # 12 Token, alle unvollständig (Holder fehlen): Wer im ersten Lauf
    # übersprungen wurde, kommt im zweiten zuerst; danach kommen die
    # zuerst geprüften wieder dran.
    mints = MINTS[:12]

    rounds = simulate(3, lambda _: mints, not_rejected_for(set(mints)))

    assert rounds[0] == mints[:10]
    assert set(rounds[1]) == {"T10", "T11"} | set(mints[:8])
    assert set(rounds[2]) >= {"T08", "T09"}


def test_ready_candidates_do_not_block_every_run():
    # 10 bereite Token füllen das Kontingent. Neue, nie geprüfte Token
    # kommen trotzdem sofort dran; die bereiten rotieren.
    ready_mints = set(MINTS[:10])
    rounds = simulate(
        3,
        lambda number: MINTS[:10] + ([] if number == 0 else MINTS[10:15]),
        not_rejected_for(ready_mints),
        logged_ready=ready_mints,
    )

    assert rounds[0] == MINTS[:10]
    assert set(MINTS[10:15]) <= set(rounds[1])
    # Bereite Token sind nicht in jedem Lauf alle dabei ...
    assert not ready_mints <= set(rounds[1])
    # ... aber jeder kommt wieder dran.
    assert ready_mints <= set(rounds[1]) | set(rounds[2])


@pytest.mark.parametrize("outcome", ["rejected", "ready", "incomplete"])
def test_no_candidate_is_skipped_permanently(outcome):
    """25 alte Token plus in jedem Lauf 4 ganz neue (bleiben danach in
    der Liste, wie Pools im Alterfenster). Mehr Kandidaten als Plätze:
    trotzdem wird jeder Token nach endlich vielen Läufen geprüft, und
    kein Token wartet länger als nötig."""
    old = [f"T{i:02d}" for i in range(25)]

    logged_ready = frozenset()

    if outcome == "rejected":
        security = not_rejected_for(set())
    elif outcome == "ready":
        security = not_rejected_for(set(old))
        logged_ready = frozenset(old)
    else:
        security = not_rejected_for(set(old) | {f"N{r}_{i}" for r in range(10) for i in range(4)})

    def pools(number):
        new = [f"N{r}_{i}" for r in range(number, -1, -1) for i in range(4)]
        return new + old  # neueste zuerst, wie DexPaprika

    runs = 10
    rounds = simulate(runs, pools, security, logged_ready)

    # Jeder Kandidat, der mindestens 5 Läufe in der Liste stand, wurde
    # geprüft (Kapazität 10 je Lauf reicht im Mittel für ~4 neue + 6 alte).
    checked = set(sum(rounds, []))
    for mint in old:
        assert mint in checked
    for number in range(runs - 5):
        for index in range(4):
            assert f"N{number}_{index}" in checked

    # Nicht abgelehnte alte Token: keine Lücke über 5 Läufe. Abgelehnte
    # stehen bewusst hinten (PR #13) und dürfen länger warten.
    for mint in old if outcome != "rejected" else []:
        hits = [n for n, r in enumerate(rounds) if mint in r]
        gaps = [b - a for a, b in zip([-1] + hits, hits + [runs])]
        assert max(gaps) <= 6, (mint, hits)

    assert all(len(r) <= LIMIT for r in rounds)


def test_limit_is_kept_over_many_runs():
    rounds = simulate(
        5,
        lambda number: MINTS + [f"X{number}_{i}" for i in range(20)],
        not_rejected_for(set()),
    )

    assert all(len(r) == LIMIT for r in rounds)
    assert all(len(set(r)) == len(r) for r in rounds)
