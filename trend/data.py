"""Tageskerzen von der öffentlichen Coinbase-Schnittstelle (ohne API-Key).

Antwortform laut Coinbase-Doku: Liste von
``[zeit_unix, low, high, open, close, volume]``, neueste zuerst, höchstens
300 Kerzen je Abfrage. Jede abweichende Form ist ein Fehler; es wird nie
etwas aufgefüllt oder geschätzt. Fehlen Tage, wird nur der längste
lückenlose Abschnitt bis zum letzten abgeschlossenen Tag verwendet und die
Kürzung im Ergebnis vermerkt.
"""

import time
from datetime import datetime, timedelta, timezone

import requests

BASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
DAY = 86400
MAX_CANDLES_PER_REQUEST = 300
MAX_REQUESTS_PER_PRODUCT = 20  # 20 × 300 Tage ≈ 16 Jahre
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 20


class CandleDataError(RuntimeError):
    pass


def parse_candles(payload):
    """Prüft eine Coinbase-Antwort und gibt Kerzen als Dicts zurück."""
    if not isinstance(payload, list):
        raise CandleDataError("Antwort ist keine Liste")
    candles = []
    for row in payload:
        if not isinstance(row, list) or len(row) != 6:
            raise CandleDataError("Kerze hat nicht genau 6 Werte")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in row):
            raise CandleDataError("Kerze enthält keine Zahl")
        ts, low, high, open_, close, volume = row
        if not isinstance(ts, int) or ts % DAY != 0:
            raise CandleDataError("Zeitstempel ist kein Tagesbeginn (UTC)")
        if min(low, high, open_, close) <= 0 or volume < 0:
            raise CandleDataError("Preis ≤ 0 oder negatives Volumen")
        if not low <= min(open_, close) <= max(open_, close) <= high:
            raise CandleDataError("Kerze widersprüchlich (low/high passt nicht zu open/close)")
        candles.append(
            {"time": ts, "open": float(open_), "high": float(high),
             "low": float(low), "close": float(close)}
        )
    return candles


def clean_series(candles, today_start):
    """Sortiert, prüft Doppelte, entfernt den laufenden Tag, kürzt auf den
    längsten lückenlosen Abschnitt bis zum Ende.

    Gibt (kerzen, info) zurück; info nennt Kürzungen.
    """
    by_time = {}
    for candle in candles:
        if candle["time"] in by_time and by_time[candle["time"]] != candle:
            raise CandleDataError("widersprüchliche doppelte Kerze")
        by_time[candle["time"]] = candle
    series = [by_time[t] for t in sorted(by_time) if t < today_start]
    if not series:
        raise CandleDataError("keine abgeschlossenen Tageskerzen")

    start_index = 0
    gaps = 0
    for i in range(1, len(series)):
        if series[i]["time"] - series[i - 1]["time"] != DAY:
            gaps += 1
            start_index = i
    info = {
        "candles_received": len(series),
        "gaps_found": gaps,
        "dropped_before_last_gap": start_index,
    }
    return series[start_index:], info


def _get_with_retries(session, url, params):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler ({type(exc).__name__})"
        else:
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                break
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 * attempt)
    raise CandleDataError(f"Abfrage fehlgeschlagen: {last_error}")


def fetch_daily_candles(product, now=None, session=None):
    """Lädt die gesamte verfügbare Tageshistorie eines Handelspaars."""
    now = now or datetime.now(timezone.utc)
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    session = session or requests.Session()
    url = BASE_URL.format(product=product)

    collected = []
    end = today_start
    for _ in range(MAX_REQUESTS_PER_PRODUCT):
        start = end - MAX_CANDLES_PER_REQUEST * DAY
        params = {
            "granularity": DAY,
            "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end - DAY, timezone.utc).isoformat(),
        }
        batch = parse_candles(_get_with_retries(session, url, params))
        if not batch:
            break
        collected.extend(batch)
        end = start
        time.sleep(0.4)  # öffentliche Schnittstelle schonen
    else:
        raise CandleDataError("Abfragelimit erreicht, Historie evtl. unvollständig")

    series, info = clean_series(collected, today_start)
    info["first_day"] = _day(series[0]["time"])
    info["last_day"] = _day(series[-1]["time"])
    return series, info


def _day(ts):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ts)).date().isoformat()
