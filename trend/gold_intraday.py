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
  Zwei Filter-Varianten (Nutzerauftrag „Gold vertiefen“, 2026-11-11):
  nur in Trendrichtung (Vortagsschluss über/unter dem ``trend_sma_days``-Ø
  der Tageskerzen → nur Long/nur Short) und nur bei enger Spanne (schmaler
  als der Schnitt der letzten ``narrow_lookback_days`` vollständigen Spannen).

Ausführung wie im übrigen Backtest: Signal aus dem Schluss einer Kerze,
Handel zur Eröffnung der nächsten Kerze, Kosten ``cost_pct`` je Eröffnung und
je Schließung. Short-Gewinn = Kursrückgang × eingesetzter Betrag. Nicht
enthalten: Übernacht-/Wochenendgebühren (Swap), Schlupf über die Kosten hinaus.
"""

from bisect import bisect_left
from datetime import datetime, timezone
from statistics import pstdev

from trend.data import DAY, HOUR
from trend.engine import run_rule, summarize


def simulate(candles, decide, start, cost_pct, capital):
    """Gibt (equity, trades, kerzen_investiert) zurück; trades = [(richtung, rendite)]."""
    cost = cost_pct / 100
    cash, pos = float(capital), 0
    base = entry_price = entry_time = entry_value = entry_index = None
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
                entry_price, entry_time, entry_index, pos = candle["open"], candle["time"], i, target
        if pos:
            invested += 1
            equity.append(base * (1 + pos * (candle["close"] / entry_price - 1)))
        else:
            equity.append(cash)
        target = decide(candles, i, {"pos": pos, "entry_price": entry_price, "entry_time": entry_time,
                                     "entry_index": entry_index})
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


def compute_ranges(candles, first, last):
    """Spanne (Hoch, Tief) je UTC-Tag aus den Kerzen ``first`` bis ``last`` Uhr;
    None, wenn eine dieser Kerzen fehlt."""
    hours = {}
    for c in candles:
        day = c["time"] - c["time"] % DAY
        hour = (c["time"] - day) // HOUR
        bucket = hours.setdefault(day, {})
        if first <= hour < last:
            bucket[hour] = c
    return {day: (max(c["high"] for c in b.values()), min(c["low"] for c in b.values()))
            if len(b) == last - first else None for day, b in hours.items()}


def london_breakout(cfg, candles, allowed=None, label_suffix=""):
    """``allowed(day)`` gibt die erlaubten Richtungen ({1, -1}, Teilmenge oder leer) zurück."""
    first, last, close_hour = cfg["range_start_hour"], cfg["range_end_hour"], cfg["close_hour"]
    ranges = compute_ranges(candles, first, last)
    traded = set()

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
        rng = ranges.get(day)
        if rng is None:
            return 0
        close = candles[i]["close"]
        signal = 1 if close > rng[0] else -1 if close < rng[1] else 0
        if signal and allowed is not None and signal not in allowed(day):
            signal = 0
        if signal:
            traded.add(day)
        return signal

    label = (f"London-Ausbruch (Spanne {first:02d}–{last:02d} Uhr UTC, "
             f"schließen spätestens {close_hour:02d} Uhr){label_suffix}")
    return label, decide, ranges


def trend_filter(daily, sma_days, max_gap_days):
    """Erlaubte Richtung je Tag aus Tageskerzen bis einschließlich Vortag:
    Schluss über dem ``sma_days``-Ø → nur Long, darunter → nur Short.
    Zu wenig Historie oder letzte Tageskerze älter als ``max_gap_days`` → keine."""
    times = [c["time"] for c in daily]
    closes = [c["close"] for c in daily]

    def allowed(day):
        n = bisect_left(times, day)  # nur Kerzen vor diesem Tag
        if n < sma_days or day - times[n - 1] > max_gap_days * DAY:
            return set()
        sma = sum(closes[n - sma_days:n]) / sma_days
        return {1} if closes[n - 1] > sma else {-1} if closes[n - 1] < sma else set()

    return allowed


def narrow_filter(ranges, lookback):
    """Nur Tage, deren Spanne (relativ) schmaler ist als der Schnitt der
    letzten ``lookback`` vollständigen Spannen davor; sonst keine Richtung."""
    days = sorted(d for d, r in ranges.items() if r is not None)
    width = {d: (ranges[d][0] - ranges[d][1]) / ranges[d][1] for d in days}

    def allowed(day):
        if ranges.get(day) is None:
            return set()
        n = bisect_left(days, day)
        if n < lookback:
            return set()
        average = sum(width[d] for d in days[n - lookback:n]) / lookback
        return {1, -1} if width[day] < average else set()

    return allowed


def _summary(candles, start, equity, trades, invested, capital):
    result = summarize(candles, start, equity, [r for _, r in trades], invested, capital)
    weekdays = {(c["time"] - c["time"] % DAY) for c in candles[start:]
                if datetime.fromtimestamp(c["time"], timezone.utc).weekday() < 5}
    result["trades_per_day"] = round(len(trades) / len(weekdays), 2) if weekdays else None
    result["long_trades"] = sum(1 for d, _ in trades if d == 1)
    result["short_trades"] = sum(1 for d, _ in trades if d == -1)
    return result


def backtest_gold_intraday(candles, cfg, cost_pct, capital, daily=None, max_gap_days=5):
    """Beide Regeln (Ausbruch zusätzlich mit Trend- und Enge-Filter) plus
    Kaufen und Halten im selben Zeitraum. Ohne Tageskerzen fehlt die
    Trendvariante (als Fehler vermerkt, nie ersetzt)."""
    lb_cfg = cfg["london_breakout"]
    mr_label, mr_decide, warmup = mean_reversion(cfg["mean_reversion"])
    lb_label, lb_decide, ranges = london_breakout(lb_cfg, candles)
    start = warmup  # gleicher Start für alle Regeln
    if len(candles) <= start + 24 * 20:
        raise ValueError("zu wenig lückenlose Stundenkerzen für den Intraday-Backtest")
    variants = [("mean_reversion", mr_label, mr_decide), ("london_breakout", lb_label, lb_decide)]
    errors = {}
    if daily:
        allowed = trend_filter(daily, lb_cfg["trend_sma_days"], max_gap_days)
        label, decide, _ = london_breakout(
            lb_cfg, candles, allowed, f" nur in Trendrichtung ({lb_cfg['trend_sma_days']}-Tage-Ø)")
        variants.append(("london_breakout_trend", label, decide))
    else:
        errors["london_breakout_trend"] = "Tageskerzen fehlen, Trendfilter nicht berechenbar"
    label, decide, _ = london_breakout(
        lb_cfg, candles, narrow_filter(ranges, lb_cfg["narrow_lookback_days"]),
        f" nur bei enger Spanne (schmaler als Ø der letzten {lb_cfg['narrow_lookback_days']})")
    variants.append(("london_breakout_narrow", label, decide))

    results = {}
    equity, trades, invested = run_rule(candles, lambda closes, i: True, start, cost_pct, capital)
    results["buy_and_hold"] = summarize(candles, start, equity, trades, invested, capital)
    results["buy_and_hold"].update(label="Kaufen und halten (Vergleich)", trades_per_day=None,
                                   long_trades=len(trades), short_trades=0)
    for key, label, decide in variants:
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
            if r is None and day >= candles[start]["time"] - DAY
            and datetime.fromtimestamp(day, timezone.utc).weekday() < 5),
        "rules": results,
        "errors": errors,
    }
