"""Tageskurse Gold in US-Dollar: LBMA Gold Price (PM), die offizielle
Referenz für den Goldpreis (entspricht XAUUSD am Nachmittag in London).

Öffentliche JSON-Datei ohne API-Key: ``https://prices.lbma.org.uk/json/gold_pm.json``,
Form laut LBMA: Liste von ``{"d": "JJJJ-MM-TT", "v": [USD, GBP, EUR]}``,
älteste zuerst. Es gibt je Handelstag genau einen Preis; Eröffnung und
Schluss sind deshalb beide dieser Preis (Signal aus dem Preis von Tag t,
Ausführung zum Preis von Tag t+1).

(Stooq lieferte am 2026-11-11 HTTP 404 und wird nicht mehr genutzt.)

Datenregeln (es wird nie etwas aufgefüllt oder geschätzt):
- Jede andere Antwortform ist ein Fehler.
- Fehlerhafte Einträge (kein Datum, USD-Preis keine Zahl oder ≤ 0) sowie
  Lücken über ``max_gap_days`` Kalendertage beenden den brauchbaren
  Abschnitt: verwendet wird nur der Teil nach der letzten solchen Stelle.
  Wochenenden und Feiertage (Lücken bis ``max_gap_days``) sind normal.
- Der laufende Tag wird nie verwendet.
"""

from datetime import date, datetime, timezone

import requests

from trend.data import DAY, CandleDataError

LBMA_URL = "https://prices.lbma.org.uk/json/gold_pm.json"
TIMEOUT_SECONDS = 30


def _entry_to_candle(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("d"), str):
        raise ValueError("kein Datum")
    day = date.fromisoformat(entry["d"])
    values = entry.get("v")
    if not isinstance(values, list) or not values:
        raise ValueError("keine Preise")
    usd = values[0]
    if isinstance(usd, bool) or not isinstance(usd, (int, float)) or usd != usd or not 0 < usd < float("inf"):
        raise ValueError("USD-Preis ungültig")
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    price = float(usd)
    return {"time": ts, "open": price, "high": price, "low": price, "close": price}


def parse_lbma_json(payload, today_start, max_gap_days):
    """Gibt (kerzen, info) zurück oder wirft CandleDataError."""
    if not isinstance(payload, list) or not payload:
        raise CandleDataError("Antwort ist keine LBMA-Preisliste")
    candles, invalid_after = [], []
    for entry in payload:
        try:
            candle = _entry_to_candle(entry)
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
        "source": "LBMA Gold Price PM (USD)",
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
            response = session.get(LBMA_URL, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler ({type(exc).__name__})"
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                raise CandleDataError("LBMA-Antwort ist kein JSON") from None
            return parse_lbma_json(payload, today_start, max_gap_days)
        last_error = f"HTTP {response.status_code}"
        if response.status_code != 429 and response.status_code < 500:
            break
    raise CandleDataError(f"LBMA-Abfrage fehlgeschlagen: {last_error}")
