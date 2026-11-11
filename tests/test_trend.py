import copy

import pytest

from trend.__main__ import build_report, run
from trend.config import TrendConfigError, load_trend_config, validate_trend_config
from trend.data import DAY, CandleDataError, clean_series, parse_candles
from trend.engine import backtest_product, max_drawdown_pct, run_rule

CFG = {
    "products": ["AAA-USD", "BBB-USD"],
    "cost_pct_per_trade": 0.5,
    "start_capital_usd": 10000,
    "sma_short_days": 5,
    "sma_long_days": 10,
    "momentum_days": 4,
}


def candles_from_closes(closes, start=1_600_000_000 // DAY * DAY):
    out = []
    previous = closes[0]
    for i, close in enumerate(closes):
        out.append({"time": start + i * DAY, "open": previous, "high": max(previous, close),
                    "low": min(previous, close), "close": close})
        previous = close
    return out


# --- Konfiguration -----------------------------------------------------------

def test_real_config_is_valid_and_paper_only():
    cfg = load_trend_config()
    assert cfg["products"] == ["BTC-USD", "ETH-USD", "SOL-USD"]
    assert cfg["cost_pct_per_trade"] >= 0.5


@pytest.mark.parametrize("change", [
    {"mode": "live"},
    {"trend_backtest": None},
])
def test_config_rejects_live_or_missing(change):
    config = {"mode": "paper", "trend_backtest": dict(CFG)}
    config.update(change)
    with pytest.raises(TrendConfigError):
        validate_trend_config(config)


@pytest.mark.parametrize("key,value", [
    ("products", []), ("products", ["BTC-EUR"]), ("products", ["A-USD", "A-USD"]),
    ("cost_pct_per_trade", "0.5"), ("cost_pct_per_trade", -1), ("cost_pct_per_trade", True),
    ("sma_short_days", 10), ("momentum_days", 1.5), ("start_capital_usd", 0),
])
def test_config_rejects_bad_values(key, value):
    section = dict(CFG, **{key: value})
    with pytest.raises(TrendConfigError):
        validate_trend_config({"mode": "paper", "trend_backtest": section})


# --- Daten --------------------------------------------------------------------

def test_parse_candles_accepts_documented_form():
    rows = [[DAY * 3, 9.0, 12.0, 10.0, 11.0, 5.0]]
    assert parse_candles(rows) == [{"time": DAY * 3, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}]


@pytest.mark.parametrize("payload", [
    {"message": "error"},
    [[DAY, 9, 12, 10, 11]],
    [[DAY, 9, 12, 10, "11", 1]],
    [[DAY + 5, 9, 12, 10, 11, 1]],
    [[DAY, 0, 12, 10, 11, 1]],
    [[DAY, 10.5, 12, 10, 11, 1]],  # low über open
    [[DAY, 9, 10.5, 10, 11, 1]],  # high unter close
])
def test_parse_candles_rejects_bad_form(payload):
    with pytest.raises(CandleDataError):
        parse_candles(payload)


def test_clean_series_drops_running_day_and_keeps_part_after_last_gap():
    days = [1, 2, 4, 5, 6, 7]
    candles = [{"time": d * DAY, "open": 1, "high": 1, "low": 1, "close": 1} for d in days]
    series, info = clean_series(candles, today_start=7 * DAY)
    assert [c["time"] // DAY for c in series] == [4, 5, 6]
    assert info["gaps_found"] == 1
    assert info["dropped_before_last_gap"] == 2


def test_clean_series_rejects_conflicting_duplicates():
    a = {"time": DAY, "open": 1, "high": 1, "low": 1, "close": 1}
    b = dict(a, close=2)
    with pytest.raises(CandleDataError):
        clean_series([a, b], today_start=10 * DAY)


# --- Rechenwerk ---------------------------------------------------------------

def test_signal_executes_next_day_open_without_lookahead():
    # Regel kennt den Sprung an Tag 3 erst nach dessen Schluss -> Kauf am Tag 4
    candles = candles_from_closes([10, 10, 10, 20, 20, 20])
    rule = lambda closes, i: closes[i] > 15
    equity, trades, invested = run_rule(candles, rule, start_index=1, cost_pct=0, capital=100)
    assert equity[:3] == [100, 100, 100]  # Tage 1-3 in Bargeld, Sprung verpasst
    assert invested == 2


def test_costs_apply_on_buy_and_sell():
    candles = candles_from_closes([10] * 6)
    rule = lambda closes, i: True
    equity, trades, _ = run_rule(candles, rule, start_index=1, cost_pct=1, capital=100)
    assert equity[-1] == pytest.approx(100 * 0.99 * 0.99)
    assert trades == [pytest.approx(0.99 * 0.99 - 1)]


def test_trend_rule_avoids_crash_buy_and_hold_does_not():
    closes = [100 + i for i in range(30)] + [130 - 5 * i for i in range(1, 20)]
    results, _ = backtest_product(candles_from_closes(closes), CFG)
    assert results["buy_and_hold"]["total_return_pct"] < results["above_sma_long"]["total_return_pct"]
    assert results["above_sma_long"]["max_drawdown_pct"] > results["buy_and_hold"]["max_drawdown_pct"]


def test_all_rules_share_start_day():
    closes = [100 + (i % 7) for i in range(80)]
    _, curves = backtest_product(candles_from_closes(closes), CFG)
    starts = {min(c) for c in curves.values()}
    assert len(starts) == 1


def test_too_short_history_is_error():
    with pytest.raises(ValueError):
        backtest_product(candles_from_closes([1.0] * 20), CFG)


def test_max_drawdown():
    assert max_drawdown_pct([100, 150, 75, 200]) == pytest.approx(-50)


# --- Gesamtlauf ---------------------------------------------------------------

def test_run_marks_failed_product_incomplete_and_skips_portfolio():
    good = candles_from_closes([100 + i for i in range(80)])

    def fetch(product):
        if product == "BBB-USD":
            raise CandleDataError("HTTP 500")
        return good, {"first_day": "x", "last_day": "y", "gaps_found": 0, "dropped_before_last_gap": 0,
                      "candles_received": 80}

    result = run(copy.deepcopy(CFG), fetch=fetch)
    assert result["products"]["BBB-USD"] == {"error": "HTTP 500"}
    assert "portfolio" not in result
    report = build_report(result)
    assert "Unvollständig" in report


def test_run_with_all_products_builds_portfolio_and_report():
    good = candles_from_closes([100 + (i % 13) * 2 for i in range(120)])

    def fetch(product):
        return good, {"first_day": "x", "last_day": "y", "gaps_found": 0, "dropped_before_last_gap": 0,
                      "candles_received": 120}

    result = run(copy.deepcopy(CFG), fetch=fetch)
    assert set(result["portfolio"]) == {"buy_and_hold", "above_sma_long", "sma_cross", "momentum"}
    assert result["mode"] == "paper"
    assert "Kaufen und halten" in build_report(result)
