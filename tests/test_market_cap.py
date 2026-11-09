import copy

import pytest

from desk.config import load_config
from desk.market_cap import ESTIMATE, REPORTED, evaluate_market_cap


CONFIG = load_config()
SUPPLY = 1_000_000_000_000_000  # 1 Mrd. Token, 6 Dezimalstellen


def evaluate(config=CONFIG, **kwargs):
    values = {
        "geckoterminal_price_usd": 0.0005,
        "rugcheck_price_usd": 0.0005,
        "total_supply_raw": SUPPLY,
        "supply_decimals": 6,
        "burned_amount_raw": 0,
    }
    values.update(kwargs)
    return evaluate_market_cap(config=config, **values)


def test_estimate_in_range_is_confirmed_and_marked():
    result = evaluate()

    assert result["status"] == "confirmed"
    assert result["basis"] == ESTIMATE
    assert result["is_estimate"] is True
    assert result["market_cap_usd"] == pytest.approx(500000)


def test_fdv_is_reported_separately():
    result = evaluate(burned_amount_raw=SUPPLY // 10)

    # FDV: gesamte Menge; Marktkapitalisierung: ohne verbrannte Token
    assert result["fdv_onchain_usd"] == pytest.approx(500000)
    assert result["market_cap_usd"] == pytest.approx(450000)
    assert result["total_supply"] == pytest.approx(1e9)
    assert result["supply_after_provable_burns"] == pytest.approx(9e8)


def test_reported_market_cap_has_priority():
    result = evaluate(reported_market_cap_usd=400000)

    assert result["basis"] == REPORTED
    assert result["is_estimate"] is False
    assert result["market_cap_usd"] == 400000
    assert result["status"] == "confirmed"


def test_reported_market_cap_above_supply_value_is_unclear():
    # Gemeldete Marktkapitalisierung größer als der Wert aller Token
    result = evaluate(reported_market_cap_usd=900000)

    assert result["status"] == "unclear"
    assert result["reason"] == "reported_market_cap_exceeds_supply_value"


def test_reported_market_cap_out_of_range_rejects():
    result = evaluate(
        reported_market_cap_usd=250000,
        geckoterminal_price_usd=None,
        rugcheck_price_usd=None,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_out_of_range"


def test_estimate_below_minimum_rejects_because_it_is_upper_bound():
    result = evaluate(geckoterminal_price_usd=0.0002, rugcheck_price_usd=0.0002)

    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_below_minimum"


def test_estimate_above_maximum_is_not_proof():
    result = evaluate(geckoterminal_price_usd=0.03, rugcheck_price_usd=0.03)

    assert result["status"] == "unclear"
    assert result["reason"] == "estimate_above_maximum_not_proven"


def test_estimate_can_be_disabled():
    config = copy.deepcopy(CONFIG)
    config["scanner"]["market_cap_allow_total_supply_estimate"] = False

    result = evaluate(config=config)

    assert result["status"] == "unclear"
    assert result["reason"] == "estimate_not_accepted"


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"geckoterminal_price_usd": None, "rugcheck_price_usd": None}, "no_price_available"),
        ({"rugcheck_price_usd": 0.0009}, "price_sources_disagree"),
        ({"total_supply_raw": None}, "supply_unavailable"),
        ({"total_supply_raw": 0}, "supply_unavailable"),
        ({"total_supply_raw": "1000"}, "supply_unavailable"),
        ({"supply_decimals": None}, "supply_unavailable"),
        ({"supply_decimals": 40}, "supply_unavailable"),
        ({"burned_amount_raw": SUPPLY}, "supply_unavailable"),
        ({"burned_amount_raw": -5}, "supply_unavailable"),
    ],
)
def test_insufficient_data_stays_unclear(kwargs, reason):
    result = evaluate(**kwargs)

    assert result["status"] == "unclear"
    assert result["reason"] == reason
    assert result["market_cap_usd"] is None


def test_single_price_source_is_enough_for_estimate():
    result = evaluate(rugcheck_price_usd=None)

    assert result["status"] == "confirmed"
    assert result["price"]["reason"] == "single_source"
