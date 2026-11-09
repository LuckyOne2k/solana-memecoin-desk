"""Lädt und prüft config.yaml.

config.yaml ist die einzige Quelle für alle Grenzwerte. Fehlt ein Wert
oder ist er unplausibel, bricht das Programm mit ConfigError ab, statt
mit einem stillschweigend angenommenen Standardwert weiterzulaufen.
"""

import copy
import os
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
CONFIG_PATH_ENV = "DESK_CONFIG_PATH"

# Einziger erlaubter Modus. Live-Trading ist nicht implementiert.
ALLOWED_MODE = "paper"


class ConfigError(Exception):
    pass


NUMBER = "number"
PERCENT = "percent"
COUNT = "count"
FLAG = "flag"

SCHEMA = {
    "scanner": {
        "pair_age_hours_min": NUMBER,
        "pair_age_hours_max": NUMBER,
        "market_cap_usd_min": NUMBER,
        "market_cap_usd_max": NUMBER,
        "max_security_checks_per_run": COUNT,
    },
    "risk_filters": {
        "require_mint_authority_revoked": FLAG,
        "require_lp_burned_or_locked": FLAG,
        "reject_liquidity_below_usd": NUMBER,
        "reject_top10_share_above_pct": PERCENT,
        "reject_dev_share_above_pct": PERCENT,
        "lp_locked_min_pct": PERCENT,
    },
    "data_quality": {
        "max_liquidity_deviation_pct": PERCENT,
        "max_price_deviation_pct": PERCENT,
    },
    "paper_trading": {
        "max_position_pct": PERCENT,
        "initial_stop_loss_pct": PERCENT,
        "trailing_activation_profit_pct": NUMBER,
        "trailing_distance_pct": PERCENT,
    },
    "debate": {
        "bear_objections_required": COUNT,
        "unresolved_objection_veto": FLAG,
    },
}


def _check_value(section, key, kind, value):
    name = f"{section}.{key}"

    if kind == FLAG:
        if not isinstance(value, bool):
            raise ConfigError(f"{name} muss true oder false sein")
        return

    # bool ist in Python eine Unterklasse von int und wird ausdrücklich
    # nicht als Zahl akzeptiert.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} muss eine Zahl sein")

    if value < 0:
        raise ConfigError(f"{name} darf nicht negativ sein")

    if kind == PERCENT and value > 100:
        raise ConfigError(f"{name} darf nicht über 100 liegen")

    if kind == COUNT and not isinstance(value, int):
        raise ConfigError(f"{name} muss eine ganze Zahl sein")


def validate_config(config):
    if not isinstance(config, dict):
        raise ConfigError("config.yaml muss ein YAML-Objekt enthalten")

    mode = config.get("mode")

    if mode != ALLOWED_MODE:
        raise ConfigError(
            f"mode muss '{ALLOWED_MODE}' sein (gefunden: {mode!r}). "
            "Live-Trading ist nicht freigegeben."
        )

    for section, fields in SCHEMA.items():
        values = config.get(section)

        if not isinstance(values, dict):
            raise ConfigError(f"Abschnitt '{section}' fehlt")

        for key, kind in fields.items():
            if key not in values:
                raise ConfigError(f"{section}.{key} fehlt")

            _check_value(section, key, kind, values[key])

    scanner = config["scanner"]

    # Entfernter Schalter: Eine Schätzung aus der Gesamtmenge darf die
    # Marktkapitalisierungs-Prüfung nie bestehen. Ein noch vorhandener
    # Eintrag würde eine Lockerung vortäuschen.
    if "market_cap_allow_total_supply_estimate" in scanner:
        raise ConfigError(
            "scanner.market_cap_allow_total_supply_estimate wird nicht "
            "mehr unterstützt: Eine Schätzung kann die Prüfung nie bestehen"
        )

    if scanner["pair_age_hours_min"] > scanner["pair_age_hours_max"]:
        raise ConfigError(
            "scanner.pair_age_hours_min ist größer als pair_age_hours_max"
        )

    if scanner["market_cap_usd_min"] > scanner["market_cap_usd_max"]:
        raise ConfigError(
            "scanner.market_cap_usd_min ist größer als market_cap_usd_max"
        )

    # Nur das Veto-Verhalten ist implementiert. Ein anderer Wert würde
    # stillschweigend ignoriert und ist deshalb nicht erlaubt.
    if config["debate"]["unresolved_objection_veto"] is not True:
        raise ConfigError(
            "debate.unresolved_objection_veto muss true sein"
        )

    return config


def load_config(path=None):
    path = Path(
        path
        or os.getenv(CONFIG_PATH_ENV)
        or DEFAULT_CONFIG_PATH
    )

    try:
        with path.open("r", encoding="utf-8") as file:
            config = yaml.safe_load(file)

    except OSError as error:
        raise ConfigError(
            f"Konfiguration kann nicht gelesen werden: {path}"
        ) from error

    except yaml.YAMLError as error:
        raise ConfigError(
            f"Konfiguration ist kein gültiges YAML: {path}"
        ) from error

    return copy.deepcopy(validate_config(config))
