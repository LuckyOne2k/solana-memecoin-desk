"""Tageskurse XAUUSD von Twelve Data (kostenloser Schlüssel, nur Kurse lesen).

Abfrage laut Twelve-Data-Doku:
``GET https://api.twelvedata.com/time_series`` mit ``symbol=XAU/USD``,
``interval=1day``, ``outputsize=5000``, ``timezone=UTC``; Schlüssel im
Header ``Authorization: apikey …`` (nicht in der URL). Antwort:
``{"meta": {"symbol": "XAU/USD", "interval": "1day", …},
"values": [{"datetime": "JJJJ-MM-TT", "open", "high", "low", "close"}, …],
"status": "ok"}``, neueste zuerst. Fehler kommen teils mit HTTP 200 und
``"status": "error"``.

Sicherheit: Der Schlüssel kommt aus ``TWELVEDATA_API_KEY`` und erscheint nie
in Ausgaben; Fehler nennen nur HTTP-Status bzw. Fehlercode.

(Stooq lieferte am 2026-11-11 HTTP 404, LBMA HTTP 403; OANDA bietet für
EU-Konten keinen API-Schlüssel an.)

Datenregeln (es wird nie etwas aufgefüllt oder geschätzt):
- Jede andere Antwortform ist ein Fehler; der laufende Tag wird nie verwendet.
- Fehlerhafte Kerzen (keine Zahl, Preis ≤ 0, low/high passt nicht) sowie
  Lücken über ``max_gap_days`` Kalendertage beenden den brauchbaren
  Abschnitt: verwendet wird nur der Teil nach der letzten solchen Stelle.
  Wochenenden und Feiertage (Lücken bis ``max_gap_days``) sind normal.
"""

import os
from datetime import date, datetime, timezone

import requests

from trend.data import DAY, CandleDataError

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
KEY_ENV = "TWELVEDATA_API_KEY"
MAX_CANDLES = 5000
TIMEOUT_SECONDS = 30


def _price(raw):
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError("Preis hat falschen Typ")
    value = float(raw)  # Twelve Data liefert Preise als Text
    if not 0 < value < float("inf"):
        raise ValueError("Preis ungültig")
    return value


def _to_candle(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("datetime"), str):
        raise ValueError("kein Datum")
    day = date.fromisoformat(raw["datetime"])
    open_, high, low, close = (_price(raw[k]) for k in ("open", "high", "low", "close"))
    if not low <= min(open_, close) <= max(open_, close) <= high:
        raise ValueError("low/high passt nicht zu open/close")
    ts = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return {"time": ts, "open": open_, "high": high, "low": low, "close": close}


def parse_twelvedata(payload, today_start, max_gap_days):
    """Gibt (kerzen, info) zurück oder wirft CandleDataError."""
    if not isinstance(payload, dict):
        raise CandleDataError("Antwort ist kein Objekt")
    if payload.get("status") != "ok":
        raise CandleDataError(f"Twelve Data meldet Fehler (Code {payload.get('code', '?')})")
    meta = payload.get("meta")
    if (
        not isinstance(meta, dict)
        or meta.get("symbol") != "XAU/USD"
        or meta.get("interval") != "1day"
        or not isinstance(payload.get("values"), list)
    ):
        raise CandleDataError("Antwort ist keine Tageskursliste für XAU/USD")

    candles, invalid_after = [], []
    for raw in reversed(payload["values"]):  # älteste zuerst
        try:
            candle = _to_candle(raw)
        except (ValueError, KeyError, TypeError):
            invalid_after.append(len(candles))
            continue
        if candles and candle["time"] <= candles[-1]["time"]:
            raise CandleDataError("Datum doppelt oder nicht absteigend geliefert")
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
        "source": "Twelve Data, XAU/USD Tageskerzen (UTC)",
        "candles_received": len(candles),
        "invalid_rows": len(invalid_after),
        "gaps_found": gaps_long,
        "dropped_before_last_gap": start,
        "first_day": datetime.fromtimestamp(series[0]["time"], timezone.utc).date().isoformat(),
        "last_day": datetime.fromtimestamp(series[-1]["time"], timezone.utc).date().isoformat(),
    }
    return series, info


def fetch_xauusd_daily(max_gap_days, now=None, session=None):
    key = (os.getenv(KEY_ENV) or "").strip()
    if not key:
        raise CandleDataError(f"{KEY_ENV} fehlt (GitHub-Secret anlegen)")
    now = now or datetime.now(timezone.utc)
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    session = session or requests.Session()
    params = {"symbol": "XAU/USD", "interval": "1day", "outputsize": MAX_CANDLES,
              "timezone": "UTC", "order": "desc"}
    headers = {"Authorization": f"apikey {key}"}
    last_error = None
    for _attempt in range(3):
        try:
            response = session.get(TWELVEDATA_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler ({type(exc).__name__})"
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                raise CandleDataError("Twelve-Data-Antwort ist kein JSON") from None
            return parse_twelvedata(payload, today_start, max_gap_days)
        last_error = f"HTTP {response.status_code}"
        if response.status_code != 429 and response.status_code < 500:
            break
    raise CandleDataError(f"Twelve-Data-Abfrage fehlgeschlagen: {last_error}")
