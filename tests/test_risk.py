import pytest

from desk.risk import (
    initial_stop_price,
    position_size_usd,
    stops_from_config,
    trailing_stop_price,
)


def test_position_size_is_capped_by_config(desk_config):
    stops = stops_from_config(desk_config)

    size = position_size_usd(
        portfolio_usd=1000,
        requested_usd=500,
        max_position_pct=stops["max_position_pct"],
    )

    assert size == pytest.approx(30)


def test_position_size_is_zero_for_invalid_amounts():
    assert position_size_usd(
        portfolio_usd=0,
        requested_usd=100,
        max_position_pct=8,
    ) == 0.0

    assert position_size_usd(
        portfolio_usd=1000,
        requested_usd=-5,
        max_position_pct=8,
    ) == 0.0


def test_initial_stop_uses_config(desk_config):
    stops = stops_from_config(desk_config)

    price = initial_stop_price(
        entry_price=1.0,
        stop_loss_pct=stops["stop_loss_pct"],
    )

    assert price == pytest.approx(0.94)


def test_initial_stop_rejects_invalid_entry():
    with pytest.raises(ValueError):
        initial_stop_price(entry_price=0, stop_loss_pct=6)


def test_trailing_stop_inactive_below_activation(desk_config):
    stops = stops_from_config(desk_config)

    assert trailing_stop_price(
        highest_price=1.10,
        entry_price=1.0,
        trailing_pct=stops["trailing_pct"],
        activation_gain_pct=stops["activation_gain_pct"],
    ) is None


def test_trailing_stop_active_above_activation(desk_config):
    stops = stops_from_config(desk_config)

    price = trailing_stop_price(
        highest_price=1.20,
        entry_price=1.0,
        trailing_pct=stops["trailing_pct"],
        activation_gain_pct=stops["activation_gain_pct"],
    )

    assert price == pytest.approx(1.128)
    assert price > 1.0


def test_trailing_stop_without_entry_price_is_none():
    assert trailing_stop_price(
        highest_price=1.2,
        entry_price=0,
        trailing_pct=6,
        activation_gain_pct=12,
    ) is None


@pytest.mark.parametrize("value", [None, "6", True, -1])
def test_invalid_percentages_are_rejected(value):
    with pytest.raises(ValueError):
        initial_stop_price(entry_price=1.0, stop_loss_pct=value)


def test_functions_require_explicit_parameters():
    with pytest.raises(TypeError):
        position_size_usd(portfolio_usd=1000, requested_usd=100)

    with pytest.raises(TypeError):
        initial_stop_price(entry_price=1.0)
