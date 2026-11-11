"""Drei Handelsgeschwindigkeiten im Vergleich (nur Backtest, kein Handel).

- schnell: Stundenkerzen der letzten ``fast.history_days`` Tage
- mittel:  Tageskerzen (ganze Historie)
- lang:    Tageskerzen, Regeln aus ``trend_backtest`` (im Hauptbericht)

Alle Regeln vorab festgelegt (Nutzerauftrag 2026-11-11), nicht an die Daten
angepasst. Jede Gruppe wird gegen Kaufen und Halten im selben Zeitraum
verglichen. Ausführung wie im Rechenwerk: Signal aus dem Schluss der Kerze,
Handel zur Eröffnung der nächsten Kerze, Kosten je Kauf und Verkauf.
"""

from trend.engine import sma


def _buy_and_hold(closes, i):
    return True


def fast_rules(cfg):
    avg, short, long_ = cfg["sma_hours"], cfg["cross_short_hours"], cfg["cross_long_hours"]

    def above_avg(closes, i):
        return closes[i] > sma(closes, i, avg)

    def cross(closes, i):
        return sma(closes, i, short) > sma(closes, i, long_)

    rules = {
        "buy_and_hold": ("Kaufen und halten (Vergleich)", _buy_and_hold),
        "above_sma": (f"Kurs über {avg}-Stunden-Durchschnitt", above_avg),
        "sma_cross": (f"{short}- über {long_}-Stunden-Durchschnitt", cross),
    }
    return rules, max(avg, long_)


def medium_rules(cfg):
    entry, exit_ = cfg["breakout_entry_days"], cfg["breakout_exit_days"]
    short, long_ = cfg["cross_short_days"], cfg["cross_long_days"]

    def make_breakout():
        # Zustand: investiert bleibt investiert, bis das Ausstiegs-Tief bricht.
        state = {"long": False, "last": None}

        def breakout(closes, i):
            if state["last"] is not None and i != state["last"] + 1:
                raise ValueError("Ausbruchsregel muss Tag für Tag aufgerufen werden")
            state["last"] = i
            if closes[i] > max(closes[i - entry:i]):
                state["long"] = True
            elif closes[i] < min(closes[i - exit_:i]):
                state["long"] = False
            return state["long"]

        return breakout

    def cross(closes, i):
        return sma(closes, i, short) > sma(closes, i, long_)

    rules = {
        "buy_and_hold": ("Kaufen und halten (Vergleich)", _buy_and_hold),
        "breakout": (f"Ausbruch: Kauf bei neuem {entry}-Tage-Hoch, Verkauf bei {exit_}-Tage-Tief",
                     make_breakout()),
        "sma_cross": (f"{short}- über {long_}-Tage-Durchschnitt", cross),
    }
    return rules, max(entry, exit_, long_) + 1
