"""Gold-Backtest (XAUUSD): LBMA-Parser, Konfiguration, Ablauf ohne Netzwerk."""

import copy
from datetime import datetime, timezone

import pytest

from trend.__main__ import build_report, run_gold
from trend.config import TrendConfigError, load_gold_config, validate_gold_config
from trend.data import DAY, CandleDataError
from trend.engine import summarize
from trend.gold_data import parse_lbma_json

TODAY = int(datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp())


# --- Parser --------------------------------------------------------------------

def lbma(rows):
    return [{"d": d, "v": [usd, 1.0, 1.0]} for d, usd in rows]


def test_parses_trading_days_and_skips_today():
    payload = lbma([
        ("2026-11-06", 101),  # Freitag
        ("2026-11-09", 102),  # Montag (Wochenende ist normal)
        ("2026-11-10", 103),
        ("2026-11-11", 104),  # laufender Tag
    ])
    series, info = parse_lbma_json(payload, TODAY, 5)
    assert [c["close"] for c in series] == [101, 102, 103]
    assert all(c["open"] == c["close"] for c in series)  # ein Preis je Tag
    assert info["gaps_found"] == 0 and info["invalid_rows"] == 0
    assert info["last_day"] == "2026-11-10"


def test_old_entries_with_zero_eur_are_valid():
    payload = [{"d": "1970-01-02", "v": [35.2, 14.6, 0]}]
    assert parse_lbma_json(payload, TODAY, 5)[0][0]["close"] == 35.2


def test_long_gap_cuts_series():
    payload = lbma([("2026-10-01", 1), ("2026-10-20", 1), ("2026-10-21", 1)])
    series, info = parse_lbma_json(payload, TODAY, 5)
    assert len(series) == 2 and info["gaps_found"] == 1 and info["dropped_before_last_gap"] == 1


@pytest.mark.parametrize("bad", [
    {"d": "2026-11-04", "v": ["x"]},
    {"d": "2026-11-04", "v": [0]},
    {"d": "2026-11-04", "v": [-5]},
    {"d": "2026-11-04", "v": [True]},
    {"d": "2026-11-04", "v": []},
    {"d": "2026-11-04"},
    {"v": [1]},
    {"d": "kein-datum", "v": [1]},
    "2026-11-04",
])
def test_invalid_entry_is_never_used_and_cuts_before_it(bad):
    payload = [{"d": "2026-11-03", "v": [2]}, bad, {"d": "2026-11-05", "v": [2]}]
    series, info = parse_lbma_json(payload, TODAY, 5)
    assert [c["time"] for c in series] == [TODAY - 6 * DAY]  # nur 2026-11-05
    assert info["invalid_rows"] == 1


@pytest.mark.parametrize("payload", [[], {}, "No data", None, {"d": "2026-11-10", "v": [1]}])
def test_other_answers_are_errors(payload):
    with pytest.raises(CandleDataError):
        parse_lbma_json(payload, TODAY, 5)


def test_duplicate_or_unsorted_dates_are_errors():
    with pytest.raises(CandleDataError):
        parse_lbma_json(lbma([("2026-11-05", 1), ("2026-11-05", 1)]), TODAY, 5)


# --- Konfiguration -------------------------------------------------------------

def test_real_gold_config_is_valid():
    cfg = load_gold_config()
    assert cfg["sma_long_days"] == 200
    assert cfg["cost_pct_per_trade"] >= 0.1


GOLD = {"cost_pct_per_trade": 0.1, "start_capital_usd": 10000, "sma_short_days": 5,
        "sma_long_days": 10, "momentum_days": 4, "max_gap_days": 5}


@pytest.mark.parametrize("change", [
    lambda c: c.update(mode="live"),
    lambda c: c.pop("gold_backtest"),
    lambda c: c["gold_backtest"].update(max_gap_days=30),
    lambda c: c["gold_backtest"].pop("max_gap_days"),
    lambda c: c["gold_backtest"].update(cost_pct_per_trade=-1),
    lambda c: c["gold_backtest"].update(sma_short_days=20),
])
def test_invalid_gold_config_stops(change):
    config = {"mode": "paper", "gold_backtest": copy.deepcopy(GOLD)}
    change(config)
    with pytest.raises(TrendConfigError):
        validate_gold_config(config)


# --- Ablauf --------------------------------------------------------------------

def trading_candles(closes):
    """Nur Montag–Freitag, wie bei Gold."""
    out, t = [], int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    for close in closes:
        while datetime.fromtimestamp(t, timezone.utc).weekday() >= 5:
            t += DAY
        out.append({"time": t, "open": close, "high": close, "low": close, "close": close})
        t += DAY
    return out


def test_run_gold_and_report():
    closes = [100 + i for i in range(60)]
    entry = run_gold(GOLD, fetch=lambda gap: (trading_candles(closes), {
        "first_day": "x", "last_day": "y", "gaps_found": 0, "dropped_before_last_gap": 0}))
    assert entry["cost_pct_per_trade"] == 0.1
    assert entry["rules"]["buy_and_hold"]["total_return_pct"] > 0
    report = build_report({"generated_at": "t", "config": {"cost_pct_per_trade": 0.5, "start_capital_usd": 10000},
                           "products": {"XAUUSD (Gold)": entry}})
    assert "XAUUSD (Gold)" in report and "Swap" in report


def test_run_gold_failure_is_incomplete():
    def fail(gap):
        raise CandleDataError("LBMA-Abfrage fehlgeschlagen: HTTP 403")
    assert run_gold(GOLD, fetch=fail) == {"error": "LBMA-Abfrage fehlgeschlagen: HTTP 403"}


def test_cagr_uses_calendar_time_not_candle_count():
    # 261 Handelstage ≈ 1 Kalenderjahr: +10 % gesamt → etwa +10 % pro Jahr
    candles = trading_candles([100] * 261)
    equity = [10000 * (1 + 0.1 * k / 260) for k in range(261)]
    result = summarize(candles, 0, equity, [], 261, 10000)
    assert 9.5 <= result["cagr_pct"] <= 10.5
