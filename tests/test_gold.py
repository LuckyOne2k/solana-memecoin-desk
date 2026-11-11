"""Gold-Backtest (XAUUSD): Twelve-Data-Parser, Konfiguration, Ablauf ohne Netzwerk."""

import copy
from datetime import datetime, timezone

import pytest

from trend.__main__ import build_report, run_gold
from trend.config import TrendConfigError, load_gold_config, validate_gold_config
from trend.data import DAY, CandleDataError
from trend.engine import summarize
from trend.gold_data import fetch_xauusd_daily, parse_twelvedata

TODAY = int(datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp())


# --- Parser --------------------------------------------------------------------

def td(rows):
    """Twelve-Data-Antwort; rows älteste zuerst, geliefert wird neueste zuerst."""
    values = [{"datetime": d, "open": str(o), "high": str(h), "low": str(l), "close": str(c)}
              for d, o, h, l, c in rows]
    return {"meta": {"symbol": "XAU/USD", "interval": "1day", "currency_base": "Gold Spot"},
            "values": list(reversed(values)), "status": "ok"}


def good(day, price=2):
    return (day, price, price + 1, price - 1, price)


def test_parses_trading_days_and_skips_today():
    payload = td([
        ("2026-11-06", 100, 102, 99, 101),   # Freitag
        ("2026-11-09", 101, 103, 100, 102),  # Montag (Wochenende ist normal)
        ("2026-11-10", 102, 104, 101, 103),
        ("2026-11-11", 103, 105, 102, 104),  # laufender Tag
    ])
    series, info = parse_twelvedata(payload, TODAY, 5)
    assert [c["close"] for c in series] == [101, 102, 103]
    assert info["gaps_found"] == 0 and info["invalid_rows"] == 0
    assert info["last_day"] == "2026-11-10"


def test_long_gap_cuts_series():
    payload = td([good("2026-10-01"), good("2026-10-20"), good("2026-10-21")])
    series, info = parse_twelvedata(payload, TODAY, 5)
    assert len(series) == 2 and info["gaps_found"] == 1 and info["dropped_before_last_gap"] == 1


def broken(change):
    payload = td([good("2026-11-03"), good("2026-11-04"), good("2026-11-05")])
    change(payload["values"][1])  # mittlerer Tag (2026-11-04)
    return payload


@pytest.mark.parametrize("change", [
    lambda c: c.update(open="x"),
    lambda c: c.update(close="0"),
    lambda c: c.update(high=True),
    lambda c: c.pop("low"),
    lambda c: c.pop("datetime"),
    lambda c: c.update(datetime="kein-datum"),
    lambda c: c.update(low="10"),  # low über open/close
])
def test_invalid_candle_is_never_used_and_cuts_before_it(change):
    series, info = parse_twelvedata(broken(change), TODAY, 5)
    assert [c["time"] for c in series] == [TODAY - 6 * DAY]  # nur 2026-11-05
    assert info["invalid_rows"] == 1


@pytest.mark.parametrize("payload", [
    [], "No data", None,
    {"code": 401, "message": "**apikey** parameter is incorrect", "status": "error"},
    {"meta": {"symbol": "EUR/USD", "interval": "1day"}, "values": [], "status": "ok"},
    {"meta": {"symbol": "XAU/USD", "interval": "1h"}, "values": [], "status": "ok"},
    {"meta": {"symbol": "XAU/USD", "interval": "1day"}, "status": "ok"},
    {"meta": {"symbol": "XAU/USD", "interval": "1day"}, "values": [], "status": "ok"},
])
def test_other_answers_are_errors(payload):
    with pytest.raises(CandleDataError):
        parse_twelvedata(payload, TODAY, 5)


def test_error_message_of_provider_is_not_copied():
    payload = {"code": 401, "message": "key abc123 invalid", "status": "error"}
    with pytest.raises(CandleDataError) as exc:
        parse_twelvedata(payload, TODAY, 5)
    assert "abc123" not in str(exc.value) and "401" in str(exc.value)


def test_duplicate_or_wrong_order_is_error():
    payload = td([good("2026-11-05"), good("2026-11-05")])
    with pytest.raises(CandleDataError):
        parse_twelvedata(payload, TODAY, 5)


# --- Abruf (ohne Netzwerk) ------------------------------------------------------

class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class RecordingSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_missing_key_is_incomplete_without_request(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_API_KEY", raising=False)
    session = RecordingSession(FakeResponse(200))
    with pytest.raises(CandleDataError, match="TWELVEDATA_API_KEY fehlt"):
        fetch_xauusd_daily(5, session=session)
    assert session.calls == []


def test_key_only_in_header_and_never_in_error(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "geheim-123")
    session = RecordingSession(FakeResponse(401))
    with pytest.raises(CandleDataError) as exc:
        fetch_xauusd_daily(5, session=session)
    assert "geheim" not in str(exc.value) and "HTTP 401" in str(exc.value)
    url, kwargs = session.calls[0]
    assert url == "https://api.twelvedata.com/time_series"
    assert "geheim" not in url and "geheim" not in str(kwargs["params"])
    assert kwargs["headers"] == {"Authorization": "apikey geheim-123"}
    assert kwargs["params"]["symbol"] == "XAU/USD" and kwargs["params"]["interval"] == "1day"
    assert len(session.calls) == 1  # 401 wird nicht wiederholt


def test_fetch_parses_answer(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "k")
    now = datetime(2026, 11, 11, 7, tzinfo=timezone.utc)
    session = RecordingSession(FakeResponse(200, td([good("2026-11-10")])))
    series, info = fetch_xauusd_daily(5, now=now, session=session)
    assert len(series) == 1 and info["source"].startswith("Twelve Data")


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
        raise CandleDataError("Twelve-Data-Abfrage fehlgeschlagen: HTTP 403")
    assert run_gold(GOLD, fetch=fail) == {"error": "Twelve-Data-Abfrage fehlgeschlagen: HTTP 403"}


def test_cagr_uses_calendar_time_not_candle_count():
    # 261 Handelstage ≈ 1 Kalenderjahr: +10 % gesamt → etwa +10 % pro Jahr
    candles = trading_candles([100] * 261)
    equity = [10000 * (1 + 0.1 * k / 260) for k in range(261)]
    result = summarize(candles, 0, equity, [], 261, 10000)
    assert 9.5 <= result["cagr_pct"] <= 10.5
