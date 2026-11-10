import time
from datetime import datetime, timezone

import requests


RUGCHECK_URL = "https://api.rugcheck.xyz/v1"
REQUEST_INTERVAL_SECONDS = 1.1

_last_request_at = 0.0


class SecurityDataError(Exception):
    pass


def _wait_for_rate_limit():
    global _last_request_at

    elapsed = time.monotonic() - _last_request_at
    remaining = REQUEST_INTERVAL_SECONDS - elapsed

    if remaining > 0:
        time.sleep(remaining)

    _last_request_at = time.monotonic()


def _get_json(url, timeout=25):
    _wait_for_rate_limit()

    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as error:
        raise SecurityDataError(
            f"RugCheck request failed: {error}"
        ) from error

    except ValueError as error:
        raise SecurityDataError(
            "RugCheck returned invalid JSON"
        ) from error

    if not isinstance(data, dict):
        raise SecurityDataError(
            "RugCheck returned an unexpected response"
        )

    return data


def fetch_rugcheck_report(mint):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    url = f"{RUGCHECK_URL}/tokens/{mint}/report"
    report = _get_json(url)

    if report.get("mint") != mint:
        raise SecurityDataError(
            "RugCheck report does not match the requested mint"
        )

    return report


def fetch_rugcheck_summary(mint):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    url = f"{RUGCHECK_URL}/tokens/{mint}/report/summary"
    return _get_json(url)


def _number(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number < 0:
        return None

    return number


# knownAccounts-Typen, die als Börse gelten. Nur ausdrücklich
# gekennzeichnete Konten; nichts wird geraten.
EXCHANGE_LABEL_TYPES = {"EXCHANGE", "CEX"}
POOL_LABEL_TYPES = {"AMM"}
LOCKER_LABEL_TYPES = {"LOCKER"}


MAX_LOGGED_MARKETS = 20


def extract_market_lp(report):
    """LP-Angaben je Markt aus dem RugCheck-Bericht, NUR zur
    Protokollierung (keine Bewertung; die LP-Prüfung nutzt weiterhin den
    tokenweiten lpLockedPct der Zusammenfassung).

    Liste {"pool_address", "market_type", "lp_locked_pct",
    "lp_locked_usd"} in Berichtsreihenfolge, ALLE lesbaren Einträge
    (die Begrenzung auf MAX_LOGGED_MARKETS erfolgt erst beim
    Protokollieren, damit der untersuchte Pool immer auffindbar ist);
    fehlende/ungültige Zahlen -> None. None, wenn "markets" fehlt oder
    keine Liste ist.
    """
    markets = report.get("markets")

    if not isinstance(markets, list):
        return None

    result = []

    for market in markets:
        if not isinstance(market, dict):
            continue

        pubkey = market.get("pubkey")
        market_type = market.get("marketType")
        lp = market.get("lp") if isinstance(market.get("lp"), dict) else {}

        result.append(
            {
                "pool_address": pubkey if isinstance(pubkey, str) and pubkey else None,
                "market_type": (
                    market_type[:40] if isinstance(market_type, str) else None
                ),
                "lp_locked_pct": _number(lp.get("lpLockedPct")),
                "lp_locked_usd": _number(lp.get("lpLockedUSD")),
            }
        )

    return result


def extract_holder_labels(report, mint):
    """Liest Pool-, Börsen- und Locker-Kennzeichnungen aus RugCheck.

    labels_complete ist nur True, wenn "markets" und "knownAccounts"
    vorhanden und alle Einträge lesbar sind.
    """
    markets = report.get("markets")
    known = report.get("knownAccounts")

    complete = isinstance(markets, list) and isinstance(known, dict)

    pool_vaults = set()
    pool_owners = set()
    exchanges = set()
    lockers = set()
    market_liquidity = {}
    invalid_rows = 0

    for market in markets if isinstance(markets, list) else []:
        if not isinstance(market, dict):
            invalid_rows += 1
            continue

        pubkey = market.get("pubkey")

        if not isinstance(pubkey, str) or not pubkey.strip():
            invalid_rows += 1
            continue

        pool_owners.add(pubkey)

        for side in ("A", "B"):
            if market.get(f"mint{side}") == mint:
                vault = market.get(f"liquidity{side}")

                if isinstance(vault, str) and vault.strip():
                    pool_vaults.add(vault)
                else:
                    invalid_rows += 1

        lp = market.get("lp")

        if isinstance(lp, dict):
            base_usd = _number(lp.get("baseUSD"))
            quote_usd = _number(lp.get("quoteUSD"))

            if base_usd is not None and quote_usd is not None:
                market_liquidity[pubkey] = base_usd + quote_usd

    for address, info in known.items() if isinstance(known, dict) else []:
        if not isinstance(info, dict) or not isinstance(address, str):
            invalid_rows += 1
            continue

        label_type = str(info.get("type", "")).upper()

        if label_type in POOL_LABEL_TYPES:
            pool_owners.add(address)
        elif label_type in EXCHANGE_LABEL_TYPES:
            exchanges.add(address)
        elif label_type in LOCKER_LABEL_TYPES:
            lockers.add(address)

    return {
        "pool_vault_accounts": sorted(pool_vaults),
        "pool_owner_addresses": sorted(pool_owners),
        "exchange_addresses": sorted(exchanges),
        "locker_addresses": sorted(lockers),
        "invalid_label_rows": invalid_rows,
        "labels_complete": complete and invalid_rows == 0,
    }, market_liquidity


def analyze_rugcheck_report(report, summary, *, lp_locked_min_pct):
    if (
        isinstance(lp_locked_min_pct, bool)
        or not isinstance(lp_locked_min_pct, (int, float))
    ):
        raise ValueError("lp_locked_min_pct must be a number")

    if not isinstance(report, dict):
        raise ValueError("Report must be a dictionary")

    if not isinstance(summary, dict):
        raise ValueError("Summary must be a dictionary")

    token = report.get("token")
    if not isinstance(token, dict):
        token = {}

    holders = report.get("topHolders")
    if not isinstance(holders, list):
        holders = []

    creator = report.get("creator")

    owner_balances = {}
    creator_share = None
    invalid_holder_rows = 0

    for holder in holders:
        if not isinstance(holder, dict):
            invalid_holder_rows += 1
            continue

        owner = holder.get("owner")
        pct = _number(holder.get("pct"))

        if not isinstance(owner, str) or not owner.strip():
            invalid_holder_rows += 1
            continue

        if pct is None:
            invalid_holder_rows += 1
            continue

        owner_balances[owner] = (
            owner_balances.get(owner, 0.0) + pct
        )

        if creator and owner == creator:
            creator_share = (
                (creator_share or 0.0) + pct
            )

    owner_attribution_complete = (
        len(holders) > 0
        and invalid_holder_rows == 0
    )

    largest_holder_pct = None

    if owner_balances and owner_attribution_complete:
        largest_holder_pct = max(owner_balances.values())

    top10_sample_pct = None

    if (
        owner_attribution_complete
        and len(owner_balances) >= 10
    ):
        top10_sample_pct = sum(
            sorted(
                owner_balances.values(),
                reverse=True
            )[:10]
        )

    mint_revoked = (
        token.get("mintAuthority") is None
        if "mintAuthority" in token
        else None
    )

    freeze_revoked = (
        token.get("freezeAuthority") is None
        if "freezeAuthority" in token
        else None
    )

    lp_locked_pct = _number(
        summary.get("lpLockedPct")
    )

    lp_burned_or_locked = (
        lp_locked_pct >= lp_locked_min_pct
        if lp_locked_pct is not None
        else None
    )

    holder_labels, market_liquidity = extract_holder_labels(
        report,
        report.get("mint"),
    )

    supply_raw = token.get("supply")

    if not isinstance(supply_raw, int) or isinstance(supply_raw, bool):
        supply_raw = None

    return {
        "mint": report.get("mint"),
        "creator_address": creator if isinstance(creator, str) and creator else None,
        "holder_labels": holder_labels,
        "market_liquidity_usd_by_pool": market_liquidity,
        "total_market_liquidity_usd": _number(
            report.get("totalMarketLiquidity")
        ),
        "rugcheck_price_usd": _number(report.get("price")),
        "rugcheck_supply_raw": supply_raw,
        "mint_authority_revoked": mint_revoked,
        "freeze_authority_revoked": freeze_revoked,
        "lp_locked_pct": lp_locked_pct,
        "lp_burned_or_locked": lp_burned_or_locked,
        # Nur Protokoll, keine Bewertung (siehe extract_market_lp)
        "market_lp": extract_market_lp(report),
        "largest_reported_holder_pct": largest_holder_pct,
        "creator_share_sampled_pct": creator_share,
        "top10_share_sampled_pct": top10_sample_pct,
        "top_holder_accounts_returned": len(holders),
        "distinct_reported_owners": len(owner_balances),
        "invalid_holder_rows": invalid_holder_rows,
        "owner_attribution_complete": owner_attribution_complete,
        "top10_share_fully_verified": False,
        "rugcheck_score": report.get("score"),
        "rugcheck_score_normalised": report.get(
            "score_normalised"
        ),
        "rugcheck_risks": report.get("risks", []),
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def check_token_security(mint, *, lp_locked_min_pct):
    report = fetch_rugcheck_report(mint)
    summary = fetch_rugcheck_summary(mint)

    return analyze_rugcheck_report(
        report,
        summary,
        lp_locked_min_pct=lp_locked_min_pct,
    )
