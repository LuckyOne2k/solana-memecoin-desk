"""Vergleich schnell / mittel / lang (ohne Netzwerk)."""

import copy
from datetime import datetime, timezone

import pytest

from trend.__main__ import GOLD, build_horizon_report, run_horizons
from trend.config import TrendConfigError, load_horizon_config, validate_horizon_config
from trend.data import DAY, HOUR, CandleDataError, clean_series, parse_candles
from trend.engine import run_rule, weeks_positive_pct
from trend.gold_data import parse_twelvedata
from trend.horizons import fast_rules, medium_rules

HCFG = {"fast": {"history_days": 365, "sma_hours": 4, "cross_short_hours": 2, "cross_long_hours": 6},
        "medium": {"breakout_entry_days": 3, "breakout_exit_days": 2,
                   "cross_short_days": 2, "cross_long_days": 4}}
CFG = {"products": ["AAA-USD"], "cost_pct_per_trade": 0.5, "start_capital_usd": 10000}
GOLD_CFG = {"cost_pct_per_trade": 0.1, "max_gap_days": 5}
START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())


def series(closes, step=DAY, start=START):
    return [{"time": start + i * step, "open": c, "high": c, "low": c, "close": c}
            for i, c in enumerate(closes)]


# --- Konfiguration -------------------------------------------------------------

def test_real_horizon_config_is_valid():
    cfg = load_horizon_config()
    assert cfg["fast"]["sma_hours"] == 24
    assert cfg["medium"]["breakout_entry_days"] == 20


@pytest.mark.parametrize("change", [
    lambda c: c.update(mode="live"),
    lambda c: c.pop("horizon_backtest"),
    lambda c: c["horizon_backtest"].pop("fast"),
    lambda c: c["horizon_backtest"]["fast"].update(sma_hours=0),
    lambda c: c["horizon_backtest"]["fast"].update(cross_short_hours=48),
    lambda c: c["horizon_backtest"]["medium"].update(cross_short_days=30),
    lambda c: c["horizon_backtest"]["medium"].update(breakout_entry_days=2.5),
])
def test_invalid_horizon_config_stops(change):
    config = {"mode": "paper", "horizon_backtest": copy.deepcopy(HCFG)}
    change(config)
    with pytest.raises(TrendConfigError):
        validate_horizon_config(config)


# --- Regeln --------------------------------------------------------------------

def test_breakout_enters_on_new_high_and_holds_until_low():
    rules, _ = medium_rules(HCFG["medium"])
    rule = rules["breakout"][1]
    closes = [10, 10, 10, 11, 10.5, 10.6, 9, 9]
    signals = [rule(closes, i) for i in range(3, len(closes))]
    # 11 = neues 3-Tage-Hoch → investiert; 10.5/10.6 halten; 9 < 2-Tage-Tief → raus
    assert signals == [True, True, True, False, False]


def test_breakout_must_be_called_day_by_day():
    rules, _ = medium_rules(HCFG["medium"])
    rule = rules["breakout"][1]
    rule([1, 2, 3, 4, 5, 6], 3)
    with pytest.raises(ValueError):
        rule([1, 2, 3, 4, 5, 6], 5)


def test_fresh_breakout_state_per_call():
    a, _ = medium_rules(HCFG["medium"])
    b, _ = medium_rules(HCFG["medium"])
    assert a["breakout"][1] is not b["breakout"][1]


def test_fast_rules_warmup_covers_longest_average():
    _, warmup = fast_rules(HCFG["fast"])
    assert warmup == 6


def test_no_lookahead_in_execution():
    # Signal aus Schluss Tag t, Ausführung zur Eröffnung t+1
    closes = [10, 10, 10, 10, 20, 20, 20]
    candles = series(closes)
    candles[5]["open"] = 15
    rules, warmup = medium_rules(HCFG["medium"])
    equity, trades, _ = run_rule(candles, rules["breakout"][1], warmup, 0, 1000)
    # Kauf erst am Tag nach dem Ausbruch zum Eröffnungskurs 15, nicht zu 20
    assert equity[-1] == pytest.approx(1000 / 15 * 20)


# --- Wochen im Plus ------------------------------------------------------------

def test_weeks_positive_counts_week_closes():
    candles = series([1] * 21)  # 2024-01-01 ist Montag → 3 volle Wochen
    equity = [100] * 7 + [110] * 7 + [105] * 7
    assert weeks_positive_pct(candles, 0, equity) == 50.0


def test_weeks_positive_none_with_single_week():
    assert weeks_positive_pct(series([1] * 3), 0, [1, 2, 3]) is None


# --- Stundenkerzen -------------------------------------------------------------

def test_hourly_candles_need_hour_grid_and_no_gaps():
    payload = [[START + 2 * HOUR, 1, 2, 1, 2, 0], [START + HOUR, 1, 2, 1, 2, 0], [START, 1, 2, 1, 2, 0]]
    candles = parse_candles(payload, step=HOUR)
    series_, info = clean_series(candles, START + 10 * HOUR, step=HOUR)
    assert len(series_) == 3 and info["gaps_found"] == 0
    with pytest.raises(CandleDataError):
        parse_candles([[START + 60, 1, 2, 1, 2, 0]], step=HOUR)
    gap = clean_series(candles[:1] + candles[2:], START + 10 * HOUR, step=HOUR)
    assert gap[1]["gaps_found"] == 1


def test_daily_parser_still_rejects_hourly_timestamps():
    with pytest.raises(CandleDataError):
        parse_candles([[START + HOUR, 1, 2, 1, 2, 0]])


def test_gold_hourly_parser():
    payload = {"meta": {"symbol": "XAU/USD", "interval": "1h"}, "status": "ok", "values": [
        {"datetime": "2026-11-10 12:00:00", "open": "2", "high": "3", "low": "1", "close": "2"},
        {"datetime": "2026-11-10 11:00:00", "open": "2", "high": "3", "low": "1", "close": "2"},
    ]}
    now = int(datetime(2026, 11, 10, 12, tzinfo=timezone.utc).timestamp())
    series_, _ = parse_twelvedata(payload, now, 5, "1h")
    assert len(series_) == 1  # laufende Stunde 12:00 ausgeschlossen
    bad = copy.deepcopy(payload)
    bad["values"][1]["datetime"] = "2026-11-10 11:30:00"
    assert parse_twelvedata(bad, now + HOUR, 5, "1h")[1]["invalid_rows"] == 1
    with pytest.raises(CandleDataError):
        parse_twelvedata(payload, now, 5, "1day")  # falsches Intervall in meta


# --- Ablauf --------------------------------------------------------------------

def test_run_horizons_and_report():
    daily = {"AAA-USD": series([100 + i for i in range(60)]), GOLD: series([50 + i for i in range(60)])}
    hourly = series([100 + i % 7 for i in range(400)], step=HOUR)
    out = run_horizons(CFG, GOLD_CFG, HCFG, daily,
                       fetch_hourly=lambda p, d: (hourly, {}),
                       fetch_gold_hourly=lambda g: (hourly, {}))
    assert set(out) == {"fast", "medium"}
    assert out["medium"][GOLD]["cost_pct_per_trade"] == 0.1
    assert out["fast"]["AAA-USD"]["cost_pct_per_trade"] == 0.5
    for group in out.values():
        for entry in group.values():
            assert set(entry["rules"]) >= {"buy_and_hold", "sma_cross"}
    report = build_horizon_report(out)
    assert "Schnell (Stundenkerzen)" in report and "Wochen im Plus" in report


def test_run_horizons_marks_failures_incomplete():
    def fail(*args):
        raise CandleDataError("HTTP 503")
    out = run_horizons(CFG, GOLD_CFG, HCFG, {}, fetch_hourly=fail, fetch_gold_hourly=fail)
    assert out["medium"]["AAA-USD"] == {"error": "Tageskerzen fehlen (siehe oben)"}
    assert out["fast"][GOLD] == {"error": "HTTP 503"}
    assert "Unvollständig" in build_horizon_report(out)
