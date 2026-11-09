import copy

import pytest
import yaml

from desk.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    load_config,
    validate_config,
)


# Schnappschuss der Grenzwerte. Schlägt fehl, wenn ein Grenzwert
# unbeabsichtigt geändert wird. Eine gewollte Änderung muss hier
# bewusst nachgezogen werden.
EXPECTED_THRESHOLDS = {
    "scanner": {
        "pair_age_hours_min": 30,
        "pair_age_hours_max": 72,
        "market_cap_usd_min": 300000,
        "market_cap_usd_max": 20000000,
        "max_security_checks_per_run": 10,
        "market_cap_allow_total_supply_estimate": True,
    },
    "risk_filters": {
        "require_mint_authority_revoked": True,
        "require_lp_burned_or_locked": True,
        "reject_liquidity_below_usd": 40000,
        "reject_top10_share_above_pct": 25,
        "reject_dev_share_above_pct": 3,
        "lp_locked_min_pct": 99.99,
    },
    "data_quality": {
        "max_liquidity_deviation_pct": 15,
        "max_price_deviation_pct": 10,
    },
    "paper_trading": {
        "max_position_pct": 8,
        "initial_stop_loss_pct": 6,
        "trailing_activation_profit_pct": 12,
        "trailing_distance_pct": 6,
    },
    "debate": {
        "bear_objections_required": 3,
        "unresolved_objection_veto": True,
    },
}


def write_config(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_project_config_loads():
    config = load_config()

    assert config["mode"] == "paper"


def test_project_thresholds_are_unchanged():
    config = load_config()

    for section, values in EXPECTED_THRESHOLDS.items():
        assert config[section] == values


def test_load_config_returns_independent_copy():
    first = load_config()
    first["scanner"]["pair_age_hours_min"] = 999

    assert load_config()["scanner"]["pair_age_hours_min"] == 30


def test_config_path_can_be_set_by_environment(tmp_path, monkeypatch):
    config = load_config()
    config["scanner"]["pair_age_hours_min"] = 31
    path = write_config(tmp_path, config)

    monkeypatch.setenv("DESK_CONFIG_PATH", str(path))

    assert load_config()["scanner"]["pair_age_hours_min"] == 31


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("scanner: [unclosed", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("mode", ["live", "LIVE", None, ""])
def test_only_paper_mode_is_allowed(mode):
    config = load_config()
    config["mode"] = mode

    with pytest.raises(ConfigError):
        validate_config(config)


@pytest.mark.parametrize(
    "section, key",
    [
        (section, key)
        for section, values in EXPECTED_THRESHOLDS.items()
        for key in values
    ],
)
def test_every_threshold_is_required(section, key):
    config = load_config()
    del config[section][key]

    with pytest.raises(ConfigError):
        validate_config(config)


@pytest.mark.parametrize(
    "section, key, value",
    [
        ("scanner", "pair_age_hours_min", "30"),
        ("scanner", "pair_age_hours_min", True),
        ("scanner", "pair_age_hours_min", -1),
        ("scanner", "max_security_checks_per_run", 2.5),
        ("risk_filters", "reject_top10_share_above_pct", 101),
        ("risk_filters", "lp_locked_min_pct", None),
        ("risk_filters", "require_mint_authority_revoked", "yes"),
        ("paper_trading", "max_position_pct", 150),
        ("debate", "bear_objections_required", 2.5),
        ("data_quality", "max_liquidity_deviation_pct", 150),
        ("scanner", "market_cap_allow_total_supply_estimate", "true"),
    ],
)
def test_invalid_values_are_rejected(section, key, value):
    config = load_config()
    config[section][key] = value

    with pytest.raises(ConfigError):
        validate_config(config)


def test_inverted_ranges_are_rejected():
    config = load_config()
    config["scanner"]["pair_age_hours_min"] = 80

    with pytest.raises(ConfigError):
        validate_config(config)

    config = load_config()
    config["scanner"]["market_cap_usd_min"] = 30_000_000

    with pytest.raises(ConfigError):
        validate_config(config)


def test_debate_veto_cannot_be_disabled():
    config = load_config()
    config["debate"]["unresolved_objection_veto"] = False

    with pytest.raises(ConfigError):
        validate_config(config)


def test_missing_section_is_rejected():
    config = load_config()
    del config["risk_filters"]

    with pytest.raises(ConfigError):
        validate_config(config)


def test_default_path_points_to_repository_config():
    assert DEFAULT_CONFIG_PATH.name == "config.yaml"
    assert DEFAULT_CONFIG_PATH.is_file()


def test_config_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_config()["mode"] == "paper"


def test_validate_does_not_need_optional_sections():
    config = copy.deepcopy(load_config())
    config.pop("schedule", None)
    config.pop("timezone", None)

    validate_config(config)
