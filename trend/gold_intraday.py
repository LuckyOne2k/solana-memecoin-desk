"""Gold intraday: Long und Short auf Stundenkerzen (nur Backtest, kein Handel).

Zwei vorab festgelegte Regeln (Nutzerauftrag 2026-11-11, nicht an die Daten
angepasst):

- Rückkehr zum Mittelwert: Schluss mindestens ``entry_z`` Standardabweichungen
  unter dem ``window_hours``-Durchschnitt → Long, darüber → Short. Ausstieg,
  sobald der Schluss den Durchschnitt wieder erreicht, nach ``max_hold_hours``
  Stunden (Uhrzeit, auch über das Wochenende) oder bei ``stop_pct`` Verlust.
- London-Ausbruch: Spanne (Hoch/Tief) der Stundenkerzen ``range_start_hour``
  bis ``range_end_hour`` UTC. Schluss über dem Hoch → Long, unter dem Tief →
  Short, höchstens ein Trade je Tag. Ausstieg bei Schluss jenseits der
  Gegenseite der Spanne oder spätestens zur Eröffnung der Kerze ``close_hour``.
  Fehlt eine Kerze der Spanne, wird an diesem Tag nicht gehandelt.

Ausführung wie im übrigen Backtest: Signal aus dem Schluss einer Kerze,
Handel zur Eröffnung der nächsten Kerze, Kosten ``cost_pct`` je Eröffnung und
je Schließung. Short-Gewinn = Kursrückgang × eingesetzter Betrag. Nicht
enthalten: Übernacht-/Wochenendgebühren (Swap), Schlupf über die Kosten hinaus.
"""

from datetime import datetime, timezone
from statistics import pstdev

from trend.data import DAY, HOUR
from trend.engine import run_rule, summarize


def simulate(candles, decide, start, cost_pct, capital):
    """Gibt (equity, trades, kerzen_investiert) zurück; trades = [(richtung, rendite)]."""
    cost = cost_pct / 100
    cash, pos = float(capital), 0
    base = entry_price = entry_time = entry_value = None
    trades, equity, invested = [], [], 0
    target = 0
    for i in range(start, len(candles)):
        candle = candles[i]
        if target != pos:
            if pos:
                cash = base * (1 + pos * (candle["open"] / entry_price - 1)) * (1 - cost)
                trades.append((pos, cash / entry_value - 1))
                pos = 0
            if target:
                entry_value, base = cash, cash * (1 - cost)
                entry_price, entry_time, pos = candle["open"], candle["time"], target
        if pos:
            invested += 1
            equity.append(base * (1 + pos * (candle["close"] / entry_price - 1)))
        else:
            equity.append(cash)
        target = decide(candles, i, {"pos": pos, "entry_price": entry_price, "entry_time": entry_time})
    if pos:  # offene Position zum letzten Schluss bewerten (inkl. Schließkosten)
        final = base * (1 + pos * (candles[-1]["close"] / entry_price - 1)) * (1 - cost)
        trades.append((pos, final / entry_value - 1))
        equity[-1] = final
    return equity, trades, invested


def mean_reversion(cfg):
    window, z_entry = cfg["window_hours"], cfg["entry_z"]
    max_hold, stop = cfg["max_hold_hours"] * HOUR, cfg["stop_pct"] / 100

    def decide(candles, i, state):
        if i + 1 < window:
            return 0
        closes = [c["close"] for c in candles[i + 1 - window:i + 1]]
        close, mean, sd = closes[-1], sum(closes) / window, pstdev(closes)
        pos = state["pos"]
        if pos:
            held = candles[i]["time"] + HOUR - state["entry_time"]
            loss = pos * (close / state["entry_price"] - 1) <= -stop
            back = close >= mean if pos == 1 else close <= mean
            return 0 if back or loss or held >= max_hold else pos
        if sd == 0:
            return 0
        z = (close - mean) / sd
        return 1 if z <= -z_entry else -1 if z >= z_entry else 0

    label = (f"Rückkehr zum {window}-h-Durchschnitt (±{z_entry} Std.-Abw., "
             f"max. {cfg['max_hold_hours']} h, Stop {cfg['stop_pct']} %)")
    return label, decide, window


def london_breakout(cfg):
    first, last, close_hour = cfg["range_start_hour"], cfg["range_end_hour"], cfg["close_hour"]
    ranges, traded = {}, set()

    def day_range(candles, i, day):
        if day not in ranges:
            hours = {}
            j = i
            while j >= 0 and candles[j]["time"] >= day:
                hour = (candles[j]["time"] - day) // HOUR
                if first <= hour < last:
                    hours[hour] = candles[j]
                j -= 1
            if len(hours) == last - first:
                ranges[day] = (max(c["high"] for c in hours.values()), min(c["low"] for c in hours.values()))
            else:
                ranges[day] = None  # Spanne unvollständig → kein Handel
        return ranges[day]

    def decide(candles, i, state):
        t = candles[i]["time"]
        day, hour = t - t % DAY, (t % DAY) // HOUR
        pos = state["pos"]
        if pos:
            entry_day = state["entry_time"] - state["entry_time"] % DAY
            if entry_day != day or hour >= close_hour - 1:
                return 0
            rng = ranges.get(day)
            if rng is None:  # Einstieg erst am Folgetag ausgeführt (Datenlücke)
                return 0
            high, low = rng
            stopped = candles[i]["close"] < low if pos == 1 else candles[i]["close"] > high
            return 0 if stopped else pos
        if hour < last or hour > close_hour - 2 or day in traded:
            return 0
        rng = day_range(candles, i, day)
        if rng is None:
            return 0
        close = candles[i]["close"]
        signal = 1 if close > rng[0] else -1 if close < rng[1] else 0
        if signal:
            traded.add(day)
        return signal

    label = (f"London-Ausbruch (Spanne {first:02d}–{last:02d} Uhr UTC, "
             f"schließen spätestens {close_hour:02d} Uhr)")
    return label, decide, ranges


def _summary(candles, start, equity, trades, invested, capital):
    result = summarize(candles, start, equity, [r for _, r in trades], invested, capital)
    weekdays = {(c["time"] - c["time"] % DAY) for c in candles[start:]
                if datetime.fromtimestamp(c["time"], timezone.utc).weekday() < 5}
    result["trades_per_day"] = round(len(trades) / len(weekdays), 2) if weekdays else None
    result["long_trades"] = sum(1 for d, _ in trades if d == 1)
    result["short_trades"] = sum(1 for d, _ in trades if d == -1)
    return result


def backtest_gold_intraday(candles, cfg, cost_pct, capital):
    """Beide Regeln plus Kaufen und Halten im selben Zeitraum."""
    mr_label, mr_decide, warmup = mean_reversion(cfg["mean_reversion"])
    lb_label, lb_decide, ranges = london_breakout(cfg["london_breakout"])
    start = warmup  # gleicher Start für alle Regeln
    if len(candles) <= start + 24 * 20:
        raise ValueError("zu wenig lückenlose Stundenkerzen für den Intraday-Backtest")
    results = {}
    equity, trades, invested = run_rule(candles, lambda closes, i: True, start, cost_pct, capital)
    results["buy_and_hold"] = summarize(candles, start, equity, trades, invested, capital)
    results["buy_and_hold"].update(label="Kaufen und halten (Vergleich)", trades_per_day=None,
                                   long_trades=len(trades), short_trades=0)
    for key, label, decide in (("mean_reversion", mr_label, mr_decide),
                               ("london_breakout", lb_label, lb_decide)):
        equity, trades, invested = simulate(candles, decide, start, cost_pct, capital)
        results[key] = _summary(candles, start, equity, trades, invested, capital)
        results[key]["label"] = label
    days = lambda t: datetime.fromtimestamp(t, timezone.utc).date().isoformat()
    return {
        "test_start": days(candles[start]["time"]),
        "test_end": days(candles[-1]["time"]),
        "candles_used": len(candles) - start,
        "cost_pct_per_trade": cost_pct,
        "days_without_full_range": sum(
            1 for day, r in ranges.items()
            if r is None and datetime.fromtimestamp(day, timezone.utc).weekday() < 5),
        "rules": results,
    }
