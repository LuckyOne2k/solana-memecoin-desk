"""Gegenprüfung von Werten aus verschiedenen Datenquellen."""


CONFIRMED = "confirmed"
UNCLEAR = "unclear"
REJECTED = "rejected"


def positive_number(value):
    """Gibt eine positive endliche Zahl zurück, sonst None."""
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    # NaN und Unendlich ausschließen
    if number != number or number in (float("inf"), float("-inf")):
        return None

    return number if number > 0 else None


def deviation_pct(first, second):
    """Relative Abweichung in Prozent, bezogen auf den größeren Wert."""
    larger = max(first, second)

    if larger <= 0:
        return 0.0

    # Auf 6 Nachkommastellen gerundet, damit Rechenrauschen bei
    # Gleitkommazahlen (z. B. 10,000000000000002 %) keinen Grenzwert
    # verschiebt.
    return round(abs(first - second) / larger * 100, 6)


def check_liquidity(
    *,
    dexpaprika_liquidity_usd,
    rugcheck_market_liquidity_usd,
    min_liquidity_usd,
    max_deviation_pct,
):
    """Vergleicht die Pool-Liquidität von DexPaprika und RugCheck.

    - Fehlt einer der Werte: unklar.
    - Weichen die Werte stärker als max_deviation_pct ab: unklar.
    - Stimmen sie überein, gilt der kleinere Wert. Liegt er unter
      min_liquidity_usd: abgelehnt.
    """
    paprika = positive_number(dexpaprika_liquidity_usd)
    rugcheck = positive_number(rugcheck_market_liquidity_usd)

    result = {
        "dexpaprika_liquidity_usd": paprika,
        "rugcheck_market_liquidity_usd": rugcheck,
        "deviation_pct": None,
        "max_deviation_pct": max_deviation_pct,
        "liquidity_used_usd": None,
    }

    if paprika is None:
        return {**result, "status": UNCLEAR, "reason": "dexpaprika_liquidity_missing"}

    if rugcheck is None:
        return {**result, "status": UNCLEAR, "reason": "rugcheck_market_liquidity_missing"}

    deviation = deviation_pct(paprika, rugcheck)
    result["deviation_pct"] = round(deviation, 2)

    if deviation > max_deviation_pct:
        return {**result, "status": UNCLEAR, "reason": "liquidity_sources_disagree"}

    used = min(paprika, rugcheck)
    result["liquidity_used_usd"] = used

    if used < min_liquidity_usd:
        return {**result, "status": REJECTED, "reason": "liquidity_below_minimum"}

    return {**result, "status": CONFIRMED, "reason": "liquidity_sources_agree"}


# Kennzeichnung der verwendeten Preisgrundlage
HIGHER_OF_TWO_SOURCES = "higher_of_two_agreeing_sources"
SINGLE_SOURCE = "single_source"


def check_price(
    *,
    primary_price_usd,
    secondary_price_usd,
    max_deviation_pct,
    primary_source="geckoterminal",
    secondary_source="rugcheck",
):
    """Vergleicht zwei Preisquellen.

    - Weichen zwei Preise stärker als max_deviation_pct ab: unklar.
      Die Toleranz gilt nur für diesen Vergleich der Preisquellen.
    - Stimmen zwei Preise überein, wird der HÖHERE verwendet
      (price_used_basis "higher_of_two_agreeing_sources"). Eine daraus
      berechnete Obergrenze ist damit auch für den anderen Preis gültig.
    - Ein einzelner gültiger Preis wird verwendet und als
      "single_source" gekennzeichnet.
    price_used_source nennt die Quelle des verwendeten Preises.
    """
    primary = positive_number(primary_price_usd)
    secondary = positive_number(secondary_price_usd)

    result = {
        f"{primary_source}_price_usd": primary,
        f"{secondary_source}_price_usd": secondary,
        "deviation_pct": None,
        "max_deviation_pct": max_deviation_pct,
        "price_used_usd": None,
        "price_used_source": None,
        "price_used_basis": None,
    }

    if primary is None and secondary is None:
        return {**result, "status": UNCLEAR, "reason": "no_price_available"}

    if primary is None or secondary is None:
        used, source = (
            (primary, primary_source)
            if primary is not None
            else (secondary, secondary_source)
        )
        return {
            **result,
            "status": CONFIRMED,
            "reason": "single_source",
            "price_used_usd": used,
            "price_used_source": source,
            "price_used_basis": SINGLE_SOURCE,
        }

    deviation = deviation_pct(primary, secondary)
    result["deviation_pct"] = deviation

    if deviation > max_deviation_pct:
        return {**result, "status": UNCLEAR, "reason": "price_sources_disagree"}

    if primary > secondary:
        source = primary_source
    elif secondary > primary:
        source = secondary_source
    else:
        source = f"{primary_source}+{secondary_source}"

    return {
        **result,
        "status": CONFIRMED,
        "reason": "price_sources_agree",
        "price_used_usd": max(primary, secondary),
        "price_used_source": source,
        "price_used_basis": HIGHER_OF_TWO_SOURCES,
    }
