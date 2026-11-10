"""Schattendepot: Beobachtung aller geprüften Token (nur Lernen)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from desk import shadow
from desk.shadow import ShadowStateError, run_shadow

from test_paper import MINT, MINT2, POOL, POOL2, ready_pool, write_scan


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(shadow, "BATCH_PAUSE_SECONDS", 0)


def fetcher(prices):
    calls = []

    def fetch(pairs):
        calls.append(list(pairs))
        return {pool: {"price_usd": prices.get(pool)} for _, pool in pairs}

    fetch.calls = calls
    return fetch


def pools():
    rejected = ready_pool(MINT2, POOL2, symbol="B", status="rejected")
    return [ready_pool(), rejected]


def scan_at(tmp_path, at, pool_list=None):
    write_scan(tmp_path, pool_list or pools(), at=at - timedelta(minutes=5), previous=None)


def state(tmp_path):
    return json.loads((tmp_path / "shadow_state.json").read_text(encoding="utf-8"))


def summary(tmp_path):
    return json.loads((tmp_path / "shadow_summary.json").read_text(encoding="utf-8"))


def test_every_checked_pool_is_observed(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0, POOL2: 2.0}))

    items = state(tmp_path)["open"]
    assert [(i["token_address"], i["status"], i["start_price_usd"]) for i in items] == [
        (MINT, "ready_for_full_risk_screen", 1.0),
        (MINT2, "rejected", 2.0),
    ]
    assert items[1]["group"] == "rejected:lp_not_locked"


def test_returns_after_horizons_and_completion(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0, POOL2: 2.0}))

    for hours, p1, p2 in [(1, 1.1, 1.0), (6, 1.5, 0.5), (24, 0.9, 0.2)]:
        later = NOW + timedelta(hours=hours, minutes=3)
        scan_at(tmp_path, later)
        run_shadow(tmp_path, "success", later, fetcher({POOL: p1, POOL2: p2}))

    done = {i["token_address"]: i for i in state(tmp_path)["completed"]}
    assert done[MINT]["returns"]["1h"]["return_pct"] == pytest.approx(10)
    assert done[MINT]["returns"]["6h"]["return_pct"] == pytest.approx(50)
    assert done[MINT]["returns"]["24h"]["return_pct"] == pytest.approx(-10)
    assert done[MINT2]["returns"]["24h"]["return_pct"] == pytest.approx(-90)

    rows = {r["group"]: r for r in summary(tmp_path)["by_status"]}
    assert rows["ready_for_full_risk_screen"]["24h"]["mean_pct"] == pytest.approx(-10)
    assert rows["rejected"]["6h"]["positive_share_pct"] == 0


def test_one_new_observation_per_token_per_day(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0, POOL2: 2.0}))
    later = NOW + timedelta(hours=2)
    scan_at(tmp_path, later)
    result = run_shadow(tmp_path, "success", later, fetcher({POOL: 1.0, POOL2: 2.0}))

    assert result["state"]["last_run"]["added"] == 0
    assert len(state(tmp_path)["open"]) == 2


def test_missing_price_sets_no_value(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0}))
    assert [i["token_address"] for i in state(tmp_path)["open"]] == [MINT]

    later = NOW + timedelta(hours=1, minutes=5)
    scan_at(tmp_path, later)
    run_shadow(tmp_path, "success", later, fetcher({}))
    assert state(tmp_path)["open"][0]["returns"] == {}


def test_observation_ends_after_max_hours_without_price(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0}))
    later = NOW + timedelta(hours=31)
    scan_at(tmp_path, later, [])
    run_shadow(tmp_path, "success", later, fetcher({}))

    assert state(tmp_path)["open"] == []
    assert state(tmp_path)["completed"][0]["returns"] == {}


@pytest.mark.parametrize("outcome", ["failure", None])
def test_failed_scan_adds_nothing_but_updates(tmp_path, outcome):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0, POOL2: 2.0}))
    later = NOW + timedelta(hours=1, minutes=5)
    result = run_shadow(tmp_path, outcome, later, fetcher({POOL: 2.0, POOL2: 2.0}))

    assert result["state"]["last_run"]["added"] == 0
    assert state(tmp_path)["open"][0]["returns"]["1h"]["return_pct"] == pytest.approx(100)


def test_corrupt_state_stops_without_overwriting(tmp_path):
    (tmp_path / "shadow_state.json").write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ShadowStateError):
        run_shadow(tmp_path, "success", NOW, fetcher({}))
    assert (tmp_path / "shadow_state.json").read_text(encoding="utf-8") == "{kaputt"


def test_summary_is_marked_as_learning_only(tmp_path):
    scan_at(tmp_path, NOW)
    run_shadow(tmp_path, "success", NOW, fetcher({POOL: 1.0, POOL2: 2.0}))
    data = summary(tmp_path)

    assert data["purpose"] == "learning_only_no_trading"
    assert data["observations_total"] == 2


def test_prices_are_fetched_in_batches(tmp_path):
    many = [ready_pool(f"M{i:03d}", f"P{i:03d}", symbol=f"T{i}") for i in range(65)]
    scan_at(tmp_path, NOW, many)
    fetch = fetcher({f"P{i:03d}": 1.0 for i in range(65)})
    run_shadow(tmp_path, "success", NOW, fetch)

    assert [len(c) for c in fetch.calls] == [30, 30, 5]
    assert len(state(tmp_path)["open"]) == 65
