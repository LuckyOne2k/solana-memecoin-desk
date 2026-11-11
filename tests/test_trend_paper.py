"""Paper-Trader des Trend-Bots (nur Simulation, ohne Netzwerk)."""

import copy
import json
from datetime import datetime, timezone

import pytest
import requests

from trend.config import TrendConfigError, load_trend_paper_config, validate_trend_paper_config
from trend.data import DAY, CandleDataError
from trend import paper
from trend.paper import (
    PriceError,
    TrendStateError,
    check_prices,
    compute_signal,
    fetch_kraken_price,
    run,
    trade_message,
)

CFG = {
    "mode": "paper",
    "products": ["BTC-USD", "ETH-USD"],
    "cost_pct_per_trade": 0.5,
    "start_capital_usd": 10000,
    "sma_short_days": 5,
    "sma_long_days": 10,
    "momentum_days": 4,
    "max_price_deviation_pct": 1.0,
    "min_minutes_after_midnight": 15,
}

NOW = datetime(2026, 11, 11, 1, 5, tzinfo=timezone.utc)
TODAY = int(datetime(2026, 11, 11, tzinfo=timezone.utc).timestamp())
YESTERDAY = TODAY - DAY


def candles(closes, end=YESTERDAY):
    start = end - (len(closes) - 1) * DAY
    return [{"time": start + i * DAY, "open": c, "high": c, "low": c, "close": c}
            for i, c in enumerate(closes)]


RISING = candles([100 + i for i in range(20)])   # Schluss 119 > Durchschnitt
FALLING = candles([200 - i for i in range(20)])  # Schluss 181 < Durchschnitt


def fake_candles(mapping):
    def fetch(product, today_start, session):
        value = mapping[product]
        if isinstance(value, Exception):
            raise value
        return value
    return fetch


def fake_prices(mapping):
    calls = []

    def fetch(product, session):
        calls.append(product)
        value = mapping[product]
        if isinstance(value, Exception):
            raise value
        return value
    fetch.calls = calls
    return fetch


def state_of(tmp_path):
    return json.loads((tmp_path / "trend_state.json").read_text(encoding="utf-8"))


def trades_of(tmp_path):
    path = tmp_path / "trend_trades.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# --- Konfiguration -------------------------------------------------------------

def test_real_config_has_paper_section():
    cfg = load_trend_paper_config()
    assert cfg["sma_long_days"] == 200
    assert cfg["products"] == ["BTC-USD", "ETH-USD", "SOL-USD"]
    assert 0 < cfg["max_price_deviation_pct"] <= 5


def base_config():
    return {
        "mode": "paper",
        "trend_backtest": {k: CFG[k] for k in (
            "products", "cost_pct_per_trade", "start_capital_usd",
            "sma_short_days", "sma_long_days", "momentum_days")},
        "trend_paper": {"max_price_deviation_pct": 1.0, "min_minutes_after_midnight": 15},
    }


@pytest.mark.parametrize("change", [
    lambda c: c.update(mode="live"),
    lambda c: c.pop("trend_paper"),
    lambda c: c["trend_paper"].update(max_price_deviation_pct=0),
    lambda c: c["trend_paper"].update(max_price_deviation_pct=10),
    lambda c: c["trend_paper"].update(max_price_deviation_pct=True),
    lambda c: c["trend_paper"].update(min_minutes_after_midnight=1.5),
    lambda c: c["trend_paper"].pop("min_minutes_after_midnight"),
    lambda c: c["trend_backtest"].update(products=["DOGE-USD"]),
])
def test_invalid_paper_config_stops(change):
    config = base_config()
    change(config)
    with pytest.raises(TrendConfigError):
        validate_trend_paper_config(config)


# --- Signal und Kurse ----------------------------------------------------------

def test_signal_long_and_flat():
    assert compute_signal(RISING, YESTERDAY, 10)["long"] is True
    assert compute_signal(FALLING, YESTERDAY, 10)["long"] is False


def test_signal_equal_to_average_is_not_long():
    assert compute_signal(candles([50] * 12), YESTERDAY, 10)["long"] is False


def test_signal_requires_yesterday_and_enough_days():
    with pytest.raises(CandleDataError):
        compute_signal(candles([1] * 20, end=YESTERDAY - DAY), YESTERDAY, 10)
    with pytest.raises(CandleDataError):
        compute_signal(candles([1] * 9), YESTERDAY, 10)
    with pytest.raises(CandleDataError):
        compute_signal([], YESTERDAY, 10)


def test_price_check_rejects_large_deviation():
    assert check_prices({"coinbase": 100.0, "kraken": 100.5}, 1.0)[:2] == (100.0, 100.5)
    with pytest.raises(PriceError):
        check_prices({"coinbase": 100.0, "kraken": 101.5}, 1.0)


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def test_kraken_parser_accepts_documented_form():
    payload = {"error": [], "result": {"XXBTZUSD": {"c": ["91234.5", "0.01"]}}}
    assert fetch_kraken_price("BTC-USD", FakeSession(FakeResponse(200, payload))) == 91234.5


@pytest.mark.parametrize("payload", [
    {"error": ["EQuery:Unknown asset pair"], "result": {}},
    {"error": [], "result": {}},
    {"error": [], "result": {"A": {"c": ["1"]}, "B": {"c": ["2"]}}},
    {"error": [], "result": {"XXBTZUSD": {"c": []}}},
    {"error": [], "result": {"XXBTZUSD": {"c": ["0"]}}},
    {"error": [], "result": {"XXBTZUSD": {"c": ["abc"]}}},
    [],
])
def test_kraken_parser_rejects_other_forms(payload):
    with pytest.raises(PriceError):
        fetch_kraken_price("BTC-USD", FakeSession(FakeResponse(200, payload)))


def test_coinbase_http_error_is_price_error():
    with pytest.raises(PriceError):
        paper.fetch_coinbase_price("BTC-USD", FakeSession(FakeResponse(503, {})))


def test_network_error_becomes_price_error():
    class Broken:
        def get(self, *a, **k):
            raise requests.ConnectionError("x")
    with pytest.raises(PriceError):
        paper.fetch_fresh_prices("BTC-USD", Broken())


# --- Ablauf --------------------------------------------------------------------

GOOD_PRICES = {"BTC-USD": {"coinbase": 120.0, "kraken": 120.6},
               "ETH-USD": {"coinbase": 180.0, "kraken": 180.0}}


def test_first_run_buys_long_coin_and_keeps_flat_coin_in_cash(tmp_path):
    prices = fake_prices(GOOD_PRICES)
    result, trades = run(tmp_path, CFG, now=NOW,
                         fetch_candles=fake_candles({"BTC-USD": RISING, "ETH-USD": FALLING}),
                         fetch_prices=prices)

    assert result["status"] == "ok"
    assert result["decisions"] == {"BTC-USD": "buy", "ETH-USD": "hold_cash"}
    assert prices.calls == ["BTC-USD"]  # Kurse nur bei Handel
    state = state_of(tmp_path)
    assert state["mode"] == "paper" and state["live_trading"] is False
    btc = state["products"]["BTC-USD"]
    assert btc["cash_usd"] == 0
    assert btc["entry_price_usd"] == 120.6  # Kauf zum höheren Kurs
    assert btc["units"] == pytest.approx(5000 * 0.995 / 120.6)
    assert state["products"]["ETH-USD"]["cash_usd"] == 5000
    assert state["equity_history"] == [{"day": "2026-11-10", "equity_usd": round(5000 + btc["units"] * 119, 2)}]
    assert [t["side"] for t in trades_of(tmp_path)] == ["buy"]
    assert trades[0]["mode"] == "paper"


def test_second_run_same_day_does_nothing_and_writes_nothing(tmp_path):
    fetch = fake_candles({"BTC-USD": RISING, "ETH-USD": FALLING})
    run(tmp_path, CFG, now=NOW, fetch_candles=fetch, fetch_prices=fake_prices(GOOD_PRICES))
    before = (tmp_path / "trend_state.json").read_text()

    def must_not_fetch(*a):
        raise AssertionError("kein Abruf erwartet")

    result, trades = run(tmp_path, CFG, now=NOW.replace(hour=5), fetch_candles=must_not_fetch,
                         fetch_prices=must_not_fetch)
    assert result["written"] is False and trades == []
    assert (tmp_path / "trend_state.json").read_text() == before


def test_sell_next_day_at_lower_price_with_costs(tmp_path):
    run(tmp_path, CFG, now=NOW, fetch_candles=fake_candles({"BTC-USD": RISING, "ETH-USD": FALLING}),
        fetch_prices=fake_prices(GOOD_PRICES))
    units = state_of(tmp_path)["products"]["BTC-USD"]["units"]

    next_day = NOW.replace(day=12)
    falling_btc = candles([200 - i for i in range(20)], end=YESTERDAY + DAY)
    eth = candles([200 - i for i in range(20)], end=YESTERDAY + DAY)
    result, trades = run(tmp_path, CFG, now=next_day,
                         fetch_candles=fake_candles({"BTC-USD": falling_btc, "ETH-USD": eth}),
                         fetch_prices=fake_prices({"BTC-USD": {"coinbase": 110.0, "kraken": 110.5}}))

    assert result["decisions"] == {"BTC-USD": "sell", "ETH-USD": "hold_cash"}
    sell = trades[0]
    assert sell["price_usd"] == 110.0  # Verkauf zum niedrigeren Kurs
    assert sell["amount_usd"] == round(units * 110.0 * 0.995, 2)
    assert sell["pnl_usd"] < 0
    state = state_of(tmp_path)
    assert state["products"]["BTC-USD"]["units"] == 0
    assert state["stats"]["trades_closed"] == 1 and state["stats"]["wins"] == 0
    assert [h["day"] for h in state["equity_history"]] == ["2026-11-10", "2026-11-11"]
    assert len(trades_of(tmp_path)) == 2


@pytest.mark.parametrize("bad_prices", [
    PriceError("Kraken: HTTP 503"),
    {"coinbase": 100.0, "kraken": 105.0},  # Quellen widersprechen sich
])
def test_no_trade_without_two_agreeing_prices(tmp_path, bad_prices):
    result, trades = run(tmp_path, CFG, now=NOW,
                         fetch_candles=fake_candles({"BTC-USD": RISING, "ETH-USD": FALLING}),
                         fetch_prices=fake_prices({"BTC-USD": bad_prices}))

    assert result["status"] == "incomplete" and trades == []
    btc = state_of(tmp_path)["products"]["BTC-USD"]
    assert btc["units"] == 0 and btc["cash_usd"] == 5000
    assert btc["status"] == "incomplete" and btc["signal_day"] is None  # nächster Lauf versucht erneut
    assert state_of(tmp_path)["equity_history"] == []  # Tag nicht vollständig


def test_missing_candles_are_incomplete_and_retried(tmp_path):
    result, _ = run(tmp_path, CFG, now=NOW,
                    fetch_candles=fake_candles({"BTC-USD": CandleDataError("HTTP 500"), "ETH-USD": FALLING}),
                    fetch_prices=fake_prices(GOOD_PRICES))
    assert result["decisions"]["BTC-USD"] == "incomplete"

    result, trades = run(tmp_path, CFG, now=NOW.replace(hour=2),
                         fetch_candles=fake_candles({"BTC-USD": RISING, "ETH-USD": FALLING}),
                         fetch_prices=fake_prices(GOOD_PRICES))
    assert result["decisions"] == {"BTC-USD": "buy", "ETH-USD": "already_done"}
    assert len(state_of(tmp_path)["equity_history"]) == 1


def test_waits_shortly_after_midnight(tmp_path):
    result, _ = run(tmp_path, CFG, now=NOW.replace(hour=0, minute=10),
                    fetch_candles=None, fetch_prices=None)
    assert result == {"status": "waiting_for_daily_candle", "written": False}
    assert not (tmp_path / "trend_state.json").exists()


@pytest.mark.parametrize("content", [
    "kein json",
    json.dumps({"schema_version": 1, "mode": "live", "live_trading": True}),
])
def test_broken_state_is_never_overwritten(tmp_path, content):
    (tmp_path / "trend_state.json").write_text(content, encoding="utf-8")
    with pytest.raises(TrendStateError):
        run(tmp_path, CFG, now=NOW, fetch_candles=fake_candles({}), fetch_prices=fake_prices({}))
    assert (tmp_path / "trend_state.json").read_text(encoding="utf-8") == content


def test_state_with_other_coins_is_rejected(tmp_path):
    run(tmp_path, CFG, now=NOW, fetch_candles=fake_candles({"BTC-USD": FALLING, "ETH-USD": FALLING}),
        fetch_prices=fake_prices({}))
    other = copy.deepcopy(CFG)
    other["products"] = ["BTC-USD"]
    with pytest.raises(TrendStateError):
        run(tmp_path, other, now=NOW.replace(day=12), fetch_candles=None, fetch_prices=None)


def test_main_returns_error_on_broken_state(tmp_path, monkeypatch):
    original = paper.run
    monkeypatch.setattr(paper, "run", lambda d, c: original(d, c, now=NOW))
    (tmp_path / "trend_state.json").write_text("{", encoding="utf-8")
    assert paper.main(["--data-dir", str(tmp_path)]) == 1
    assert (tmp_path / "trend_state.json").read_text() == "{"


def test_trade_message_marks_paper():
    text = trade_message([{"side": "buy", "product": "BTC-USD", "amount_usd": 10.0, "price_usd": 1.0}])
    assert "Paper" in text and "BTC-USD" in text
