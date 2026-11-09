"""Marktkapitalisierung aus Preis und nachvollziehbarer Tokenmenge.

Begriffe:
- Gesamtmenge (total supply): direkt von der Blockchain (getTokenSupply).
- Nachweislich verbrannt: Token auf Burn-Adressen (desk/holders.py).
- Gemeldete Umlaufmenge: Die von GeckoTerminal gemeldete
  Marktkapitalisierung (Basis: von CoinGecko gemeldete Umlaufmenge),
  gekennzeichnet als "reported_circulating_supply".
- Obergrenze: Preis x (Gesamtmenge - nachweislich verbrannt). Die
  Umlaufmenge ist nie größer als diese Menge, deshalb ist der Wert eine
  OBERGRENZE der echten Marktkapitalisierung. Bei zwei übereinstimmenden
  Preisquellen wird der HÖHERE Preis verwendet (siehe
  desk.data_quality.check_price); die Grundlage steht im Protokoll
  (price_used_usd, price_used_source, price_used_basis).
- FDV (Preis x Gesamtmenge) wird getrennt ausgewiesen und nie als
  Marktkapitalisierung verwendet.

Entscheidungsregeln:
- Eine Obergrenze kann nur eine Ablehnung begründen (Obergrenze unter
  dem Minimum). Sie führt NIE zum Bestehen der Prüfung.
- Bestehen ist nur mit gemeldeter Umlaufmenge möglich, und nur wenn
  * zwei unabhängige Preisquellen (GeckoTerminal, RugCheck) vorliegen
    und übereinstimmen,
  * die Gesamtmenge von der Blockchain vorliegt und
  * der gemeldete Wert nicht über der Obergrenze liegt (ohne
    zusätzliche Toleranz; die Preistoleranz gilt nur für den Vergleich
    der beiden Preisquellen).
- Widersprüchliche Preise machen die Prüfung immer unklar, auch wenn
  ein gemeldeter Wert vorliegt.
"""

from desk.data_quality import (
    CONFIRMED,
    REJECTED,
    UNCLEAR,
    check_price,
    positive_number,
)


REPORTED = "reported_circulating_supply"
UPPER_BOUND = "upper_bound_total_supply"

# Grund im Protokoll, wenn zwei übereinstimmende Preisquellen vorliegen
TWO_SOURCES_AGREE = "price_sources_agree"


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

    status:
    - "confirmed": Bereichsprüfung mit gemeldeter Umlaufmenge bestanden
    - "rejected": nachweislich außerhalb des Bereichs
    - "unclear": keine sichere Aussage möglich
    """
    scanner = config["scanner"]
    minimum = scanner["market_cap_usd_min"]
    maximum = scanner["market_cap_usd_max"]
    max_price_deviation = config["data_quality"]["max_price_deviation_pct"]

    price = check_price(
        primary_price_usd=geckoterminal_price_usd,
        secondary_price_usd=rugcheck_price_usd,
        max_deviation_pct=max_price_deviation,
    )

    reported = positive_number(reported_market_cap_usd)
    total_supply = _supply_ui(total_supply_raw, supply_decimals)

    burned = (
        burned_amount_raw
        if isinstance(burned_amount_raw, int)
        and not isinstance(burned_amount_raw, bool)
        else None
    )
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

    price_used = (
        price["price_used_usd"]
        if price["status"] == CONFIRMED
        else None
    )

    upper_bound = (
        price_used * supply_after_burns
        if price_used is not None and supply_after_burns is not None
        else None
    )

    result = {
        "price": price,
        # Preisgrundlage für Obergrenze und FDV, eindeutig ausgewiesen
        "price_used_usd": price_used,
        "price_used_source": price["price_used_source"] if price_used else None,
        "price_used_basis": price["price_used_basis"] if price_used else None,
        "total_supply": total_supply,
        "supply_after_provable_burns": supply_after_burns,
        # Getrennt ausgewiesen, nie als Marktkapitalisierung verwendet
        "fdv_onchain_usd": (
            price_used * total_supply
            if price_used is not None and total_supply is not None
            else None
        ),
        # Für die Risikoeinschätzung: höchstens so groß kann die
        # Marktkapitalisierung sein.
        "market_cap_upper_bound_usd": upper_bound,
        "reported_market_cap_usd": reported,
        # Nur gesetzt, wenn ein gemeldeter Wert die Prüfung trägt
        "market_cap_usd": None,
        "basis": None,
        "range_usd": [minimum, maximum],
    }

    # 1. Widersprüchliche oder fehlende Preise: nie bestehen, nie
    #    ablehnen. Gilt auch, wenn ein gemeldeter Wert vorliegt.
    if price["status"] != CONFIRMED:
        return {**result, "status": UNCLEAR, "reason": price["reason"]}

    # 2. Obergrenze unter dem Minimum: nachweislich zu klein.
    if upper_bound is not None and upper_bound < minimum:
        return {
            **result,
            "basis": UPPER_BOUND,
            "status": REJECTED,
            "reason": "market_cap_below_minimum",
        }

    # 3. Ohne gemeldete Umlaufmenge keine Bestätigung.
    if reported is None:
        return {
            **result,
            "basis": UPPER_BOUND if upper_bound is not None else None,
            "status": UNCLEAR,
            "reason": (
                "circulating_supply_not_confirmed"
                if upper_bound is not None
                else "supply_unavailable"
            ),
        }

    # 4. Gemeldeter Wert: nur mit zwei übereinstimmenden Preisquellen
    #    und Plausibilitätsprüfung gegen die Obergrenze.
    if price["reason"] != TWO_SOURCES_AGREE:
        return {
            **result,
            "status": UNCLEAR,
            "reason": "reported_market_cap_needs_two_price_sources",
        }

    if upper_bound is None:
        return {
            **result,
            "status": UNCLEAR,
            "reason": "reported_market_cap_not_verifiable_without_supply",
        }

    if reported > upper_bound:
        return {
            **result,
            "status": UNCLEAR,
            "reason": "reported_market_cap_exceeds_supply_value",
        }

    result.update(market_cap_usd=reported, basis=REPORTED)

    if minimum <= reported <= maximum:
        return {**result, "status": CONFIRMED, "reason": "reported_in_range"}

    return {**result, "status": REJECTED, "reason": "market_cap_out_of_range"}
