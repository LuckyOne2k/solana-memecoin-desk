"""Backtest-Rechenwerk für Tageskerzen (nur Simulation).

Ablauf je Tag t: Das Signal wird nur aus Schlusskursen bis einschließlich
Tag t berechnet und am Eröffnungskurs von Tag t+1 ausgeführt (kein Blick
in die Zukunft). Jeder Kauf und Verkauf kostet ``cost_pct`` Prozent des
gehandelten Betrags. Alle Regeln starten am selben Tag (nach der längsten
Vorlaufzeit), damit der Vergleich fair ist.
"""

from datetime import datetime, timezone


def sma(values, end_index, days):
    if end_index + 1 < days:
        return None
    window = values[end_index + 1 - days:end_index + 1]
    return sum(window) / days


def make_rules(cfg):
    short, long_, mom = cfg["sma_short_days"], cfg["sma_long_days"], cfg["momentum_days"]

    def buy_and_hold(closes, i):
        return True

    def above_sma_long(closes, i):
        return closes[i] > sma(closes, i, long_)

    def sma_cross(closes, i):
        return sma(closes, i, short) > sma(closes, i, long_)

    def momentum(closes, i):
        return closes[i] > closes[i - mom]

    return {
        "buy_and_hold": ("Kaufen und halten (Vergleich)", buy_and_hold),
        "above_sma_long": (f"Kurs über {long_}-Tage-Durchschnitt", above_sma_long),
        "sma_cross": (f"{short}-Tage- über {long_}-Tage-Durchschnitt", sma_cross),
        "momentum": (f"Kurs höher als vor {mom} Tagen", momentum),
    }


def warmup_days(cfg):
    return max(cfg["sma_long_days"], cfg["momentum_days"] + 1)


def run_rule(candles, rule, start_index, cost_pct, capital):
    """Simuliert eine Regel ab ``start_index``.

    Gibt (equity, trades, days_invested) zurück; equity[k] ist der
    Depotwert am Schluss von Tag start_index + k.
    """
    closes = [c["close"] for c in candles]
    cost = cost_pct / 100
    cash, units = float(capital), 0.0
    entry_value = None
    trades = []
    equity = []
    days_invested = 0
    want_long = rule(closes, start_index - 1)  # Signal vom Vortag

    for i in range(start_index, len(candles)):
        open_price = candles[i]["open"]
        if want_long and units == 0:
            entry_value = cash
            units = cash * (1 - cost) / open_price
            cash = 0.0
        elif not want_long and units > 0:
            cash = units * open_price * (1 - cost)
            trades.append(cash / entry_value - 1)
            units = 0.0
        if units > 0:
            days_invested += 1
        equity.append(cash + units * closes[i])
        want_long = rule(closes, i)

    if units > 0:  # offene Position zum letzten Schlusskurs bewerten (inkl. Verkaufskosten)
        final = units * closes[-1] * (1 - cost)
        trades.append(final / entry_value - 1)
        equity[-1] = final
    return equity, trades, days_invested


def max_drawdown_pct(equity):
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def yearly_returns(candles, start_index, equity, capital):
    """Rendite je Kalenderjahr (Teiljahre am Anfang/Ende markiert)."""
    result = {}
    previous = float(capital)
    for k, value in enumerate(equity):
        year = datetime.fromtimestamp(candles[start_index + k]["time"], timezone.utc).year
        entry = result.setdefault(year, {"start": previous})
        entry["end"] = value
        is_last_of_year = (
            k + 1 == len(equity)
            or datetime.fromtimestamp(candles[start_index + k + 1]["time"], timezone.utc).year != year
        )
        if is_last_of_year:
            previous = value
    return {year: (v["end"] / v["start"] - 1) * 100 for year, v in result.items()}


def summarize(candles, start_index, equity, trades, days_invested, capital):
    days = len(equity)
    total = equity[-1] / capital - 1
    years = days / 365
    cagr = (equity[-1] / capital) ** (1 / years) - 1 if years > 0 and equity[-1] > 0 else -1.0
    return {
        "end_value_usd": round(equity[-1], 2),
        "total_return_pct": round(total * 100, 1),
        "cagr_pct": round(cagr * 100, 1),
        "max_drawdown_pct": round(max_drawdown_pct(equity), 1),
        "trades": len(trades),
        "win_rate_pct": round(100 * sum(t > 0 for t in trades) / len(trades), 1) if trades else None,
        "invested_pct": round(100 * days_invested / days, 1),
        "yearly_pct": {y: round(r, 1) for y, r in yearly_returns(candles, start_index, equity, capital).items()},
    }


def backtest_product(candles, cfg):
    """Alle Regeln für ein Handelspaar. Gibt Ergebnis-Dict und Kurven zurück."""
    start = warmup_days(cfg)
    if len(candles) <= start + 30:
        raise ValueError("zu wenig lückenlose Historie für den Backtest")
    results, curves = {}, {}
    for key, (label, rule) in make_rules(cfg).items():
        equity, trades, invested = run_rule(
            candles, rule, start, cfg["cost_pct_per_trade"], cfg["start_capital_usd"]
        )
        summary = summarize(candles, start, equity, trades, invested, cfg["start_capital_usd"])
        summary["label"] = label
        results[key] = summary
        curves[key] = {candles[start + k]["time"]: v for k, v in enumerate(equity)}
    return results, curves


def combine_portfolio(curves_by_product, cfg):
    """Gleich verteiltes Depot über den gemeinsamen Zeitraum (ohne
    Umschichtung): je Paar derselbe Anteil, jede Kurve auf ihren Wert am
    gemeinsamen Starttag normiert."""
    capital = cfg["start_capital_usd"]
    rules = next(iter(curves_by_product.values())).keys()
    out = {}
    for rule in rules:
        series = [curves_by_product[p][rule] for p in curves_by_product]
        common = sorted(set.intersection(*(set(s) for s in series)))
        if len(common) < 30:
            return None
        share = capital / len(series)
        # Vortag des gemeinsamen Starts als Basis: alle Teildepots starten in Bargeld
        equity = [
            sum(share * s[t] / s[common[0]] for s in series) for t in common
        ]
        years = len(common) / 365
        out[rule] = {
            "first_day": datetime.fromtimestamp(common[0], timezone.utc).date().isoformat(),
            "total_return_pct": round((equity[-1] / capital - 1) * 100, 1),
            "cagr_pct": round(((equity[-1] / capital) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round(max_drawdown_pct(equity), 1),
        }
    return out
