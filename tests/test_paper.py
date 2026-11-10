"""Paper-Broker: Einstiegs- und Ausstiegsregeln, Depot, Kursquellen.

Keine Netzwerkabfragen: Kurse kommen aus einem Test-Lieferanten bzw.
gemockten requests-Aufrufen.
"""

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from desk import paper
from desk.paper import (
    PaperStateError,
    combine_quote,
    current_stop,
    stop_levels,
    run_paper,
    trade_cost_pct,
)


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
MINT = "E5c1JvHbwEf3ud2m4PXPMugcMDevLrnskq7sj1FTpump"
POOL = "9eTpuokzkShkz9GSFrQ1cPpow8beSx1ZXE7UtTQUrAFT"
MINT2 = "Mint2222222222222222222222222222222222222pump"
POOL2 = "Pool2222222222222222222222222222222222222222"


# --------------------------------------------------
# Hilfen
# --------------------------------------------------

def ready_pool(mint=MINT, pool=POOL, price=0.0067, liquidity=(200000, 197000),
               symbol="TTP", status="ready_for_full_risk_screen"):
    return {
        "token_name": symbol,
        "token_symbol": symbol,
        "token_address": mint,
        "pool_address": pool,
        "dex_name": "PumpSwap",
        "status": status,
        "security_check_status": "completed",
        "rejection_reasons": [] if status != "rejected" else ["lp_not_locked"],
        "missing_checks": [],
        "liquidity_check": {"dexpaprika_usd": liquidity[0], "rugcheck_usd": liquidity[1]},
        "market_cap": {"price_used_usd": price},
    }


def scan_event(pools, at):
    counts = {s: sum(p["status"] == s for p in pools)
              for s in ("rejected", "incomplete", "ready_for_full_risk_screen")}
    return {
        "timestamp": at.isoformat(),
        "event_type": "security_scan_completed",
        "details": {
            "total_pools": len(pools),
            "rejected_pools": counts["rejected"],
            "incomplete_pools": counts["incomplete"],
            "ready_for_full_risk_screen": counts["ready_for_full_risk_screen"],
            "checked_pools": pools,
        },
    }


def write_scan(data_dir, pools, at=NOW - timedelta(minutes=5), previous="same",
               gap=timedelta(hours=1)):
    """Schreibt den aktuellen Scan; standardmäßig davor einen gleichen
    Scan eine Stunde früher (Bestätigung in zwei Scans)."""
    lines = []
    if previous == "same":
        previous = copy.deepcopy(pools)
    if previous is not None:
        lines.append(scan_event(previous, at - gap))
        lines.append({"timestamp": (at - gap / 2).isoformat(),
                      "event_type": "market_data_error", "details": {}})
    lines.append(scan_event(pools, at))
    (data_dir / "events.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )


def quote(gt=0.0067, dp=0.0067, liq_gt=200000, liq_dp=200000, mint=MINT):
    return {
        "token_address": mint,
        "geckoterminal": {"price_usd": gt, "liquidity_usd": liq_gt, "error": None},
        "dexpaprika": {"price_usd": dp, "liquidity_usd": liq_dp, "error": None},
    }


def fetcher(mapping):
    calls = []

    def fetch(pairs):
        calls.append(list(pairs))
        return {pool: mapping[pool] for _, pool in pairs if pool in mapping}

    fetch.calls = calls
    return fetch


def state_file(data_dir):
    return json.loads((data_dir / "paper_state.json").read_text(encoding="utf-8"))


def trades(data_dir):
    path = data_dir / "paper_trades.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def after_scan(tmp_path, desk_config, mapping, now=NOW, outcome="success"):
    return run_paper("after-scan", tmp_path, desk_config, now=now,
                     quote_fetcher=fetcher(mapping), scan_outcome=outcome)


def monitor(tmp_path, desk_config, mapping, now):
    return run_paper("monitor", tmp_path, desk_config, now=now,
                     quote_fetcher=fetcher(mapping))


def open_ttp(tmp_path, desk_config, price=0.0067):
    write_scan(tmp_path, [ready_pool(price=price)])
    after_scan(tmp_path, desk_config, {POOL: quote(price, price)})
    return state_file(tmp_path)["open_positions"][0]


# --------------------------------------------------
# Bausteine
# --------------------------------------------------

def test_combine_quote_statuses():
    assert combine_quote(quote(1.0, 1.05), 10)["status"] == "confirmed"
    assert combine_quote(quote(1.0, 1.2), 10)["status"] == "disagree"
    assert combine_quote(quote(1.0, None), 10)["status"] == "single_source"
    assert combine_quote(quote(None, None), 10)["status"] == "missing"
    assert combine_quote(None, 10)["status"] == "missing"


def test_combine_quote_uses_low_and_high_and_smaller_liquidity():
    result = combine_quote(quote(1.0, 1.05, liq_gt=100, liq_dp=90), 10)

    assert result["price_low_usd"] == 1.0
    assert result["price_high_usd"] == 1.05
    assert result["liquidity_usd"] == 90


@pytest.mark.parametrize("bad", ["abc", -1, 0, True, None])
def test_combine_quote_ignores_invalid_prices(bad):
    assert combine_quote(quote(bad, 1.0), 10)["status"] == "single_source"


def test_trade_cost_pct():
    assert trade_cost_pct(1000, 200000, 1) == pytest.approx(2.0)
    assert trade_cost_pct(1000, None, 1) is None
    assert trade_cost_pct(1000, 0, 1) is None


def test_trailing_stop_activates_only_after_gain(desk_config):
    p = desk_config["paper_trading"]
    # Höchstkurs unter der ersten Gewinnstufe: nur der Anfangs-Stop
    position = {"entry_price_usd": 1.0, "initial_stop_usd": 0.94, "highest_price_usd": 1.05,
                "entry_cost_pct": 2.0}
    assert current_stop(position, p) == 0.94

    # +50 %: Stufe 8 -> +42 % liegt über dem Trailing (1,5 x 0,94 = 1,41)
    position["highest_price_usd"] = 1.5
    assert current_stop(position, p) == pytest.approx(1.42)


@pytest.mark.parametrize("highest, expected, kind", [
    (1.059, 0.94, "initial"),            # knapp unter +6 %: noch kein Einstand
    (1.06, 1 / 0.98, "profit_lock"),     # +6 %: Einstand nach 2 % Verkaufskosten
    (1.119, 1 / 0.98, "profit_lock"),    # bis zur nächsten Stufe Einstand
    (1.12, 1.06, "profit_lock"),         # +12 %: Stop +6 % (Trailing nur 1,0528)
    (1.179, 1.179 * 0.94, "trailing"),   # kurz vor +18 %: Trailing liegt höher
    (1.18, 1.12, "profit_lock"),         # +18 %: Stop +12 %
    (1.30, 1.24, "profit_lock"),         # +30 %: Stop +24 %
])
def test_profit_lock_steps(desk_config, highest, expected, kind):
    p = desk_config["paper_trading"]
    position = {"entry_price_usd": 1.0, "initial_stop_usd": 0.94, "highest_price_usd": highest,
                "entry_cost_pct": 2.0}
    levels = stop_levels(position, p)
    assert current_stop(position, p) == pytest.approx(expected)
    assert max(levels, key=levels.get) == kind


def test_profit_lock_needs_exit_cost_and_stays_below_high(desk_config):
    p = desk_config["paper_trading"]
    # Ohne gespeicherte Kosten: konservativ 1 % + 2 x 1 % = 3 %
    position = {"entry_price_usd": 1.0, "initial_stop_usd": 0.94, "highest_price_usd": 1.06}
    assert current_stop(position, p) == pytest.approx(1 / 0.97)
    # Einstand läge über dem Höchstkurs (sehr hohe Kosten) -> keine Stufe,
    # sonst würde sofort ohne Grund verkauft
    position["entry_cost_pct"] = 8.0
    assert "profit_lock" not in stop_levels(position, p)


def test_profit_lock_sells_at_breakeven_instead_of_loss(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    entry = position["entry_price_usd"]
    up = entry * 1.08
    monitor(tmp_path, desk_config, {POOL: quote(up, up)}, NOW + timedelta(minutes=15))
    held = state_file(tmp_path)["open_positions"][0]
    assert held["current_stop_kind"] == "profit_lock"
    assert held["current_stop_usd"] > entry

    back = entry * 1.0
    monitor(tmp_path, desk_config, {POOL: quote(back, back)}, NOW + timedelta(minutes=30))
    trade = state_file(tmp_path)["closed_trades"][0]
    assert trade["exit_reason"] == "profit_lock"
    # Verkauft beim beobachteten Kurs (hier unter der Stufe) – nie zum Stop-Kurs
    assert trade["exit_mark_price_usd"] == pytest.approx(back)


# --------------------------------------------------
# Einstieg
# --------------------------------------------------

def test_ready_token_with_confirmed_fresh_price_opens_position(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    state = state_file(tmp_path)
    paper_cfg = desk_config["paper_trading"]

    # Größe: min(8 % von 10.000 = 800, 1 % von 197.000 = 1.970, Bargeld)
    assert position["cost_basis_usd"] == pytest.approx(800)
    assert state["cash_usd"] == pytest.approx(9200)
    # Kosten: 1 % Gebühr + 2 x 800 / 197.000
    cost = 1 + 2 * 800 / 197000 * 100
    assert position["entry_price_usd"] == pytest.approx(0.0067 * (1 + cost / 100))
    assert position["initial_stop_usd"] == pytest.approx(
        position["entry_price_usd"] * (1 - paper_cfg["initial_stop_loss_pct"] / 100)
    )
    assert state["live_trading"] is False
    assert state["stats"]["trades_opened"] == 1
    assert trades(tmp_path)[0]["event"] == "paper_position_opened"


def test_entry_uses_higher_fresh_price(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool(price=1.0)])
    after_scan(tmp_path, desk_config, {POOL: quote(1.0, 1.05)})

    position = state_file(tmp_path)["open_positions"][0]
    assert position["entry_mark_price_usd"] == 1.05
    assert position["last_price_usd"] == 1.0


@pytest.mark.parametrize("q, reason", [
    (quote(0.0067, None), "fresh_price_single_source"),
    (quote(None, None), "fresh_price_missing"),
    (quote(0.0067, 0.0090), "fresh_price_disagree"),
    (quote(0.0080, 0.0080), "price_moved_since_scan"),
    (quote(0.0055, 0.0055), "price_moved_since_scan"),
])
def test_unclear_fresh_price_never_opens(tmp_path, desk_config, q, reason):
    write_scan(tmp_path, [ready_pool()])
    after_scan(tmp_path, desk_config, {POOL: q})

    state = state_file(tmp_path)
    assert state["open_positions"] == []
    assert state["cash_usd"] == 10000
    assert trades(tmp_path)[0]["reason"] == reason


@pytest.mark.parametrize("status", ["rejected", "incomplete"])
def test_only_ready_pools_are_candidates(tmp_path, desk_config, status):
    write_scan(tmp_path, [ready_pool(status=status)])
    result = after_scan(tmp_path, desk_config, {POOL: quote()})

    assert result["state"]["open_positions"] == []
    assert result["events"] == []


def test_ready_pool_with_open_checks_is_not_opened(tmp_path, desk_config):
    pool = ready_pool()
    pool["missing_checks"] = ["top10_share_pct"]
    write_scan(tmp_path, [pool])
    # latest.build_latest erkennt den Widerspruch -> Scan nicht "ok"
    result = after_scan(tmp_path, desk_config, {POOL: quote()})

    assert result["state"]["open_positions"] == []
    assert result["state"]["last_run"]["scan_status"] == "scan_inconsistent"


@pytest.mark.parametrize("outcome", ["failure", "cancelled", None])
def test_failed_scan_means_no_entries(tmp_path, desk_config, outcome):
    write_scan(tmp_path, [ready_pool()])
    result = after_scan(tmp_path, desk_config, {POOL: quote()}, outcome=outcome)

    assert result["state"]["open_positions"] == []
    assert result["state"]["last_run"]["scan_status"] == "scan_failed"


def test_old_scan_means_no_entries(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()], at=NOW - timedelta(minutes=31))
    result = after_scan(tmp_path, desk_config, {POOL: quote()})

    assert result["state"]["open_positions"] == []
    assert result["state"]["last_run"]["scan_status"] == "scan_too_old"


def test_missing_scan_log_means_no_entries(tmp_path, desk_config):
    result = after_scan(tmp_path, desk_config, {POOL: quote()})

    assert result["state"]["last_run"]["scan_status"] == "scan_log_unreadable"


@pytest.mark.parametrize("field", ["price", "liquidity"])
def test_missing_scan_values_never_open(tmp_path, desk_config, field):
    pool = ready_pool()
    if field == "price":
        pool["market_cap"]["price_used_usd"] = None
    else:
        pool["liquidity_check"]["rugcheck_usd"] = None
    write_scan(tmp_path, [pool])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert state_file(tmp_path)["open_positions"] == []
    assert trades(tmp_path)[0]["reason"] == f"scan_{field}_missing"


def test_no_second_position_in_same_token(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    write_scan(tmp_path, [ready_pool()], at=NOW + timedelta(minutes=55))
    after_scan(tmp_path, desk_config, {POOL: quote()}, now=NOW + timedelta(hours=1))

    assert len(state_file(tmp_path)["open_positions"]) == 1
    assert trades(tmp_path)[-1]["reason"] == "already_open"


def test_max_open_positions(tmp_path, desk_config):
    desk_config["paper_trading"]["max_open_positions"] = 1
    write_scan(tmp_path, [ready_pool(), ready_pool(MINT2, POOL2, symbol="B")])
    after_scan(tmp_path, desk_config, {POOL: quote(), POOL2: quote(mint=MINT2)})

    assert len(state_file(tmp_path)["open_positions"]) == 1
    assert trades(tmp_path)[-1]["reason"] == "max_open_positions"


def test_size_limited_by_pool_liquidity(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool(liquidity=(20000, 20000))])
    after_scan(tmp_path, desk_config, {POOL: quote(liq_gt=20000, liq_dp=20000)})

    # 1 % von 20.000 = 200 $ < 800 $
    assert state_file(tmp_path)["open_positions"][0]["cost_basis_usd"] == pytest.approx(200)


def test_size_below_minimum_is_skipped(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool(liquidity=(4000, 4000))])
    after_scan(tmp_path, desk_config, {POOL: quote(liq_gt=4000, liq_dp=4000)})

    assert state_file(tmp_path)["open_positions"] == []
    assert trades(tmp_path)[0]["reason"] == "size_below_minimum"


# --------------------------------------------------
# Bestätigung in zwei Scans
# --------------------------------------------------

def last_reason(tmp_path):
    return trades(tmp_path)[-1]["reason"]


def test_no_previous_scan_means_no_entry(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()], previous=None)
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert state_file(tmp_path)["open_positions"] == []
    assert last_reason(tmp_path) == "not_ready_in_previous_scan"


@pytest.mark.parametrize("previous_status", ["incomplete", "rejected"])
def test_previous_scan_not_ready_means_no_entry(tmp_path, desk_config, previous_status):
    write_scan(tmp_path, [ready_pool()], previous=[ready_pool(status=previous_status)])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert last_reason(tmp_path) == "not_ready_in_previous_scan"


def test_previous_scan_other_pool_does_not_confirm(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()], previous=[ready_pool(pool=POOL2)])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert last_reason(tmp_path) == "not_ready_in_previous_scan"


@pytest.mark.parametrize("gap", [timedelta(minutes=20), timedelta(hours=7)])
def test_previous_scan_gap_out_of_range(tmp_path, desk_config, gap):
    write_scan(tmp_path, [ready_pool()], gap=gap)
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert last_reason(tmp_path) == "previous_scan_gap_out_of_range"


def test_price_change_between_scans(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()], previous=[ready_pool(price=0.0067 / 1.2)])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert last_reason(tmp_path) == "price_changed_between_scans"


def test_liquidity_drop_between_scans(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool(liquidity=(150000, 150000))],
               previous=[ready_pool(liquidity=(200000, 200000))])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert last_reason(tmp_path) == "liquidity_dropped_between_scans"


def test_small_changes_between_scans_still_open(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool(liquidity=(190000, 185000))],
               previous=[ready_pool(price=0.0063, liquidity=(200000, 200000))])
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert len(state_file(tmp_path)["open_positions"]) == 1


def test_corrupt_line_in_log_is_skipped(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()])
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    lines.insert(1, '{"event_type": "security_scan_completed", kaputt')
    (tmp_path / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    after_scan(tmp_path, desk_config, {POOL: quote()})

    assert len(state_file(tmp_path)["open_positions"]) == 1


# --------------------------------------------------
# Ausstieg
# --------------------------------------------------

def test_stop_loss_sells_at_observed_price_not_stop(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    crash = position["initial_stop_usd"] * 0.5
    monitor(tmp_path, desk_config, {POOL: quote(crash, crash)}, NOW + timedelta(minutes=15))

    state = state_file(tmp_path)
    trade = state["closed_trades"][0]
    assert state["open_positions"] == []
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_mark_price_usd"] == crash
    assert trade["exit_price_usd"] < crash
    assert trade["pnl_usd"] < -400
    assert state["stats"]["losses"] == 1
    assert state["cash_usd"] == pytest.approx(9200 + trade["proceeds_usd"])


def test_price_above_stop_keeps_position(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    monitor(tmp_path, desk_config, {POOL: quote(0.0066, 0.0066)}, NOW + timedelta(minutes=15))

    assert len(state_file(tmp_path)["open_positions"]) == 1


def test_trailing_stop_locks_in_profit(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    entry = position["entry_price_usd"]
    high = entry * 1.5
    monitor(tmp_path, desk_config, {POOL: quote(high, high)}, NOW + timedelta(hours=1))
    # +50 %: Gewinnstufe +42 % liegt über dem Trailing (1,5 x 0,94 = 1,41)
    assert state_file(tmp_path)["open_positions"][0]["current_stop_usd"] == pytest.approx(entry * 1.42)

    drop = high * 0.93
    monitor(tmp_path, desk_config, {POOL: quote(drop, drop)}, NOW + timedelta(hours=2))
    state = state_file(tmp_path)
    assert state["closed_trades"][0]["exit_reason"] == "profit_lock"
    assert state["closed_trades"][0]["pnl_usd"] > 0
    assert state["stats"]["wins"] == 1


def test_marking_uses_lower_price(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    stop = position["initial_stop_usd"]
    # Eine Quelle unter dem Stop -> Verkauf (konservativ)
    monitor(tmp_path, desk_config, {POOL: quote(stop * 0.99, stop * 1.2)}, NOW + timedelta(minutes=15))

    assert state_file(tmp_path)["closed_trades"][0]["exit_reason"] == "stop_loss"


def test_security_rejection_closes_position(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    later = NOW + timedelta(hours=1)
    write_scan(tmp_path, [ready_pool(status="rejected")], at=later - timedelta(minutes=5))
    after_scan(tmp_path, desk_config, {POOL: quote()}, now=later)

    assert state_file(tmp_path)["closed_trades"][0]["exit_reason"] == "security_rejected"


def test_incomplete_in_later_scan_does_not_close(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    later = NOW + timedelta(hours=1)
    write_scan(tmp_path, [ready_pool(status="incomplete")], at=later - timedelta(minutes=5))
    after_scan(tmp_path, desk_config, {POOL: quote()}, now=later)

    assert len(state_file(tmp_path)["open_positions"]) == 1


def test_max_holding_time(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    monitor(tmp_path, desk_config, {POOL: quote()}, NOW + timedelta(hours=72))

    assert state_file(tmp_path)["closed_trades"][0]["exit_reason"] == "max_holding_time"


def test_missing_price_holds_then_writes_off(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    monitor(tmp_path, desk_config, {}, NOW + timedelta(hours=1))
    state = state_file(tmp_path)
    assert len(state["open_positions"]) == 1
    assert state["open_positions"][0]["price_missing_since"]

    monitor(tmp_path, desk_config, {}, NOW + timedelta(hours=26))
    state = state_file(tmp_path)
    trade = state["closed_trades"][0]
    assert trade["exit_reason"] == "price_unavailable_writeoff"
    assert trade["proceeds_usd"] == 0
    assert state["cash_usd"] == pytest.approx(9200)


def test_price_returning_resets_missing_timer(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    monitor(tmp_path, desk_config, {}, NOW + timedelta(hours=20))
    monitor(tmp_path, desk_config, {POOL: quote()}, NOW + timedelta(hours=21))
    monitor(tmp_path, desk_config, {}, NOW + timedelta(hours=30))

    assert len(state_file(tmp_path)["open_positions"]) == 1


def test_reentry_cooldown(tmp_path, desk_config):
    position = open_ttp(tmp_path, desk_config)
    crash = position["initial_stop_usd"] * 0.9
    monitor(tmp_path, desk_config, {POOL: quote(crash, crash)}, NOW + timedelta(minutes=15))

    later = NOW + timedelta(hours=2)
    write_scan(tmp_path, [ready_pool(price=crash)], at=later - timedelta(minutes=5))
    after_scan(tmp_path, desk_config, {POOL: quote(crash, crash)}, now=later)
    assert trades(tmp_path)[-1]["reason"] == "reentry_cooldown"

    later = NOW + timedelta(hours=25)
    write_scan(tmp_path, [ready_pool(price=crash)], at=later - timedelta(minutes=5))
    after_scan(tmp_path, desk_config, {POOL: quote(crash, crash)}, now=later)
    assert len(state_file(tmp_path)["open_positions"]) == 1


def test_monitor_never_opens(tmp_path, desk_config):
    write_scan(tmp_path, [ready_pool()])
    result = monitor(tmp_path, desk_config, {POOL: quote()}, NOW)

    assert result["state"]["open_positions"] == []
    assert result["written"] is False

    # auch mit offener Position: Monitor kauft nichts dazu
    write_scan(tmp_path, [ready_pool(MINT2, POOL2, symbol="B")])
    open_ttp(tmp_path, desk_config)
    write_scan(tmp_path, [ready_pool(MINT2, POOL2, symbol="B")])
    result = monitor(tmp_path, desk_config, {POOL: quote(), POOL2: quote(mint=MINT2)},
                     NOW + timedelta(minutes=15))
    assert [p["token_address"] for p in result["state"]["open_positions"]] == [MINT]
    assert result["state"]["last_run"]["scan_status"] == "not_requested"


def test_monitor_without_positions_makes_no_requests_and_writes_nothing(tmp_path, desk_config):
    fetch = fetcher({})
    result = run_paper("monitor", tmp_path, desk_config, now=NOW, quote_fetcher=fetch)

    assert fetch.calls == []
    assert result["written"] is False
    assert not (tmp_path / "paper_state.json").exists()


# --------------------------------------------------
# Depot-Datei
# --------------------------------------------------

def test_new_state_and_equity_history(tmp_path, desk_config):
    after_scan(tmp_path, desk_config, {}, now=NOW)
    state = state_file(tmp_path)

    assert state["cash_usd"] == 10000
    assert state["mode"] == "paper"
    assert state["equity_history"] == [{"at": NOW.isoformat(), "equity_usd": 10000}]


def test_monitor_with_unchanged_state_file_is_left_alone(tmp_path, desk_config):
    after_scan(tmp_path, desk_config, {}, now=NOW)
    before = (tmp_path / "paper_state.json").read_text(encoding="utf-8")
    monitor(tmp_path, desk_config, {}, NOW + timedelta(minutes=15))

    assert (tmp_path / "paper_state.json").read_text(encoding="utf-8") == before


def test_corrupt_state_stops_without_overwriting(tmp_path, desk_config):
    path = tmp_path / "paper_state.json"
    path.write_text("{kaputt", encoding="utf-8")

    with pytest.raises(PaperStateError):
        monitor(tmp_path, desk_config, {}, NOW)
    assert path.read_text(encoding="utf-8") == "{kaputt"


@pytest.mark.parametrize("change", [
    {"live_trading": True},
    {"mode": "live"},
    {"cash_usd": -1},
    {"schema_version": 99},
    {"open_positions": [{"token_address": MINT}]},
])
def test_implausible_state_is_rejected(tmp_path, desk_config, change):
    after_scan(tmp_path, desk_config, {}, now=NOW)
    state = state_file(tmp_path)
    state.update(change)
    (tmp_path / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(PaperStateError):
        monitor(tmp_path, desk_config, {}, NOW)


def test_closed_trade_accounting_is_consistent(tmp_path, desk_config):
    open_ttp(tmp_path, desk_config)
    monitor(tmp_path, desk_config, {POOL: quote()}, NOW + timedelta(hours=72))
    state = state_file(tmp_path)
    trade = state["closed_trades"][0]

    assert trade["pnl_usd"] == pytest.approx(trade["proceeds_usd"] - trade["cost_basis_usd"])
    assert state["cash_usd"] == pytest.approx(10000 + state["stats"]["realized_pnl_usd"])
    # Gleicher Kurs bei Ein- und Ausstieg -> Verlust durch Kosten
    assert trade["pnl_usd"] < 0


# --------------------------------------------------
# Kursquellen (requests gemockt)
# --------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError("fail")
            error.response = self
            raise error

    def json(self):
        return self.payload


def gt_item(pool=POOL, base=MINT, quote_token="So11111111111111111111111111111111111111112"):
    return {
        "attributes": {"address": pool, "base_token_price_usd": "0.0067",
                       "quote_token_price_usd": "150", "reserve_in_usd": "197204.4"},
        "relationships": {
            "base_token": {"data": {"id": f"solana_{base}"}},
            "quote_token": {"data": {"id": f"solana_{quote_token}"}},
        },
    }


def test_geckoterminal_quote_reads_price_of_our_token(monkeypatch):
    monkeypatch.setattr(paper.requests, "get",
                        lambda *a, **k: FakeResponse({"data": [gt_item()]}))
    result = paper.fetch_geckoterminal_quotes([(MINT, POOL)])

    assert result[POOL]["price_usd"] == pytest.approx(0.0067)
    assert result[POOL]["liquidity_usd"] == pytest.approx(197204.4)


def test_geckoterminal_quote_token_as_quote_side(monkeypatch):
    item = gt_item(base="So11111111111111111111111111111111111111112", quote_token=MINT)
    monkeypatch.setattr(paper.requests, "get", lambda *a, **k: FakeResponse({"data": [item]}))

    assert paper.fetch_geckoterminal_quotes([(MINT, POOL)])[POOL]["price_usd"] == 150


def test_geckoterminal_other_token_gives_no_price(monkeypatch):
    item = gt_item(base="Other", quote_token="Other2")
    monkeypatch.setattr(paper.requests, "get", lambda *a, **k: FakeResponse({"data": [item]}))
    result = paper.fetch_geckoterminal_quotes([(MINT, POOL)])[POOL]

    assert result["price_usd"] is None
    assert result["error"] == "token_price_missing"


def test_geckoterminal_http_error_after_bounded_retries(monkeypatch):
    from desk import market_data
    calls = []
    waits = []

    def get(*a, **k):
        calls.append(1)
        return FakeResponse({}, 429)

    monkeypatch.setattr(paper.requests, "get", get)
    monkeypatch.setattr(market_data, "_wait", waits.append)
    result = paper.fetch_geckoterminal_quotes([(MINT, POOL)])[POOL]

    assert result["price_usd"] is None
    assert result["error"] == "HTTPError HTTP 429"
    assert len(calls) == 3
    assert len(waits) == 2


def test_geckoterminal_retry_then_success(monkeypatch):
    from desk import market_data
    responses = [FakeResponse({}, 429), FakeResponse({"data": [gt_item()]})]
    monkeypatch.setattr(paper.requests, "get", lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(market_data, "_wait", lambda s: None)

    assert paper.fetch_geckoterminal_quotes([(MINT, POOL)])[POOL]["price_usd"] == pytest.approx(0.0067)


def test_dexpaprika_quote_only_for_base_token(monkeypatch):
    payload = {"base_token_id": MINT, "last_price_usd": 0.0068, "liquidity_usd": 190000}
    monkeypatch.setattr(paper.requests, "get", lambda *a, **k: FakeResponse(payload))
    assert paper.fetch_dexpaprika_quotes([(MINT, POOL)])[POOL]["price_usd"] == 0.0068

    payload["base_token_id"] = "So11111111111111111111111111111111111111112"
    result = paper.fetch_dexpaprika_quotes([(MINT, POOL)])[POOL]
    assert result["price_usd"] is None
    assert result["error"] == "token_not_base"


def test_dexpaprika_network_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(paper.requests, "get", boom)

    assert paper.fetch_dexpaprika_quotes([(MINT, POOL)])[POOL]["error"] == "ConnectionError"
