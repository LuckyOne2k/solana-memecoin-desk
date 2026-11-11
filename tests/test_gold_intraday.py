"""Gold intraday Long/Short (ohne Netzwerk)."""

import copy
from datetime import datetime, timezone

import pytest

from trend.__main__ import GOLD, build_intraday_report, run_gold_intraday
from trend.config import TrendConfigError, load_gold_intraday_config, validate_gold_intraday_config
from trend.data import HOUR
from trend.gold_intraday import backtest_gold_intraday, london_breakout, mean_reversion, simulate

MONDAY = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())  # Montag 00:00 UTC
MR = {"window_hours": 4, "entry_z": 1.0, "max_hold_hours": 3, "stop_pct": 2.0}
LB = {"range_start_hour": 0, "range_end_hour": 7, "close_hour": 20}
ICFG = {"mean_reversion": MR, "london_breakout": LB}


def bar(t, o, c=None, high=None, low=None):
    c = o if c is None else c
    return {"time": t, "open": o, "close": c,
            "high": max(o, c) if high is None else high, "low": min(o, c) if low is None else low}


def hourly(closes, start=MONDAY):
    """Eröffnung = Schluss der Vorkerze, damit Ausführungspreise eindeutig sind."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(bar(start + i * HOUR, prev, c))
        prev = c
    return out


def run(candles, decide, start=0, cost=0.0):
    return simulate(candles, decide, start, cost, 1000)


# --- Konfiguration -------------------------------------------------------------

def test_real_config_is_valid():
    cfg = load_gold_intraday_config()
    assert cfg["mean_reversion"]["window_hours"] == 24
    assert cfg["london_breakout"]["close_hour"] == 20


@pytest.mark.parametrize("change", [
    lambda c: c.update(mode="live"),
    lambda c: c.pop("gold_intraday"),
    lambda c: c["gold_intraday"].pop("mean_reversion"),
    lambda c: c["gold_intraday"]["mean_reversion"].update(entry_z=0),
    lambda c: c["gold_intraday"]["mean_reversion"].update(window_hours=True),
    lambda c: c["gold_intraday"]["mean_reversion"].update(window_hours=4.5),
    lambda c: c["gold_intraday"]["mean_reversion"].update(stop_pct="1"),
    lambda c: c["gold_intraday"]["london_breakout"].update(range_end_hour=19),
    lambda c: c["gold_intraday"]["london_breakout"].update(range_start_hour=8),
])
def test_invalid_config_rejected(change):
    config = {"mode": "paper", "gold_intraday": copy.deepcopy(ICFG)}
    change(config)
    with pytest.raises(TrendConfigError):
        validate_gold_intraday_config(config)


# --- Rechenwerk ----------------------------------------------------------------

def test_short_profits_when_price_falls_and_costs_apply():
    candles = hourly([100, 100, 90, 90])
    plan = {0: -1, 2: 0}
    equity, trades, _ = simulate(candles, lambda c, i, s: plan.get(i, s["pos"]), 0, 1.0, 1000)
    # Short zur Eröffnung von Kerze 1 (100), geschlossen zur Eröffnung von Kerze 3 (90)
    expected = 1000 * 0.99 * 1.1 * 0.99
    assert trades == [(-1, pytest.approx(expected / 1000 - 1))]
    assert equity[-1] == pytest.approx(expected)


def test_signal_executes_at_next_open_not_same_close():
    candles = [bar(MONDAY, 100, 100), bar(MONDAY + HOUR, 105, 110), bar(MONDAY + 2 * HOUR, 110, 110)]
    equity, trades, _ = run(candles, lambda c, i, s: 1)
    assert equity[0] == 1000            # Kerze 0: noch flach
    assert equity[1] == pytest.approx(1000 * 110 / 105)  # Einstieg zu 105, nicht 100


def test_reversal_closes_then_opens():
    candles = hourly([100, 100, 110, 110, 100])
    plan = {0: 1, 2: -1}
    _, trades, _ = run(candles, lambda c, i, s: plan.get(i, s["pos"]))
    assert [d for d, _ in trades] == [1, -1]
    assert trades[0][1] == pytest.approx(0.1)
    assert trades[1][1] == pytest.approx(10 / 110)


# --- Rückkehr zum Mittelwert ---------------------------------------------------

def test_mean_reversion_long_after_drop_exits_at_mean():
    _, decide, warmup = mean_reversion(MR)
    candles = hourly([100, 100, 100, 100, 90, 98, 100, 100])
    _, trades, _ = run(candles, decide, start=warmup - 1)
    assert len(trades) == 1 and trades[0][0] == 1
    # Einstieg 90; Schluss 98 liegt über dem 4-h-Ø 97 → Ausstieg zur nächsten Eröffnung (98)
    assert trades[0][1] == pytest.approx(98 / 90 - 1)


def test_mean_reversion_short_after_spike():
    _, decide, warmup = mean_reversion(MR)
    candles = hourly([100, 100, 100, 100, 110, 101, 100, 100])
    _, trades, _ = run(candles, decide, start=warmup - 1)
    assert trades[0][0] == -1 and trades[0][1] > 0


def test_mean_reversion_stop_loss():
    _, decide, warmup = mean_reversion(MR)
    candles = hourly([100, 100, 100, 100, 90, 85, 80, 80])
    _, trades, _ = run(candles, decide, start=warmup - 1)
    assert trades[0][0] == 1
    # Schluss 85 ist > 2 % Verlust → Ausstieg zur nächsten Eröffnung (85)
    assert trades[0][1] == pytest.approx(85 / 90 - 1)


def test_mean_reversion_max_hold_counts_clock_hours():
    _, decide, warmup = mean_reversion({**MR, "stop_pct": 10.0})
    closes = [100, 100, 100, 100, 90, 89, 88, 87, 86, 86]
    _, trades, invested = run(hourly(closes), decide, start=warmup - 1)
    assert trades[0][0] == 1
    assert trades[0][1] == pytest.approx(87 / 90 - 1)  # nach 3 Stunden geschlossen


def test_mean_reversion_flat_series_never_trades():
    _, decide, warmup = mean_reversion(MR)
    _, trades, _ = run(hourly([100] * 30), decide, start=warmup - 1)
    assert trades == []


# --- London-Ausbruch -----------------------------------------------------------

def day_candles(day_start, range_hl=(101, 99), after=None, skip_hours=()):
    """Kerzen 0–23 Uhr: Spanne 0–6 Uhr zwischen 99 und 101, danach Schlüsse aus ``after``."""
    after = after or {}
    out, price = [], 100.0
    for h in range(24):
        if h in skip_hours:
            continue
        t = day_start + h * HOUR
        if h < 7:
            out.append(bar(t, 100, 100, high=range_hl[0], low=range_hl[1]))
        else:
            close = after.get(h, price)
            out.append(bar(t, price, close))
            price = close
    return out


def test_breakout_long_closed_at_close_hour():
    _, decide, _ = london_breakout(LB)
    candles = day_candles(MONDAY, after={8: 102, 12: 105})
    _, trades, _ = run(candles, decide)
    assert trades == [(1, pytest.approx(105 / 102 - 1))]  # Einstieg Eröffnung 9 Uhr, Ausstieg 20 Uhr


def test_breakout_short_and_only_one_trade_per_day():
    _, decide, _ = london_breakout(LB)
    candles = day_candles(MONDAY, after={8: 98, 10: 101.5, 11: 98, 13: 97})
    _, trades, _ = run(candles, decide)
    # Short ab 98, Stop bei Schluss über 101 (10 Uhr) → Ausstieg 101.5; kein zweiter Trade
    assert trades == [(-1, pytest.approx(1 - 101.5 / 98))]


def test_breakout_no_trade_without_full_range():
    _, decide, ranges = london_breakout(LB)
    candles = day_candles(MONDAY, after={8: 102}, skip_hours=(3,))
    _, trades, _ = run(candles, decide)
    assert trades == [] and ranges[MONDAY] is None


def test_breakout_range_uses_only_range_hours():
    _, decide, _ = london_breakout(LB)
    # 7-Uhr-Kerze mit hohem Hoch darf die Spanne nicht verändern
    candles = day_candles(MONDAY, after={8: 102})
    candles[7]["high"] = 200
    _, trades, _ = run(candles, decide)
    assert trades and trades[0][0] == 1


def test_breakout_no_entry_after_last_entry_hour():
    _, decide, _ = london_breakout(LB)
    candles = day_candles(MONDAY, after={19: 103})
    _, trades, _ = run(candles, decide)
    assert trades == []


# --- Gesamtablauf und Bericht ---------------------------------------------------

def long_series():
    candles = []
    for d in range(30):
        wobble = {8: 100 + (d % 3) - 1, 15: 100}
        candles += day_candles(MONDAY + d * 86400, after=wobble)
    return candles


def test_backtest_has_all_rules_and_counts():
    data = backtest_gold_intraday(long_series(), ICFG, 0.1, 10000)
    assert set(data["rules"]) == {"buy_and_hold", "mean_reversion", "london_breakout"}
    lb = data["rules"]["london_breakout"]
    assert lb["trades"] == lb["long_trades"] + lb["short_trades"]
    assert lb["trades_per_day"] is not None


def test_too_little_history_is_error():
    with pytest.raises(ValueError):
        backtest_gold_intraday(day_candles(MONDAY), ICFG, 0.1, 10000)


def test_run_without_hourly_is_incomplete():
    out = run_gold_intraday({"cost_pct_per_trade": 0.1, "start_capital_usd": 10000}, ICFG, {})
    assert "error" in out
    assert "Unvollständig" in build_intraday_report(out)


def test_report_renders():
    out = run_gold_intraday({"cost_pct_per_trade": 0.1, "start_capital_usd": 10000}, ICFG,
                            {GOLD: long_series()})
    report = build_intraday_report(out)
    assert "London-Ausbruch" in report and "Long/Short" in report
