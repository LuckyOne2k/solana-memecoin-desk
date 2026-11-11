"""Gold: Rücksetzer im Aufwärtstrend auf Tageskerzen (nur Backtest, kein Handel).

Zwei vorab festgelegte Regeln (Nutzerauftrag „Gold vertiefen“, 2026-11-11,
nicht an die Daten angepasst), nur Long:

- Tief: Schluss über dem ``sma_days``-Ø und tiefster Schluss der letzten
  ``low_days`` Tage → Kauf. Verkauf, sobald der Schluss über dem
  ``exit_sma_days``-Ø liegt oder nach ``max_hold_days`` Handelstagen.
- RSI: Schluss über dem ``sma_days``-Ø und RSI(``rsi_days``) unter
  ``rsi_entry`` → Kauf. Verkauf bei RSI über ``rsi_exit`` oder nach
  ``max_hold_days`` Handelstagen. RSI hier mit einfachen Durchschnitten
  (Cutler): Ø Gewinne / Ø Verluste der letzten ``rsi_days`` Veränderungen.

Ausführung wie überall: Signal aus dem Schluss, Handel zur nächsten Eröffnung,
Kosten je Kauf und Verkauf. Swap nicht enthalten.
"""

from datetime import datetime, timezone

from trend.engine import run_rule, sma, summarize
from trend.gold_intraday import simulate


def rsi(closes, i, days):
    if i < days:
        return None
    changes = [closes[k] - closes[k - 1] for k in range(i - days + 1, i + 1)]
    gain = sum(c for c in changes if c > 0) / days
    loss = -sum(c for c in changes if c < 0) / days
    if loss == 0:
        return 50.0 if gain == 0 else 100.0
    return 100 - 100 / (1 + gain / loss)


def _held(i, state):
    return i - state["entry_index"] + 1


def pullback_low(cfg):
    long_, low, exit_, hold = cfg["sma_days"], cfg["low_days"], cfg["exit_sma_days"], cfg["max_hold_days"]

    def decide(candles, i, state):
        closes = [c["close"] for c in candles[max(0, i + 1 - max(long_, low, exit_)):i + 1]]
        j = len(closes) - 1
        if state["pos"]:
            done = closes[j] > sma(closes, j, exit_) or _held(i, state) >= hold
            return 0 if done else 1
        if j + 1 < long_:
            return 0
        return 1 if closes[j] > sma(closes, j, long_) and closes[j] <= min(closes[j + 1 - low:]) else 0

    label = (f"Rücksetzer: tiefster Schluss seit {low} Tagen über {long_}-Tage-Ø, "
             f"Verkauf über {exit_}-Tage-Ø oder nach {hold} Tagen")
    return label, decide


def pullback_rsi(cfg):
    long_, n, entry, exit_, hold = (cfg["sma_days"], cfg["rsi_days"], cfg["rsi_entry"],
                                    cfg["rsi_exit"], cfg["max_hold_days"])

    def decide(candles, i, state):
        closes = [c["close"] for c in candles[max(0, i + 1 - max(long_, n + 1)):i + 1]]
        j = len(closes) - 1
        value = rsi(closes, j, n)
        if state["pos"]:
            return 0 if (value is not None and value > exit_) or _held(i, state) >= hold else 1
        if j + 1 < long_ or value is None:
            return 0
        return 1 if closes[j] > sma(closes, j, long_) and value < entry else 0

    label = (f"Rücksetzer: RSI({n}) unter {entry} über {long_}-Tage-Ø, "
             f"Verkauf bei RSI über {exit_} oder nach {hold} Tagen")
    return label, decide


def backtest_gold_pullback(candles, cfg, cost_pct, capital):
    long_ = cfg["sma_days"]
    start = max(long_, cfg["rsi_days"] + 1, cfg["low_days"], cfg["exit_sma_days"])
    if len(candles) <= start + 250:
        raise ValueError("zu wenig lückenlose Tageskerzen für den Rücksetzer-Backtest")
    results = {}
    comparisons = (
        ("buy_and_hold", "Kaufen und halten (Vergleich)", lambda closes, i: True),
        ("above_sma", f"Kurs über {long_}-Tage-Ø (Vergleich)", lambda closes, i: closes[i] > sma(closes, i, long_)),
    )
    for key, label, rule in comparisons:
        equity, trades, invested = run_rule(candles, rule, start, cost_pct, capital)
        results[key] = summarize(candles, start, equity, trades, invested, capital)
        results[key]["label"] = label
    for key, (label, decide) in (("pullback_low", pullback_low(cfg)), ("pullback_rsi", pullback_rsi(cfg))):
        equity, trades, invested = simulate(candles, decide, start, cost_pct, capital)
        results[key] = summarize(candles, start, equity, [r for _, r in trades], invested, capital)
        results[key]["label"] = label
        results[key]["avg_trade_pct"] = (
            round(100 * sum(r for _, r in trades) / len(trades), 2) if trades else None)
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).date().isoformat()
    return {"test_start": day(candles[start]["time"]), "test_end": day(candles[-1]["time"]),
            "candles_used": len(candles) - start, "cost_pct_per_trade": cost_pct, "rules": results}
