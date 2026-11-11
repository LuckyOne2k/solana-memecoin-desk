"""Tageskurse XAUUSD vom OANDA-Demokonto (nur Lesen, nur Demo-Server).

Abfrage laut OANDA-v20-Doku:
``GET https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles``
mit ``granularity=D``, ``price=M`` (Mittelkurs), ``dailyAlignment=0`` und
``alignmentTimezone=UTC`` (Tage von 00:00 bis 24:00 UTC), höchstens 5000
Kerzen. Antwort: ``{"instrument": "XAU_USD", "granularity": "D",
"candles": [{"complete": bool, "time": "...Z", "mid": {"o","h","l","c"}}]}``.

Sicherheit: fest nur der Demo-Server (``api-fxpractice``), nur der
Kursendpunkt, keine Orders, kein Konto-Zugriff. Der Token kommt aus der
Umgebungsvariable ``OANDA_API_TOKEN`` und erscheint nie in Ausgaben;
Fehler nennen nur den HTTP-Status.

(Stooq lieferte am 2026-11-11 HTTP 404, LBMA HTTP 403; beide nicht genutzt.)

Datenregeln (es wird nie etwas aufgefüllt oder geschätzt):
- Jede andere Antwortform ist ein Fehler; nicht abgeschlossene Kerzen und
  der laufende Tag werden nie verwendet.
- Fehlerhafte Kerzen (keine Zahl, Preis ≤ 0, low/high passt nicht) sowie
  Lücken über ``max_gap_days`` Kalendertage beenden den brauchbaren
  Abschnitt: verwendet wird nur der Teil nach der letzten solchen Stelle.
  Wochenenden und Feiertage (Lücken bis ``max_gap_days``) sind normal.
"""

import os
from datetime import datetime, timezone

import requests

from trend.data import DAY, CandleDataError

OANDA_URL = "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
TOKEN_ENV = "OANDA_API_TOKEN"
MAX_CANDLES = 5000
TIMEOUT_SECONDS = 30


def _price(mid, key):
    raw = mid[key]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError("Preis hat falschen Typ")
    value = float(raw)  # OANDA liefert Preise als Text
    if not 0 < value < float("inf"):
        raise ValueError("Preis ungültig")
    return value


def _to_candle(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("time"), str):
        raise ValueError("kein Zeitstempel")
    mid = raw.get("mid")
    if not isinstance(mid, dict):
        raise ValueError("kein Mittelkurs")
    open_, high, low, close = (_price(mid, k) for k in ("o", "h", "l", "c"))
    if not low <= min(open_, close) <= max(open_, close) <= high:
        raise ValueError("low/high passt nicht zu open/close")
    moment = datetime.fromisoformat(raw["time"][:19]).replace(tzinfo=timezone.utc)
    if (moment.hour, moment.minute, moment.second) != (0, 0, 0):
        raise ValueError("Kerze beginnt nicht um 00:00 UTC")
    return {"time": int(moment.timestamp()), "open": open_, "high": high, "low": low, "close": close}


def parse_oanda_candles(payload, today_start, max_gap_days):
    """Gibt (kerzen, info) zurück oder wirft CandleDataError."""
    if (
        not isinstance(payload, dict)
        or payload.get("instrument") != "XAU_USD"
        or payload.get("granularity") != "D"
        or not isinstance(payload.get("candles"), list)
    ):
        raise CandleDataError("Antwort ist keine OANDA-Tageskerzenliste für XAU_USD")

    candles, invalid_after = [], []
    for raw in payload["candles"]:
        if isinstance(raw, dict) and raw.get("complete") is False:
            continue  # noch nicht abgeschlossen
        try:
            if not isinstance(raw, dict) or raw.get("complete") is not True:
                raise ValueError("Abschluss-Kennzeichen fehlt")
            candle = _to_candle(raw)
        except (ValueError, KeyError, TypeError):
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
        "source": "OANDA Demo, XAU_USD Mittelkurs, Tage in UTC",
        "candles_received": len(candles),
        "invalid_rows": len(invalid_after),
        "gaps_found": gaps_long,
        "dropped_before_last_gap": start,
        "first_day": datetime.fromtimestamp(series[0]["time"], timezone.utc).date().isoformat(),
        "last_day": datetime.fromtimestamp(series[-1]["time"], timezone.utc).date().isoformat(),
    }
    return series, info


def fetch_xauusd_daily(max_gap_days, now=None, session=None):
    token = (os.getenv(TOKEN_ENV) or "").strip()
    if not token:
        raise CandleDataError(f"{TOKEN_ENV} fehlt (GitHub-Secret anlegen)")
    now = now or datetime.now(timezone.utc)
    today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    session = session or requests.Session()
    params = {"granularity": "D", "price": "M", "count": MAX_CANDLES,
              "dailyAlignment": 0, "alignmentTimezone": "UTC"}
    headers = {"Authorization": f"Bearer {token}", "Accept-Datetime-Format": "RFC3339"}
    last_error = None
    for _attempt in range(3):
        try:
            response = session.get(OANDA_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler ({type(exc).__name__})"
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                raise CandleDataError("OANDA-Antwort ist kein JSON") from None
            return parse_oanda_candles(payload, today_start, max_gap_days)
        last_error = f"HTTP {response.status_code}"
        if response.status_code != 429 and response.status_code < 500:
            break
    raise CandleDataError(f"OANDA-Abfrage fehlgeschlagen: {last_error}")
