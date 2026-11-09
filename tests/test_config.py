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
    "holders": {
        "max_pages": 30,
        "max_slot_span": 150,
    },
    "market_cap_onchain_bounds": {
        "allow_onchain_bounds": True,
        "max_account_calls_per_token": 10,
        "max_slot_span": 150,
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


@pytest.mark.parametrize("value", [True, False])
def test_removed_estimate_switch_is_rejected(value):
    # Eine Schätzung darf nie bestehen; der Schalter darf nicht wieder
    # auftauchen und eine Lockerung vortäuschen.
    config = load_config()
    config["scanner"]["market_cap_allow_total_supply_estimate"] = value

    with pytest.raises(ConfigError):
        validate_config(config)


def test_missing_holders_section_is_rejected():
    config = load_config()
    del config["holders"]

    with pytest.raises(ConfigError, match="holders"):
        validate_config(config)


@pytest.mark.parametrize("key", ["max_pages", "max_slot_span"])
@pytest.mark.parametrize("value", [0, -1, -150, True, False, 1.5, 30.0, "30", None, [30]])
def test_invalid_holder_limits_are_rejected(key, value):
    config = load_config()
    config["holders"][key] = value

    with pytest.raises(ConfigError, match=f"holders.{key}"):
        validate_config(config)


def test_holder_limits_accept_positive_integers():
    config = load_config()
    config["holders"]["max_pages"] = 1
    config["holders"]["max_slot_span"] = 1

    validate_config(config)


def test_missing_onchain_bounds_section_is_rejected():
    config = load_config()
    del config["market_cap_onchain_bounds"]

    with pytest.raises(ConfigError, match="market_cap_onchain_bounds"):
        validate_config(config)


@pytest.mark.parametrize(
    "key", ["allow_onchain_bounds", "max_account_calls_per_token", "max_slot_span"]
)
def test_missing_onchain_bounds_key_is_rejected(key):
    config = load_config()
    del config["market_cap_onchain_bounds"][key]

    with pytest.raises(ConfigError, match=f"market_cap_onchain_bounds.{key}"):
        validate_config(config)


@pytest.mark.parametrize("value", [1, 0, "false", "true", None])
def test_onchain_bounds_switch_must_be_boolean(value):
    config = load_config()
    config["market_cap_onchain_bounds"]["allow_onchain_bounds"] = value

    with pytest.raises(ConfigError, match="allow_onchain_bounds"):
        validate_config(config)


@pytest.mark.parametrize("key", ["max_account_calls_per_token", "max_slot_span"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10", None])
def test_invalid_onchain_bounds_limits_are_rejected(key, value):
    config = load_config()
    config["market_cap_onchain_bounds"][key] = value

    with pytest.raises(ConfigError, match=f"market_cap_onchain_bounds.{key}"):
        validate_config(config)


@pytest.mark.parametrize("value", [1, 5, 10])
def test_onchain_call_limit_up_to_ten_is_valid(value):
    config = load_config()
    config["market_cap_onchain_bounds"]["max_account_calls_per_token"] = value

    validate_config(config)


@pytest.mark.parametrize("value", [11, 12, 100, 10_000])
def test_onchain_call_limit_above_ten_is_rejected(value):
    config = load_config()
    config["market_cap_onchain_bounds"]["max_account_calls_per_token"] = value

    with pytest.raises(ConfigError, match="höchstens 10"):
        validate_config(config)


def test_onchain_call_limit_above_ten_in_file_stops_program(tmp_path):
    config = load_config()
    config["market_cap_onchain_bounds"]["max_account_calls_per_token"] = 11

    with pytest.raises(ConfigError, match="max_account_calls_per_token"):
        load_config(write_config(tmp_path, config))
