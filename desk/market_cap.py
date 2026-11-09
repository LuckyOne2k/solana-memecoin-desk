"""Marktkapitalisierung aus Preis und nachvollziehbarer Tokenmenge.

Begriffe:
- Gesamtmenge (total supply): direkt von der Blockchain (getTokenSupply).
- Nachweislich verbrannt: Token auf Burn-Adressen (desk/holders.py).
- Bestätigte Umlaufmenge: nur, wenn eine Quelle sie ausdrücklich meldet.
  Derzeit ist das die von GeckoTerminal gemeldete Marktkapitalisierung
  (Basis: von CoinGecko gemeldete Umlaufmenge). Sie wird als
  "reported_circulating_supply" gekennzeichnet.
- Schätzung: Preis x (Gesamtmenge - nachweislich verbrannt). Weil die
  Umlaufmenge nie größer als diese Menge ist, ist die Schätzung eine
  OBERGRENZE der echten Marktkapitalisierung.
- FDV (Preis x Gesamtmenge) wird getrennt ausgewiesen und nie als
  Marktkapitalisierung verwendet.
"""

from desk.data_quality import (
    CONFIRMED,
    REJECTED,
    UNCLEAR,
    check_price,
    positive_number,
)


REPORTED = "reported_circulating_supply"
ESTIMATE = "estimate_total_supply"


def _supply_ui(amount_raw, decimals):
    if (
        not isinstance(amount_raw, int)
        or isinstance(amount_raw, bool)
        or amount_raw <= 0
    ):
        return None

    if (
        not isinstance(decimals, int)
        or isinstance(decimals, bool)
        or not 0 <= decimals <= 18
    ):
        return None

    return amount_raw / (10 ** decimals)


def evaluate_market_cap(
    *,
    config,
    reported_market_cap_usd=None,
    geckoterminal_price_usd=None,
    rugcheck_price_usd=None,
    total_supply_raw=None,
    supply_decimals=None,
    burned_amount_raw=0,
):
    """Bewertet die Marktkapitalisierung.

    Gibt ein Ergebnis mit status "confirmed", "unclear" oder "rejected"
    zurück. "confirmed" bedeutet: Bereichsprüfung bestanden.
    """
    scanner = config["scanner"]
    minimum = scanner["market_cap_usd_min"]
    maximum = scanner["market_cap_usd_max"]
    allow_estimate = scanner["market_cap_allow_total_supply_estimate"]
    max_price_deviation = config["data_quality"]["max_price_deviation_pct"]

    price = check_price(
        primary_price_usd=geckoterminal_price_usd,
        secondary_price_usd=rugcheck_price_usd,
        max_deviation_pct=max_price_deviation,
    )

    reported = positive_number(reported_market_cap_usd)
    total_supply = _supply_ui(total_supply_raw, supply_decimals)

    burned = burned_amount_raw if isinstance(burned_amount_raw, int) else None
    supply_after_burns = None

    if (
        total_supply is not None
        and burned is not None
        and 0 <= burned < total_supply_raw
    ):
        supply_after_burns = _supply_ui(
            total_supply_raw - burned,
            supply_decimals,
        )

    price_used = price["price_used_usd"]

    result = {
        "price": price,
        "total_supply": total_supply,
        "supply_after_provable_burns": supply_after_burns,
        "fdv_onchain_usd": (
            price_used * total_supply
            if price_used is not None and total_supply is not None
            else None
        ),
        "reported_market_cap_usd": reported,
        "estimated_market_cap_upper_bound_usd": (
            price_used * supply_after_burns
            if price_used is not None and supply_after_burns is not None
            else None
        ),
        "market_cap_usd": None,
        "basis": None,
        "is_estimate": None,
        "range_usd": [minimum, maximum],
    }

    estimate = result["estimated_market_cap_upper_bound_usd"]

    # 1. Gemeldete Umlaufmenge (bevorzugt)
    if reported is not None:
        # Plausibilität: Die Marktkapitalisierung kann nicht über dem
        # Wert aller nicht verbrannten Token liegen.
        if (
            estimate is not None
            and reported > estimate * (1 + max_price_deviation / 100)
        ):
            return {
                **result,
                "status": UNCLEAR,
                "reason": "reported_market_cap_exceeds_supply_value",
            }

        result.update(
            market_cap_usd=reported,
            basis=REPORTED,
            is_estimate=False,
        )

        if minimum <= reported <= maximum:
            return {**result, "status": CONFIRMED, "reason": "reported_in_range"}

        return {**result, "status": REJECTED, "reason": "market_cap_out_of_range"}

    # 2. Schätzung aus der Gesamtmenge
    if price["status"] != CONFIRMED:
        return {**result, "status": UNCLEAR, "reason": price["reason"]}

    if estimate is None:
        return {**result, "status": UNCLEAR, "reason": "supply_unavailable"}

    result.update(
        market_cap_usd=estimate,
        basis=ESTIMATE,
        is_estimate=True,
    )

    # Die Schätzung ist eine Obergrenze: Liegt sie unter dem Minimum,
    # liegt auch die echte Marktkapitalisierung darunter.
    if estimate < minimum:
        return {**result, "status": REJECTED, "reason": "market_cap_below_minimum"}

    # Über dem Maximum beweist eine Obergrenze nichts.
    if estimate > maximum:
        return {
            **result,
            "status": UNCLEAR,
            "reason": "estimate_above_maximum_not_proven",
        }

    if not allow_estimate:
        return {**result, "status": UNCLEAR, "reason": "estimate_not_accepted"}

    return {**result, "status": CONFIRMED, "reason": "estimate_in_range"}
