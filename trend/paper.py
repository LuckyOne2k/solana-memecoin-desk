"""Paper-Trader des Trend-Bots: ``python -m trend.paper --data-dir desk-data``.

Nur Simulation, kein echtes Geld, keine Wallet, keine Börsen-Konten.

Regel (aus dem Backtest, Nutzerfreigabe 2026-11-11): Ein Coin ist
investiert, solange sein letzter abgeschlossener Tagesschluss über dem
Durchschnitt der letzten ``sma_long_days`` Schlusskurse liegt. Das Signal
eines Tages wird einmal am Folgetag ausgewertet (wie im Backtest: Signal
aus Schlusskurs, Ausführung danach). Startkapital gleich auf die Coins
verteilt, ohne Umschichtung; jeder Coin hat sein eigenes Bargeld.

Datenregeln (fehlende oder widersprüchliche Daten führen nie zu einem
Handel):
- Tageskerzen von Coinbase; der Vortag muss vorhanden sein und die letzten
  ``sma_long_days`` Tage lückenlos, sonst ``incomplete``.
- Gehandelt wird nur mit aktuellem Kurs aus Coinbase UND Kraken mit
  höchstens ``max_price_deviation_pct`` Abweichung. Kauf zum höheren, Verkauf
  zum niedrigeren Kurs, jeweils plus ``cost_pct_per_trade`` Kosten.
- Scheitert etwas, bleibt der Tag offen und wird im nächsten Lauf erneut
  versucht. Ein beschädigtes Depot wird nie überschrieben.

Dateien im Datenordner: ``trend_state.json`` (Depot) und
``trend_trades.jsonl`` (jeder Kauf/Verkauf, nur anhängen).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from trend.config import KRAKEN_PAIRS, load_trend_paper_config
from trend.data import DAY, CandleDataError, _get_with_retries, clean_series, parse_candles

SCHEMA_VERSION = 1
STATE_FILE = "trend_state.json"
TRADES_FILE = "trend_trades.jsonl"
MAX_EQUITY_POINTS = 3000
CANDLE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker"
TIMEOUT_SECONDS = 20


class TrendStateError(RuntimeError):
    """Depotdatei fehlt nicht, ist aber unbrauchbar: nichts schreiben."""


class PriceError(RuntimeError):
    pass


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat()


def _num(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value == value


# --- Daten -------------------------------------------------------------------

def fetch_recent_candles(product, today_start, session):
    """Höchstens 300 abgeschlossene Tageskerzen bis einschließlich Vortag."""
    params = {
        "granularity": DAY,
        "start": datetime.fromtimestamp(today_start - 300 * DAY, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(today_start - DAY, timezone.utc).isoformat(),
    }
    payload = _get_with_retries(session, CANDLE_URL.format(product=product), params)
    series, _info = clean_series(parse_candles(payload), today_start)
    return series


def compute_signal(candles, signal_day_start, sma_days):
    """Signal für ``signal_day_start``. Wirft CandleDataError bei Lücken."""
    if not candles or candles[-1]["time"] != signal_day_start:
        raise CandleDataError("Tageskerze des Vortags fehlt")
    if len(candles) < sma_days:
        raise CandleDataError(f"weniger als {sma_days} lückenlose Tage")
    closes = [c["close"] for c in candles[-sma_days:]]
    average = sum(closes) / sma_days
    close = closes[-1]
    return {"close_usd": close, "sma_usd": round(average, 8), "long": close > average}


def _positive_price(value, source):
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise PriceError(f"{source}: Kurs ist keine Zahl") from None
    if not price > 0 or price == float("inf"):
        raise PriceError(f"{source}: Kurs ungültig")
    return price


def fetch_coinbase_price(product, session):
    response = session.get(COINBASE_TICKER_URL.format(product=product), timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise PriceError(f"Coinbase: HTTP {response.status_code}")
    data = response.json()
    if not isinstance(data, dict):
        raise PriceError("Coinbase: unerwartete Antwort")
    return _positive_price(data.get("price"), "Coinbase")


def fetch_kraken_price(product, session):
    response = session.get(KRAKEN_TICKER_URL, params={"pair": KRAKEN_PAIRS[product]}, timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise PriceError(f"Kraken: HTTP {response.status_code}")
    data = response.json()
    if not isinstance(data, dict) or data.get("error"):
        raise PriceError("Kraken: Fehlermeldung in der Antwort")
    result = data.get("result")
    if not isinstance(result, dict) or len(result) != 1:
        raise PriceError("Kraken: unerwartete Antwort")
    ticker = next(iter(result.values()))
    last = ticker.get("c") if isinstance(ticker, dict) else None
    if not isinstance(last, list) or not last:
        raise PriceError("Kraken: letzter Kurs fehlt")
    return _positive_price(last[0], "Kraken")


def fetch_fresh_prices(product, session):
    """Gibt {coinbase, kraken} zurück oder wirft PriceError."""
    try:
        return {
            "coinbase": fetch_coinbase_price(product, session),
            "kraken": fetch_kraken_price(product, session),
        }
    except requests.RequestException as exc:
        raise PriceError(f"Netzwerkfehler ({type(exc).__name__})") from None
    except ValueError:
        raise PriceError("Antwort ist kein JSON") from None


def check_prices(prices, max_deviation_pct):
    low, high = min(prices.values()), max(prices.values())
    deviation = (high / low - 1) * 100
    if deviation > max_deviation_pct:
        raise PriceError(f"Kursquellen weichen {deviation:.2f} % ab (> {max_deviation_pct} %)")
    return low, high, round(deviation, 3)


# --- Depot -------------------------------------------------------------------

def new_state(cfg, now):
    share = round(cfg["start_capital_usd"] / len(cfg["products"]), 2)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "paper",
        "live_trading": False,
        "rule": "close_above_sma",
        "sma_days": cfg["sma_long_days"],
        "cost_pct_per_trade": cfg["cost_pct_per_trade"],
        "starting_balance_usd": round(share * len(cfg["products"]), 2),
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "products": {
            p: {
                "cash_usd": share,
                "units": 0.0,
                "entry_price_usd": None,
                "cost_basis_usd": None,
                "opened_at": None,
                "signal_day": None,
                "signal": None,
                "last_close_usd": None,
                "status": "pending",
                "reason": None,
            }
            for p in cfg["products"]
        },
        "equity_history": [],
        "stats": {"trades_closed": 0, "wins": 0, "realized_pnl_usd": 0.0},
    }


def validate_state(state, cfg):
    if not isinstance(state, dict):
        raise TrendStateError("Depot ist kein Objekt")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise TrendStateError("unbekannte Schema-Version")
    if state.get("mode") != "paper" or state.get("live_trading") is not False:
        raise TrendStateError("Depot ist nicht im Paper-Modus")
    if state.get("sma_days") != cfg["sma_long_days"]:
        raise TrendStateError("Regel im Depot passt nicht zur Konfiguration")
    products = state.get("products")
    if not isinstance(products, dict) or set(products) != set(cfg["products"]):
        raise TrendStateError("Coins im Depot passen nicht zur Konfiguration")
    for name, p in products.items():
        if not isinstance(p, dict) or not _num(p.get("cash_usd")) or not _num(p.get("units")):
            raise TrendStateError(f"{name}: Bargeld/Bestand fehlt")
        if p["cash_usd"] < 0 or p["units"] < 0:
            raise TrendStateError(f"{name}: negativer Wert")
        if p["units"] > 0 and not (_num(p.get("entry_price_usd")) and _num(p.get("cost_basis_usd"))):
            raise TrendStateError(f"{name}: offene Position ohne Einstieg")
    if not isinstance(state.get("equity_history"), list) or not isinstance(state.get("stats"), dict):
        raise TrendStateError("Verlauf oder Statistik fehlt")
    return state


def load_state(path, cfg, now):
    if not path.exists():
        return new_state(cfg, now)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrendStateError(f"Depot nicht lesbar ({type(exc).__name__})") from None
    return validate_state(state, cfg)


def equity_usd(state):
    """Depotwert zu den letzten Tagesschlusskursen (ohne Verkaufskosten)."""
    total = 0.0
    for p in state["products"].values():
        total += p["cash_usd"]
        if p["units"] > 0:
            mark = p["last_close_usd"] if _num(p.get("last_close_usd")) else p["entry_price_usd"]
            total += p["units"] * mark
    return round(total, 2)


def _buy(p, product, prices, cfg, now, signal_day):
    low, high, deviation = check_prices(prices, cfg["max_price_deviation_pct"])
    cost = cfg["cost_pct_per_trade"] / 100
    spend = p["cash_usd"]
    units = spend * (1 - cost) / high
    p.update(cash_usd=0.0, units=units, entry_price_usd=high, cost_basis_usd=round(spend, 2),
             opened_at=_iso(now))
    return {
        "time": _iso(now), "product": product, "side": "buy", "signal_day": signal_day,
        "price_usd": high, "prices": prices, "deviation_pct": deviation,
        "amount_usd": round(spend, 2), "units": units, "cost_pct": cfg["cost_pct_per_trade"],
        "mode": "paper",
    }


def _sell(p, product, prices, cfg, now, signal_day, stats):
    low, high, deviation = check_prices(prices, cfg["max_price_deviation_pct"])
    cost = cfg["cost_pct_per_trade"] / 100
    proceeds = p["units"] * low * (1 - cost)
    pnl = proceeds - p["cost_basis_usd"]
    trade = {
        "time": _iso(now), "product": product, "side": "sell", "signal_day": signal_day,
        "price_usd": low, "prices": prices, "deviation_pct": deviation,
        "amount_usd": round(proceeds, 2), "units": p["units"], "cost_pct": cfg["cost_pct_per_trade"],
        "entry_price_usd": p["entry_price_usd"], "opened_at": p["opened_at"],
        "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl / p["cost_basis_usd"] * 100, 2),
        "mode": "paper",
    }
    p.update(cash_usd=round(proceeds, 2), units=0.0, entry_price_usd=None, cost_basis_usd=None,
             opened_at=None)
    stats["trades_closed"] = stats.get("trades_closed", 0) + 1
    stats["wins"] = stats.get("wins", 0) + (1 if pnl > 0 else 0)
    stats["realized_pnl_usd"] = round(stats.get("realized_pnl_usd", 0.0) + pnl, 2)
    return trade


def process_product(product, p, state, cfg, now, session, fetch_candles, fetch_prices):
    """Wertet das Signal des Vortags aus. Gibt (entscheidung, trade|None) zurück."""
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    signal_start = today_start - DAY
    signal_day = _day(signal_start)
    if p.get("signal_day") == signal_day:
        return "already_done", None

    try:
        signal = compute_signal(fetch_candles(product, today_start, session), signal_start, cfg["sma_long_days"])
    except (CandleDataError, requests.RequestException, ValueError) as exc:
        p.update(status="incomplete", reason=f"Tageskerzen: {exc}")
        return "incomplete", None

    trade = None
    if signal["long"] != (p["units"] > 0):
        try:
            prices = fetch_prices(product, session)
            if signal["long"]:
                trade = _buy(p, product, prices, cfg, now, signal_day)
            else:
                trade = _sell(p, product, prices, cfg, now, signal_day, state["stats"])
        except PriceError as exc:
            p.update(status="incomplete", reason=f"Kurs: {exc}")
            return "incomplete", None

    p.update(signal_day=signal_day, signal=signal, last_close_usd=signal["close_usd"],
             status="ok", reason=None)
    if trade:
        return trade["side"], trade
    return "hold_invested" if p["units"] > 0 else "hold_cash", None


def run(data_dir, cfg, now=None, session=None, fetch_candles=fetch_recent_candles,
        fetch_prices=fetch_fresh_prices):
    """Ein Lauf. Gibt (ergebnis, trades) zurück; schreibt nur bei Änderungen."""
    now = now or datetime.now(timezone.utc)
    data_dir = Path(data_dir)
    state_path = data_dir / STATE_FILE
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now < midnight + timedelta(minutes=cfg["min_minutes_after_midnight"]):
        return {"status": "waiting_for_daily_candle", "written": False}, []

    existed = state_path.exists()
    state = load_state(state_path, cfg, now)
    before = json.dumps(state, sort_keys=True)
    session = session or requests.Session()

    decisions, trades = {}, []
    for product in cfg["products"]:
        decision, trade = process_product(
            product, state["products"][product], state, cfg, now, session, fetch_candles, fetch_prices
        )
        decisions[product] = decision
        if trade:
            trades.append(trade)

    signal_day = _day(int(midnight.timestamp()) - DAY)
    history = state["equity_history"]
    all_done = all(p.get("signal_day") == signal_day for p in state["products"].values())
    if all_done and (not history or history[-1].get("day") != signal_day):
        history.append({"day": signal_day, "equity_usd": equity_usd(state)})
        del history[:-MAX_EQUITY_POINTS]

    changed = not existed or json.dumps(state, sort_keys=True) != before
    if changed:
        state["updated_at"] = _iso(now)
        state["last_run"] = {"at": _iso(now), "decisions": decisions}
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
        if trades:
            with (data_dir / TRADES_FILE).open("a", encoding="utf-8") as handle:
                for trade in trades:
                    handle.write(json.dumps(trade, ensure_ascii=False) + "\n")

    status = "incomplete" if "incomplete" in decisions.values() else "ok"
    return {"status": status, "written": changed, "decisions": decisions,
            "equity_usd": equity_usd(state)}, trades


def trade_message(trades):
    lines = ["Trend-Bot (Paper):"]
    for t in trades:
        if t["side"] == "buy":
            lines.append(f"Kauf {t['product']} {t['amount_usd']:.2f} $ zu {t['price_usd']:.2f} $")
        else:
            lines.append(
                f"Verkauf {t['product']} {t['amount_usd']:.2f} $ zu {t['price_usd']:.2f} $ "
                f"({t['pnl_pct']:+.1f} %)"
            )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paper-Trader Trend-Bot (nur Simulation)")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)

    cfg = load_trend_paper_config()
    try:
        result, trades = run(args.data_dir, cfg)
    except TrendStateError as exc:
        print(f"Depot unbrauchbar, nichts geschrieben: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    if trades:
        from desk.notify import send_telegram

        print("Telegram:", send_telegram(trade_message(trades)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
