import pytest

from desk.data_quality import (
    check_liquidity,
    check_price,
    deviation_pct,
    positive_number,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, 1.0),
        ("2.5", 2.5),
        (0, None),
        (-3, None),
        (None, None),
        (True, None),
        ("abc", None),
        (float("nan"), None),
        (float("inf"), None),
        ([], None),
    ],
)
def test_positive_number(value, expected):
    assert positive_number(value) == expected


def test_deviation_is_relative_to_larger_value():
    assert deviation_pct(100, 85) == pytest.approx(15)
    assert deviation_pct(85, 100) == pytest.approx(15)
    assert deviation_pct(0, 0) == 0


def liquidity(paprika, rugcheck, minimum=40000, tolerance=15):
    return check_liquidity(
        dexpaprika_liquidity_usd=paprika,
        rugcheck_market_liquidity_usd=rugcheck,
        min_liquidity_usd=minimum,
        max_deviation_pct=tolerance,
    )


def test_matching_liquidity_is_confirmed_with_lower_value():
    # Echter Fall XTO: 303.472 $ gegen 303.649 $
    result = liquidity(303472, 303649)

    assert result["status"] == "confirmed"
    assert result["liquidity_used_usd"] == 303472


def test_stale_liquidity_is_unclear():
    # Echter Fall HABIBI: 70.676 $ gegen 1.921 $
    result = liquidity(70676, 1921)

    assert result["status"] == "unclear"
    assert result["reason"] == "liquidity_sources_disagree"
    assert result["deviation_pct"] > 90


def test_deviation_exactly_at_limit_is_accepted():
    assert liquidity(100000, 85000)["status"] == "confirmed"
    assert liquidity(100000, 84000)["status"] == "unclear"


@pytest.mark.parametrize(
    "paprika, rugcheck, reason",
    [
        (None, 50000, "dexpaprika_liquidity_missing"),
        (50000, None, "rugcheck_market_liquidity_missing"),
        (50000, 0, "rugcheck_market_liquidity_missing"),
        ("abc", 50000, "dexpaprika_liquidity_missing"),
    ],
)
def test_missing_or_invalid_liquidity_is_unclear(paprika, rugcheck, reason):
    result = liquidity(paprika, rugcheck)

    assert result["status"] == "unclear"
    assert result["reason"] == reason


def test_lower_value_below_minimum_rejects():
    result = liquidity(42000, 39000)

    assert result["status"] == "rejected"


def price(primary, secondary, tolerance=10):
    return check_price(
        primary_price_usd=primary,
        secondary_price_usd=secondary,
        max_deviation_pct=tolerance,
    )


def test_agreeing_prices_are_confirmed():
    result = price(0.0005, 0.00051)

    assert result["status"] == "confirmed"
    assert result["price_used_usd"] == 0.0005


def test_single_price_is_used_but_marked():
    result = price(None, 0.0005)

    assert result["status"] == "confirmed"
    assert result["reason"] == "single_source"
    assert result["price_used_usd"] == 0.0005


def test_conflicting_prices_are_unclear():
    # Echter Fall HABIBI: DexPaprika-Poolpreis 7,4e-5 gegen 1,78e-6
    assert price(7.38e-5, 1.78e-6)["status"] == "unclear"


def test_no_price_is_unclear():
    assert price(None, "x")["reason"] == "no_price_available"
