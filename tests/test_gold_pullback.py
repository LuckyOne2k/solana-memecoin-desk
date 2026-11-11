"""Gold: Rücksetzer im Aufwärtstrend (ohne Netzwerk)."""

import copy
from datetime import datetime, timezone

import pytest

from trend.__main__ import GOLD, build_pullback_report, run_gold_pullback
from trend.config import TrendConfigError, load_gold_pullback_config, validate_gold_pullback_config
from trend.data import DAY
from trend.gold_intraday import simulate
from trend.gold_pullback import backtest_gold_pullback, pullback_low, pullback_rsi, rsi

START = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
CFG = {"sma_days": 5, "low_days": 3, "exit_sma_days": 2, "rsi_days": 2,
       "rsi_entry": 10, "rsi_exit": 70, "max_hold_days": 3}
GOLD_CFG = {"cost_pct_per_trade": 0.1, "start_capital_usd": 10000}


def series(closes):
    """Eröffnung = Schluss des Vortags."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append({"time": START + i * DAY, "open": prev, "high": max(prev, c), "low": min(prev, c), "close": c})
        prev = c
    return out


def run(closes, decide):
    return simulate(series(closes), decide, 4, 0.0, 1000)


def test_real_config_is_valid():
    cfg = load_gold_pullback_config()
    assert cfg["sma_days"] == 200 and cfg["max_hold_days"] == 10


@pytest.mark.parametrize("change", [
    lambda c: c.update(mode="live"),
    lambda c: c.pop("gold_pullback"),
    lambda c: c["gold_pullback"].update(rsi_entry=0),
    lambda c: c["gold_pullback"].update(rsi_exit=True),
    lambda c: c["gold_pullback"].update(low_days=5),  # nicht kürzer als sma_days
    lambda c: c["gold_pullback"].pop("max_hold_days"),
])
def test_invalid_config_rejected(change):
    config = {"mode": "paper", "gold_pullback": copy.deepcopy(CFG)}
    change(config)
    with pytest.raises(TrendConfigError):
        validate_gold_pullback_config(config)


def test_rsi_values():
    assert rsi([1, 2, 3], 2, 2) == 100.0
    assert rsi([3, 2, 1], 2, 2) == 0.0
    assert rsi([1, 1, 1], 2, 2) == 50.0
    assert rsi([1, 2, 1], 2, 2) == 50.0
    assert rsi([1, 2], 1, 2) is None


def test_low_rule_buys_dip_in_uptrend_and_sells_on_recovery():
    _, decide = pullback_low(CFG)
    # Aufwärtstrend, dann Rücksetzer auf 3-Tage-Tief (über 5-Tage-Ø), dann Erholung
    closes = [100, 104, 108, 112, 116, 120, 119, 118, 121, 122, 122]
    _, trades, _ = run(closes, decide)
    # Tag 6 (119): kein 3-Tage-Tief. Tag 7 (118): Tief und über 5-Tage-Ø (117) → Kauf zur
    # Eröffnung Tag 8 (118); Schluss 121 über 2-Tage-Ø → Verkauf zur Eröffnung Tag 9 (121)
    assert trades[0][1] == pytest.approx(121 / 118 - 1)


def test_low_rule_no_buy_below_trend():
    _, decide = pullback_low(CFG)
    closes = [110, 108, 106, 104, 102, 100, 98, 96, 94, 92]
    _, trades, _ = run(closes, decide)
    assert trades == []


def test_max_hold_days():
    _, decide = pullback_low({**CFG, "exit_sma_days": 2})
    closes = [100, 104, 108, 112, 116, 120, 119, 118, 117.9, 117.8, 117.7, 117.6, 117.5]
    _, trades, _ = run(closes, decide)
    # Kauf Eröffnung Tag 8 (118), nach 3 Handelstagen (Schluss Tag 10) → Verkauf Eröffnung Tag 11
    assert trades[0][1] == pytest.approx(117.7 / 118 - 1)


def test_rsi_rule():
    _, decide = pullback_rsi(CFG)
    closes = [100, 102, 104, 106, 108, 110, 109, 108.5, 112, 113, 113]
    _, trades, _ = run(closes, decide)
    # RSI(2) nach zwei Verlusttagen = 0 < 10, Schluss 108.5 über 5-Tage-Ø → Kauf zur Eröffnung 108.5
    # Schluss 112 → RSI 100/… > 70 → Verkauf zur Eröffnung 112
    assert trades and trades[0][1] == pytest.approx(112 / 108.5 - 1)


def long_closes():
    """Steigender Verlauf: vier Tage +2 %, dann ein Tag −5 %."""
    closes, price = [], 100.0
    for k in range(400):
        price *= 0.95 if k % 5 == 4 else 1.02
        closes.append(price)
    return closes


def test_backtest_and_report():
    data = backtest_gold_pullback(series(long_closes()), {**CFG, "sma_days": 20}, 0.1, 10000)
    assert set(data["rules"]) == {"buy_and_hold", "above_sma", "pullback_low", "pullback_rsi"}
    assert data["rules"]["pullback_low"]["trades"] > 0
    out = run_gold_pullback(GOLD_CFG, CFG, {GOLD: series(long_closes())})
    assert "Rücksetzer" in build_pullback_report(out)


def test_missing_daily_is_incomplete():
    out = run_gold_pullback(GOLD_CFG, CFG, {})
    assert "error" in out and "Unvollständig" in build_pullback_report(out)


def test_too_little_history():
    with pytest.raises(ValueError):
        backtest_gold_pullback(series([100] * 50), CFG, 0.1, 10000)
