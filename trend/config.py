"""Lädt den Abschnitt ``trend_backtest`` aus ``config.yaml`` und prüft ihn.

Fehlende oder falsch typisierte Werte stoppen das Programm, es gibt keine
stillen Standardwerte.
"""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
ALLOWED_MODE = "paper"

_NUMBER_FIELDS = {
    "cost_pct_per_trade": (0, 5),
    "start_capital_usd": (1, 10_000_000),
}
_DAY_FIELDS = ("sma_short_days", "sma_long_days", "momentum_days")


class TrendConfigError(ValueError):
    pass


def validate_trend_config(config):
    if not isinstance(config, dict):
        raise TrendConfigError("config.yaml muss ein YAML-Objekt enthalten")
    if config.get("mode") != ALLOWED_MODE:
        raise TrendConfigError("mode muss 'paper' sein, Live-Trading ist nicht freigegeben")

    section = config.get("trend_backtest")
    if not isinstance(section, dict):
        raise TrendConfigError("Abschnitt 'trend_backtest' fehlt")

    products = section.get("products")
    if (
        not isinstance(products, list)
        or not products
        or not all(isinstance(p, str) and p.endswith("-USD") for p in products)
        or len(set(products)) != len(products)
    ):
        raise TrendConfigError("trend_backtest.products muss eine Liste eindeutiger *-USD-Paare sein")

    for key, (low, high) in _NUMBER_FIELDS.items():
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrendConfigError(f"trend_backtest.{key} muss eine Zahl sein")
        if not low <= value <= high:
            raise TrendConfigError(f"trend_backtest.{key} muss zwischen {low} und {high} liegen")

    for key in _DAY_FIELDS:
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 400:
            raise TrendConfigError(f"trend_backtest.{key} muss eine ganze Zahl von 2 bis 400 sein")

    if section["sma_short_days"] >= section["sma_long_days"]:
        raise TrendConfigError("trend_backtest.sma_short_days muss kleiner als sma_long_days sein")

    return section


def load_trend_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    with path.open(encoding="utf-8") as handle:
        return validate_trend_config(yaml.safe_load(handle))


# Kraken-Paare für die Kursgegenprobe des Paper-Traders (öffentlich, ohne Key)
KRAKEN_PAIRS = {"BTC-USD": "XBTUSD", "ETH-USD": "ETHUSD", "SOL-USD": "SOLUSD"}


def validate_trend_paper_config(config):
    """Prüft ``trend_backtest`` und ``trend_paper`` und gibt beide zusammen zurück."""
    base = validate_trend_config(config)
    section = config.get("trend_paper")
    if not isinstance(section, dict):
        raise TrendConfigError("Abschnitt 'trend_paper' fehlt")

    deviation = section.get("max_price_deviation_pct")
    if isinstance(deviation, bool) or not isinstance(deviation, (int, float)) or not 0 < deviation <= 5:
        raise TrendConfigError("trend_paper.max_price_deviation_pct muss eine Zahl über 0 und höchstens 5 sein")

    minutes = section.get("min_minutes_after_midnight")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 180:
        raise TrendConfigError("trend_paper.min_minutes_after_midnight muss eine ganze Zahl von 0 bis 180 sein")

    missing = [p for p in base["products"] if p not in KRAKEN_PAIRS]
    if missing:
        raise TrendConfigError(f"keine Kraken-Gegenprobe für {', '.join(missing)}")

    merged = dict(base)
    merged.update(section)
    return merged


def load_trend_paper_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    with path.open(encoding="utf-8") as handle:
        return validate_trend_paper_config(yaml.safe_load(handle))


def validate_gold_config(config):
    """Prüft ``gold_backtest`` mit denselben Regeln wie ``trend_backtest``."""
    if not isinstance(config, dict) or config.get("mode") != ALLOWED_MODE:
        raise TrendConfigError("mode muss 'paper' sein, Live-Trading ist nicht freigegeben")
    section = config.get("gold_backtest")
    if not isinstance(section, dict):
        raise TrendConfigError("Abschnitt 'gold_backtest' fehlt")
    gap = section.get("max_gap_days")
    if isinstance(gap, bool) or not isinstance(gap, int) or not 3 <= gap <= 10:
        raise TrendConfigError("gold_backtest.max_gap_days muss eine ganze Zahl von 3 bis 10 sein")
    probe = {k: v for k, v in section.items() if k != "max_gap_days"}
    probe["products"] = ["XAU-USD"]
    try:
        validate_trend_config({"mode": ALLOWED_MODE, "trend_backtest": probe})
    except TrendConfigError as exc:
        raise TrendConfigError(str(exc).replace("trend_backtest", "gold_backtest")) from None
    return dict(section)


def load_gold_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    with path.open(encoding="utf-8") as handle:
        return validate_gold_config(yaml.safe_load(handle))
