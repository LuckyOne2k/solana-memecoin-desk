import pytest

from desk.config import load_config
from desk.market_cap import REPORTED, UPPER_BOUND, evaluate_market_cap


CONFIG = load_config()
SUPPLY = 1_000_000_000_000_000  # 1 Mrd. Token, 6 Dezimalstellen


def evaluate(**kwargs):
    values = {
        "geckoterminal_price_usd": 0.0005,
        "rugcheck_price_usd": 0.0005,
        "total_supply_raw": SUPPLY,
        "supply_decimals": 6,
        "burned_amount_raw": 0,
    }
    values.update(kwargs)
    return evaluate_market_cap(config=CONFIG, **values)


# --------------------------------------------------
# Obergrenze aus der Gesamtmenge: nie Bestehen
# --------------------------------------------------

def test_upper_bound_in_range_never_confirms():
    # Obergrenze 500.000 $ im Zielbereich, aber keine Umlaufmenge
    result = evaluate()

    assert result["status"] == "unclear"
    assert result["reason"] == "circulating_supply_not_confirmed"
    assert result["market_cap_usd"] is None
    assert result["market_cap_upper_bound_usd"] == pytest.approx(500000)
    assert result["basis"] == UPPER_BOUND


def test_upper_bound_exactly_at_minimum_stays_unclear():
    # 0,0003 $ x 1 Mrd. = 300.000 $ -> nicht unter dem Minimum
    result = evaluate(geckoterminal_price_usd=0.0003, rugcheck_price_usd=0.0003)

    assert result["status"] == "unclear"


def test_upper_bound_below_minimum_rejects():
    result = evaluate(geckoterminal_price_usd=0.000299, rugcheck_price_usd=0.000299)

    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_below_minimum"


def test_upper_bound_above_maximum_stays_unclear():
    result = evaluate(geckoterminal_price_usd=0.03, rugcheck_price_usd=0.03)

    assert result["status"] == "unclear"
    assert result["reason"] == "circulating_supply_not_confirmed"


def test_upper_bound_with_single_price_can_reject():
    # Ablehnung ist auch mit nur einer Preisquelle zulässig
    result = evaluate(geckoterminal_price_usd=0.0001, rugcheck_price_usd=None)

    assert result["status"] == "rejected"


def test_fdv_and_upper_bound_are_reported_separately():
    result = evaluate(burned_amount_raw=SUPPLY // 10)

    assert result["fdv_onchain_usd"] == pytest.approx(500000)
    assert result["market_cap_upper_bound_usd"] == pytest.approx(450000)
    assert result["total_supply"] == pytest.approx(1e9)
    assert result["supply_after_provable_burns"] == pytest.approx(9e8)


# --------------------------------------------------
# Gemeldete Umlaufmenge
# --------------------------------------------------

def test_reported_market_cap_with_agreeing_prices_confirms():
    result = evaluate(reported_market_cap_usd=400000)

    assert result["status"] == "confirmed"
    assert result["basis"] == REPORTED
    assert result["market_cap_usd"] == 400000


def test_reported_market_cap_out_of_range_rejects():
    result = evaluate(
        reported_market_cap_usd=250000,
        geckoterminal_price_usd=0.0003,
        rugcheck_price_usd=0.0003,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_out_of_range"


def test_reported_market_cap_above_supply_value_is_unclear():
    result = evaluate(reported_market_cap_usd=900000)

    assert result["status"] == "unclear"
    assert result["reason"] == "reported_market_cap_exceeds_supply_value"


# --------------------------------------------------
# Preisabweichung: kann nicht durch gemeldeten Wert umgangen werden
# --------------------------------------------------

def test_conflicting_prices_block_reported_market_cap():
    result = evaluate(reported_market_cap_usd=400000, rugcheck_price_usd=0.0009)

    assert result["status"] == "unclear"
    assert result["reason"] == "price_sources_disagree"
    assert result["market_cap_usd"] is None


def test_conflicting_prices_block_rejection_too():
    # Auch eine Ablehnung braucht einen belastbaren Preis
    result = evaluate(geckoterminal_price_usd=0.0001, rugcheck_price_usd=0.0005)

    assert result["status"] == "unclear"
    assert result["reason"] == "price_sources_disagree"


def test_price_deviation_exactly_at_limit_is_accepted():
    # 10 % Abweichung, bezogen auf den größeren Wert
    result = evaluate(
        reported_market_cap_usd=400000,
        geckoterminal_price_usd=0.0005,
        rugcheck_price_usd=0.00045,
    )

    assert result["price"]["deviation_pct"] == pytest.approx(10)
    assert result["status"] == "confirmed"


def test_price_deviation_just_above_limit_is_unclear():
    result = evaluate(
        reported_market_cap_usd=400000,
        geckoterminal_price_usd=0.0005,
        rugcheck_price_usd=0.000449,
    )

    assert result["status"] == "unclear"


def test_reported_market_cap_needs_two_price_sources():
    result = evaluate(reported_market_cap_usd=400000, rugcheck_price_usd=None)

    assert result["status"] == "unclear"
    assert result["reason"] == "reported_market_cap_needs_two_price_sources"


def test_reported_market_cap_without_supply_is_not_verifiable():
    result = evaluate(reported_market_cap_usd=400000, total_supply_raw=None)

    assert result["status"] == "unclear"
    assert result["reason"] == "reported_market_cap_not_verifiable_without_supply"


# --------------------------------------------------
# Fehlende und fehlerhafte Daten
# --------------------------------------------------

@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"geckoterminal_price_usd": None, "rugcheck_price_usd": None}, "no_price_available"),
        ({"geckoterminal_price_usd": "abc", "rugcheck_price_usd": -1}, "no_price_available"),
        ({"total_supply_raw": None}, "supply_unavailable"),
        ({"total_supply_raw": 0}, "supply_unavailable"),
        ({"total_supply_raw": "1000"}, "supply_unavailable"),
        ({"supply_decimals": None}, "supply_unavailable"),
        ({"supply_decimals": 40}, "supply_unavailable"),
        ({"burned_amount_raw": SUPPLY}, "supply_unavailable"),
        ({"burned_amount_raw": -5}, "supply_unavailable"),
        ({"burned_amount_raw": True}, "supply_unavailable"),
    ],
)
def test_insufficient_data_stays_unclear(kwargs, reason):
    result = evaluate(**kwargs)

    assert result["status"] == "unclear"
    assert result["reason"] == reason
    assert result["market_cap_usd"] is None


# --------------------------------------------------
# Preisgrundlage der Obergrenze: höherer von zwei Preisen
# --------------------------------------------------

@pytest.mark.parametrize(
    "gecko, rugcheck, source",
    [
        (0.00055, 0.0005, "geckoterminal"),  # GeckoTerminal höher
        (0.0005, 0.00055, "rugcheck"),       # RugCheck höher
    ],
)
def test_upper_bound_uses_higher_of_two_prices(gecko, rugcheck, source):
    result = evaluate(geckoterminal_price_usd=gecko, rugcheck_price_usd=rugcheck)

    # 0,00055 $ x 1 Mrd. Token = 550.000 $, unabhängig von der Reihenfolge
    assert result["market_cap_upper_bound_usd"] == pytest.approx(550000)
    assert result["fdv_onchain_usd"] == pytest.approx(550000)
    assert result["price_used_usd"] == 0.00055
    assert result["price_used_source"] == source
    assert result["price_used_basis"] == "higher_of_two_agreeing_sources"


@pytest.mark.parametrize(
    "gecko, rugcheck",
    [(0.00055, 0.0005), (0.0005, 0.00055)],
)
def test_reported_market_cap_checked_against_higher_price_bound(gecko, rugcheck):
    # 520.000 $ liegt über 0,0005 x 1 Mrd., aber unter 0,00055 x 1 Mrd.
    result = evaluate(
        reported_market_cap_usd=520000,
        geckoterminal_price_usd=gecko,
        rugcheck_price_usd=rugcheck,
    )

    assert result["status"] == "confirmed"
    assert result["market_cap_usd"] == 520000


def test_reported_market_cap_just_above_upper_bound_is_unclear():
    # Obergrenze 500.000 $; früher galt hier noch 10 % Toleranz
    result = evaluate(reported_market_cap_usd=500000.01)

    assert result["market_cap_upper_bound_usd"] == pytest.approx(500000)
    assert result["status"] == "unclear"
    assert result["reason"] == "reported_market_cap_exceeds_supply_value"


def test_reported_market_cap_inside_old_tolerance_is_now_unclear():
    # 540.000 $ wäre mit der entfernten 10-%-Toleranz noch akzeptiert worden
    result = evaluate(reported_market_cap_usd=540000)

    assert result["status"] == "unclear"


def test_reported_market_cap_exactly_at_upper_bound_is_allowed():
    result = evaluate(
        reported_market_cap_usd=500000,
        geckoterminal_price_usd=0.0005,
        rugcheck_price_usd=0.0005,
        total_supply_raw=SUPPLY,
    )

    assert result["status"] == "confirmed"


def test_single_price_basis_is_logged_for_rejection():
    result = evaluate(geckoterminal_price_usd=None, rugcheck_price_usd=0.0001)

    assert result["status"] == "rejected"
    assert result["price_used_source"] == "rugcheck"
    assert result["price_used_basis"] == "single_source"


def test_conflicting_prices_leave_no_price_basis():
    result = evaluate(reported_market_cap_usd=400000, rugcheck_price_usd=0.0009)

    assert result["price_used_usd"] is None
    assert result["price_used_basis"] is None
    assert result["market_cap_upper_bound_usd"] is None
