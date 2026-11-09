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

    return abs(first - second) / larger * 100


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


def check_price(*, primary_price_usd, secondary_price_usd, max_deviation_pct):
    """Vergleicht zwei Preisquellen.

    Ein einzelner gültiger Preis wird verwendet, aber als
    "single_source" gekennzeichnet. Weichen zwei Preise stärker als
    erlaubt ab, ist der Preis unklar.
    """
    primary = positive_number(primary_price_usd)
    secondary = positive_number(secondary_price_usd)

    result = {
        "primary_price_usd": primary,
        "secondary_price_usd": secondary,
        "deviation_pct": None,
        "max_deviation_pct": max_deviation_pct,
        "price_used_usd": None,
    }

    if primary is None and secondary is None:
        return {**result, "status": UNCLEAR, "reason": "no_price_available"}

    if primary is None or secondary is None:
        return {
            **result,
            "status": CONFIRMED,
            "reason": "single_source",
            "price_used_usd": primary if primary is not None else secondary,
        }

    deviation = deviation_pct(primary, secondary)
    result["deviation_pct"] = round(deviation, 2)

    if deviation > max_deviation_pct:
        return {**result, "status": UNCLEAR, "reason": "price_sources_disagree"}

    return {
        **result,
        "status": CONFIRMED,
        "reason": "price_sources_agree",
        "price_used_usd": primary,
    }
