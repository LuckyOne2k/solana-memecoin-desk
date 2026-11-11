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
