"""Tageskurse XAUUSD (Gold in US-Dollar, Kassakurs) von Stooq.

Öffentliche CSV-Abfrage ohne API-Key:
``https://stooq.com/q/d/l/?s=xauusd&i=d``, Kopfzeile
``Date,Open,High,Low,Close`` (optional ``,Volume``), älteste Zeile zuerst.

Datenregeln (es wird nie etwas aufgefüllt oder geschätzt):
- Jede andere Antwortform (keine CSV, andere Kopfzeile) ist ein Fehler.
- Fehlerhafte Zeilen (keine Zahl, Preis ≤ 0, low/high passt nicht) sowie
  Lücken über ``max_gap_days`` Kalendertage beenden den brauchbaren
  Abschnitt: verwendet wird nur der Teil nach der letzten solchen Stelle.
  Wochenenden und Feiertage (Lücken bis ``max_gap_days``) sind normal.
- Der laufende Tag wird nie verwendet (noch nicht abgeschlossen).
"""

import csv
import io
from datetime import date, datetime, timezone

import requests

from trend.data import DAY, CandleDataError

STOOQ_URL = "https://stooq.com/q/d/l/"
TIMEOUT_SECONDS = 30
ALLOWED_HEADERS = (
    ["Date", "Open", "High", "Low", "Close"],
    ["Date", "Open", "High", "Low", "Close", "Volume"],
)


def _row_to_candle(row):
    day = date.fromisoformat(row[0])
    open_, high, low, close = (float(v) for v in row[1:5])
    if not all(v == v and v != float("inf") for v in (open_, high, low, close)):
        raise ValueError("keine endliche Zahl")
    if min(open_, high, low, close) <= 0:
        raise ValueError("Preis ≤ 0")
    if not low <= min(open_, close) <= max(open_, close) <= high:
        raise ValueError("low/high passt nicht zu open/close")
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return {"time": ts, "open": open_, "high": high, "low": low, "close": close}


def parse_stooq_csv(text, today_start, max_gap_days):
    """Gibt (kerzen, info) zurück oder wirft CandleDataError."""
    if not isinstance(text, str) or not text.strip():
        raise CandleDataError("leere Antwort")
    rows = list(csv.reader(io.StringIO(text.strip())))
    if rows[0] not in ALLOWED_HEADERS:
        raise CandleDataError("Antwort ist keine Stooq-Kurstabelle")

    candles, invalid_after = [], []
    for row in rows[1:]:
        if len(row) != len(rows[0]):
            invalid_after.append(len(candles))
            continue
        try:
            candle = _row_to_candle(row)
        except ValueError:
            invalid_after.append(len(candles))
            continue
        if candles and candle["time"] <= candles[-1]["time"]:
            raise CandleDataError("Datum doppelt oder nicht aufsteigend")
        if candle["time"] >= today_start:
            continue  # laufender Tag
        candles.append(candle)
    if not candles:
        raise CandleDataError("keine abgeschlossenen Tageskurse")

    start = max(invalid_after) if invalid_after else 0
    gaps_long = 0
    for i in range(1, len(candles)):
        if (candles[i]["time"] - candles[i - 1]["time"]) / DAY > max_gap_days:
            gaps_long += 1
            start = max(start, i)
    series = candles[start:]
    if not series:
        raise CandleDataError("kein brauchbarer Abschnitt nach der letzten Fehlerstelle")
    info = {
        "candles_received": len(candles),
        "invalid_rows": len(invalid_after),
        "gaps_found": gaps_long,
        "dropped_before_last_gap": start,
        "first_day": datetime.fromtimestamp(series[0]["time"], timezone.utc).date().isoformat(),
        "last_day": datetime.fromtimestamp(series[-1]["time"], timezone.utc).date().isoformat(),
    }
    return series, info


def fetch_xauusd_daily(max_gap_days, now=None, session=None):
    now = now or datetime.now(timezone.utc)
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    session = session or requests.Session()
    last_error = None
    for _attempt in range(3):
        try:
            response = session.get(STOOQ_URL, params={"s": "xauusd", "i": "d"}, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler ({type(exc).__name__})"
            continue
        if response.status_code == 200:
            return parse_stooq_csv(response.text, today_start, max_gap_days)
        last_error = f"HTTP {response.status_code}"
        if response.status_code != 429 and response.status_code < 500:
            break
    raise CandleDataError(f"Stooq-Abfrage fehlgeschlagen: {last_error}")
