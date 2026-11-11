"""Backtest des Trend-Bots: ``python -m trend``.

Lädt Tageskerzen, rechnet alle Regeln, schreibt einen Bericht (Markdown)
auf die Konsole, in die GitHub-Zusammenfassung (falls vorhanden) und das
Ergebnis als ``trend_backtest_result.json``. Nur Simulation, kein Handel.
"""

import json
import os
import sys
from datetime import datetime, timezone

from trend.config import load_gold_config, load_horizon_config, load_trend_config
from trend.data import CandleDataError, fetch_daily_candles, fetch_hourly_candles
from trend.engine import backtest_rules
from trend.gold_data import fetch_xauusd_daily, fetch_xauusd_hourly
from trend.horizons import fast_rules, medium_rules
from trend.engine import backtest_product, combine_portfolio

RESULT_FILE = "trend_backtest_result.json"
GOLD = "XAUUSD (Gold)"


def _fmt(value, suffix=" %"):
    return "–" if value is None else f"{value:+.1f}{suffix}" if suffix == " %" else f"{value}{suffix}"


def _pct_or_dash(value):
    return "–" if value is None else f"{value} %"


HORIZON_TITLES = {
    "fast": "Schnell (Stundenkerzen)",
    "medium": "Mittel (Tageskerzen)",
}


def build_horizon_report(horizons):
    lines = ["", "# Vergleich schnell / mittel / lang",
             "", "„Lang“ sind die Tabellen oben (200-Tage-Regeln). "
             "Wochen im Plus: Anteil der Kalenderwochen mit gestiegenem Depotwert."]
    for key in ("fast", "medium"):
        lines += ["", f"## {HORIZON_TITLES[key]}"]
        for product, data in horizons.get(key, {}).items():
            lines += ["", f"### {product}"]
            if "error" in data:
                lines.append(f"**Unvollständig:** {data['error']}")
                continue
            lines += [
                f"Bewertet {data['test_start']} bis {data['test_end']} ({data['candles_used']} Kerzen), "
                f"Kosten je Kauf/Verkauf {data['cost_pct_per_trade']} %.",
                "",
                "| Regel | Gesamt | pro Jahr | größter Einbruch | Trades | Gewinn-Trades | Wochen im Plus |",
                "|---|---|---|---|---|---|---|",
            ]
            for rule in data["rules"].values():
                lines.append(
                    f"| {rule['label']} | {_fmt(rule['total_return_pct'])} | {_fmt(rule['cagr_pct'])} | "
                    f"{rule['max_drawdown_pct']:.1f} % | {rule['trades']} | "
                    f"{_pct_or_dash(rule['win_rate_pct'])} | {_pct_or_dash(rule['weeks_positive_pct'])} |"
                )
    return "\n".join(lines) + "\n"


def _horizon_entry(candles, rules, warmup, cost, capital):
    results, curves = backtest_rules(candles, rules, warmup, cost, capital)
    times = sorted(next(iter(curves.values())))
    day = lambda t: datetime.fromtimestamp(t, timezone.utc).date().isoformat()
    return {"test_start": day(times[0]), "test_end": day(times[-1]), "candles_used": len(candles),
            "cost_pct_per_trade": cost, "rules": results}


def run_horizons(cfg, gold_cfg, hcfg, daily_candles, fetch_hourly=fetch_hourly_candles,
                 fetch_gold_hourly=fetch_xauusd_hourly):
    """Mittel aus den schon geladenen Tageskerzen, schnell aus Stundenkerzen."""
    products = list(cfg["products"]) + [GOLD]
    cost = {p: cfg["cost_pct_per_trade"] for p in cfg["products"]}
    cost[GOLD] = gold_cfg["cost_pct_per_trade"]
    capital = cfg["start_capital_usd"]
    out = {"medium": {}, "fast": {}}
    for product in products:
        if product not in daily_candles:
            out["medium"][product] = {"error": "Tageskerzen fehlen (siehe oben)"}
        else:
            rules, warmup = medium_rules(hcfg["medium"])  # frisch je Coin (Ausbruchsregel hat Zustand)
            try:
                out["medium"][product] = _horizon_entry(daily_candles[product], rules, warmup, cost[product], capital)
            except ValueError as exc:
                out["medium"][product] = {"error": str(exc)}
        try:
            if product == GOLD:
                candles, _info = fetch_gold_hourly(gold_cfg["max_gap_days"])
            else:
                candles, _info = fetch_hourly(product, hcfg["fast"]["history_days"])
            rules, warmup = fast_rules(hcfg["fast"])
            out["fast"][product] = _horizon_entry(candles, rules, warmup, cost[product], capital)
        except (CandleDataError, ValueError) as exc:
            out["fast"][product] = {"error": str(exc)}
    return out


def build_report(result):
    lines = [
        "# Backtest Trend-Bot (nur Simulation)",
        "",
        f"Erstellt: {result['generated_at']} · Kosten je Kauf/Verkauf: "
        f"{result['config']['cost_pct_per_trade']} % · Startkapital je Coin: "
        f"{result['config']['start_capital_usd']:,} $",
        "",
        "Vergangene Ergebnisse sind keine Garantie für die Zukunft.",
    ]
    for product, data in result["products"].items():
        lines += ["", f"## {product}"]
        if "error" in data:
            lines.append(f"**Unvollständig:** {data['error']}")
            continue
        info = data["data"]
        if "cost_pct_per_trade" in data:
            lines.append(
                f"Eigene Kosten je Kauf/Verkauf: {data['cost_pct_per_trade']} % "
                "(ohne Übernachtgebühren/Swap). Handelstage, keine Wochenenden."
            )
        lines.append(
            f"Daten {info['first_day']} bis {info['last_day']} "
            f"({info['candles_used']} Tage, Lücken: {info['gaps_found']}, "
            f"davor verworfen: {info['dropped_before_last_gap']} Tage). "
            f"Bewertet ab {data['test_start']}."
        )
        lines += [
            "",
            "| Regel | Gesamt | pro Jahr | größter Einbruch | Trades | Gewinn-Trades | investiert | Wochen im Plus |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for rule in data["rules"].values():
            lines.append(
                f"| {rule['label']} | {_fmt(rule['total_return_pct'])} | {_fmt(rule['cagr_pct'])} | "
                f"{rule['max_drawdown_pct']:.1f} % | {rule['trades']} | "
                f"{'–' if rule['win_rate_pct'] is None else str(rule['win_rate_pct']) + ' %'} | "
                f"{rule['invested_pct']} % | {_pct_or_dash(rule.get('weeks_positive_pct'))} |"
            )
        years = sorted({y for r in data["rules"].values() for y in r["yearly_pct"]})
        lines += ["", "| Jahr | " + " | ".join(r["label"] for r in data["rules"].values()) + " |",
                  "|---" * (len(data["rules"]) + 1) + "|"]
        for year in years:
            lines.append(
                f"| {year} | "
                + " | ".join(_fmt(r["yearly_pct"].get(year)) for r in data["rules"].values())
                + " |"
            )
    portfolio = result.get("portfolio")
    if portfolio:
        first = next(iter(portfolio.values()))["first_day"]
        lines += [
            "",
            f"## Depot gleich verteilt auf alle Coins (ab {first}, ohne Umschichtung)",
            "",
            "| Regel | Gesamt | pro Jahr | größter Einbruch |",
            "|---|---|---|---|",
        ]
        labels = next(d for d in result["products"].values() if "rules" in d)["rules"]
        for key, row in portfolio.items():
            lines.append(
                f"| {labels[key]['label']} | {_fmt(row['total_return_pct'])} | "
                f"{_fmt(row['cagr_pct'])} | {row['max_drawdown_pct']:.1f} % |"
            )
    return "\n".join(lines) + "\n"


def run(cfg, fetch=fetch_daily_candles, candles_out=None):
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "paper",
        "config": cfg,
        "products": {},
    }
    curves = {}
    for product in cfg["products"]:
        try:
            candles, info = fetch(product)
            rules, product_curves = backtest_product(candles, cfg)
        except (CandleDataError, ValueError) as exc:
            result["products"][product] = {"error": str(exc)}
            continue
        if candles_out is not None:
            candles_out[product] = candles
        info["candles_used"] = len(candles)
        start = len(candles) - len(next(iter(product_curves.values())))
        result["products"][product] = {
            "data": info,
            "test_start": datetime.fromtimestamp(candles[start]["time"], timezone.utc).date().isoformat(),
            "rules": rules,
        }
        curves[product] = product_curves
    # Gesamtdepot nur, wenn alle Paare vollständig sind (sonst irreführend)
    # (nur Krypto; Gold wird getrennt bewertet)
    if len(curves) == len(cfg["products"]) and len(curves) > 1:
        result["portfolio"] = combine_portfolio(curves, cfg)
    return result


def run_gold(gold_cfg, fetch=fetch_xauusd_daily, candles_out=None):
    """Backtest XAUUSD mit eigenen Kosten. Gibt den Eintrag für den Bericht zurück."""
    try:
        candles, info = fetch(gold_cfg["max_gap_days"])
        rules, curves = backtest_product(candles, gold_cfg)
    except (CandleDataError, ValueError) as exc:
        return {"error": str(exc)}
    if candles_out is not None:
        candles_out[GOLD] = candles
    info["candles_used"] = len(candles)
    start = len(candles) - len(next(iter(curves.values())))
    return {
        "data": info,
        "cost_pct_per_trade": gold_cfg["cost_pct_per_trade"],
        "test_start": datetime.fromtimestamp(candles[start]["time"], timezone.utc).date().isoformat(),
        "rules": rules,
    }


def main():
    cfg = load_trend_config()
    gold_cfg = load_gold_config()
    hcfg = load_horizon_config()
    daily = {}
    result = run(cfg, candles_out=daily)
    result["gold_config"] = gold_cfg
    result["products"][GOLD] = run_gold(gold_cfg, candles_out=daily)
    result["horizon_config"] = hcfg
    result["horizons"] = run_horizons(cfg, gold_cfg, hcfg, daily)
    report = build_report(result) + build_horizon_report(result["horizons"])
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)
    with open(RESULT_FILE, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    failed = any("error" in d for d in result["products"].values()) or any(
        "error" in d for group in result["horizons"].values() for d in group.values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
