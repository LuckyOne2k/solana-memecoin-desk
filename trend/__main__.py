"""Backtest des Trend-Bots: ``python -m trend``.

Lädt Tageskerzen, rechnet alle Regeln, schreibt einen Bericht (Markdown)
auf die Konsole, in die GitHub-Zusammenfassung (falls vorhanden) und das
Ergebnis als ``trend_backtest_result.json``. Nur Simulation, kein Handel.
"""

import json
import os
import sys
from datetime import datetime, timezone

from trend.config import load_gold_config, load_trend_config
from trend.data import CandleDataError, fetch_daily_candles
from trend.gold_data import fetch_xauusd_daily
from trend.engine import backtest_product, combine_portfolio

RESULT_FILE = "trend_backtest_result.json"


def _fmt(value, suffix=" %"):
    return "–" if value is None else f"{value:+.1f}{suffix}" if suffix == " %" else f"{value}{suffix}"


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
            "| Regel | Gesamt | pro Jahr | größter Einbruch | Trades | Gewinn-Trades | investiert |",
            "|---|---|---|---|---|---|---|",
        ]
        for rule in data["rules"].values():
            lines.append(
                f"| {rule['label']} | {_fmt(rule['total_return_pct'])} | {_fmt(rule['cagr_pct'])} | "
                f"{rule['max_drawdown_pct']:.1f} % | {rule['trades']} | "
                f"{'–' if rule['win_rate_pct'] is None else str(rule['win_rate_pct']) + ' %'} | "
                f"{rule['invested_pct']} % |"
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


def run(cfg, fetch=fetch_daily_candles):
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


def run_gold(gold_cfg, fetch=fetch_xauusd_daily):
    """Backtest XAUUSD mit eigenen Kosten. Gibt den Eintrag für den Bericht zurück."""
    try:
        candles, info = fetch(gold_cfg["max_gap_days"])
        rules, curves = backtest_product(candles, gold_cfg)
    except (CandleDataError, ValueError) as exc:
        return {"error": str(exc)}
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
    result = run(cfg)
    gold_cfg = load_gold_config()
    result["gold_config"] = gold_cfg
    result["products"]["XAUUSD (Gold)"] = run_gold(gold_cfg)
    report = build_report(result)
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)
    with open(RESULT_FILE, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return 1 if any("error" in d for d in result["products"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
