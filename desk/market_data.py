
import json
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.geckoterminal.com/api/v2"
DEXPAPRIKA_URL = "https://api.dexpaprika.com"

QUOTE_TOKEN_IDS = {
    "So11111111111111111111111111111111111111112".lower(),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".lower(),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),
}


class MarketDataError(Exception):
    pass


def fetch_new_pools(page=1, timeout=15):
    if page < 1:
        raise ValueError("Page must be at least 1")

    try:
        response = requests.get(
            f"{BASE_URL}/networks/solana/new_pools",
            params={
                "page": page,
                "include": "base_token,quote_token",
            },
            headers={"Accept": "application/json;version=20230203"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()

    except requests.RequestException as error:
        raise MarketDataError(
            f"GeckoTerminal request failed: {error}"
        ) from error

    except ValueError as error:
        raise MarketDataError(
            "Invalid GeckoTerminal JSON response"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MarketDataError("Invalid GeckoTerminal response structure")

    return payload


def fetch_pools_by_age(timeout=20, max_pages=10):
    api_key = os.getenv("DEXPAPRIKA_API_KEY")
    cursor = None
    all_pools = []
    seen_ids = set()
    seen_cursors = set()
    pages_fetched = 0
    duplicates = 0
    has_next_page = False

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    for page in range(max_pages):
        params = {
            "order_by": "created_at",
            "sort": "desc",
            "created_after": "-72h",
            "created_before": "-30h",
            "liquidity_usd_min": 40000,
            "limit": 100,
            "detailed": "true",
        }

        if cursor:
            if cursor in seen_cursors:
                raise MarketDataError(
                    "DexPaprika pagination cursor repeated"
                )

            seen_cursors.add(cursor)
            params["cursor"] = cursor

        headers = {}

        if api_key:
            headers["Authorization"] = api_key

        if page > 0:
            time.sleep(1)

        try:
            response = requests.get(
                f"{DEXPAPRIKA_URL}/networks/solana/pools/search",
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()

        except requests.RequestException as error:
            raise MarketDataError(
                f"DexPaprika request failed: {error}"
            ) from error

        except ValueError as error:
            raise MarketDataError(
                "Invalid DexPaprika JSON response"
            ) from error

        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("results"), list)
        ):
            raise MarketDataError(
                "DexPaprika returned no valid results list"
            )

        pages_fetched += 1
        has_next_page = bool(payload.get("has_next_page"))

        for pool in payload["results"]:
            if not isinstance(pool, dict):
                continue

            pool_id = pool.get("id")

            if pool_id and pool_id in seen_ids:
                duplicates += 1
                continue

            if pool_id:
                seen_ids.add(pool_id)

            all_pools.append(pool)

        if not has_next_page:
            cursor = None
            break

        next_cursor = payload.get("next_cursor")

        if not next_cursor:
            raise MarketDataError(
                "DexPaprika reports more pages but provides no cursor"
            )

        if next_cursor in seen_cursors:
            raise MarketDataError(
                "DexPaprika returned a repeated pagination cursor"
            )

        cursor = next_cursor

    return {
        "results": all_pools,
        "has_next_page": has_next_page,
        "next_cursor": cursor,
        "pages_fetched": pages_fetched,
        "duplicates_removed": duplicates,
        "api_key_used": bool(api_key),
        "pagination_complete": not has_next_page,
        "max_pages_reached": (
            has_next_page and pages_fetched >= max_pages
        ),
        "liquidity_filter_usd": 40000,
    }


def _as_number(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 0 else None


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _resolve_token(tokens):
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None

    valid = [token for token in tokens if isinstance(token, dict)]

    if len(valid) != 2:
        return None

    quote_tokens = [
        token for token in valid
        if str(token.get("id", "")).lower() in QUOTE_TOKEN_IDS
    ]

    if len(quote_tokens) != 1:
        return None

    return next(token for token in valid if token is not quote_tokens[0])


def prepare_pool(
    pool,
    now=None,
    min_age_hours=30,
    max_age_hours=72,
    min_liquidity_usd=40000,
):
    if not isinstance(pool, dict):
        raise ValueError("Pool must be a dictionary")

    now = now or datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now = now.astimezone(timezone.utc)

    created_at = _parse_timestamp(pool.get("created_at"))
    reasons = []
    missing = []
    age_hours = None

    if created_at is None:
        reasons.append("created_at_missing_or_invalid")
    else:
        age_hours = (now - created_at).total_seconds() / 3600

        if age_hours < min_age_hours or age_hours > max_age_hours:
            reasons.append("pair_age_out_of_range")

    liquidity = _as_number(pool.get("liquidity_usd"))

    if liquidity is None:
        reasons.append("liquidity_missing_or_invalid")
    elif liquidity < min_liquidity_usd:
        reasons.append("liquidity_below_minimum")

    volume = _as_number(pool.get("volume_usd_24h"))
    transactions = _as_number(pool.get("transactions_24h"))

    if volume == 0 and transactions == 0:
        reasons.append("no_24h_trading_activity")
    elif volume is None or transactions is None:
        missing.append("24h_activity_data")

    token = _resolve_token(pool.get("tokens"))

    if token is None:
        missing.append("unambiguous_base_token")

    fdv = _as_number(token.get("fdv")) if token else None
    market_cap = None

    if market_cap is None:
        missing.append("market_cap_usd")

    missing.extend([
        "mint_authority_revoked",
        "lp_burned_or_locked",
        "top10_share_pct",
        "dev_share_pct",
    ])

    status = (
        "rejected" if reasons
        else "incomplete" if missing
        else "ready_for_full_risk_screen"
    )

    return {
        "pool_address": pool.get("id"),
        "dex_name": pool.get("dex_name"),
        "created_at": created_at.isoformat() if created_at else None,
        "pair_age_hours": round(age_hours, 2) if age_hours is not None else None,
        "token_address": token.get("id") if token else None,
        "token_name": token.get("name") if token else None,
        "token_symbol": token.get("symbol") if token else None,
        "liquidity_usd": liquidity,
        "volume_usd_24h": volume,
        "transactions_24h": transactions,
        "fdv_usd": fdv,
        "market_cap_usd": None,
        "status": status,
        "rejection_reasons": reasons,
        "missing_checks": missing,
    }


def prepare_pools(pools, **kwargs):
    return [prepare_pool(pool, **kwargs) for pool in pools]


if __name__ == "__main__":
    print("SOLANA MEMECOIN DESK")
    print("===================")

    print("\n1. GeckoTerminal")
    gecko = fetch_new_pools()
    print("Verbindung erfolgreich.")
    print("Neue Pools:", len(gecko["data"]))

    print("\n2. DexPaprika")
    paprika = fetch_pools_by_age()
    pools = paprika["results"]
    prepared = prepare_pools(pools)

    rejected = sum(p["status"] == "rejected" for p in prepared)
    incomplete = sum(p["status"] == "incomplete" for p in prepared)
    ready = sum(
        p["status"] == "ready_for_full_risk_screen"
        for p in prepared
    )

    print("Verbindung erfolgreich.")
    print("API-Key mitgesendet:", paprika["api_key_used"])
    print("Pagination vollständig:", paprika["pagination_complete"])
    print("Liquiditätsfilter:", paprika["liquidity_filter_usd"], "USD")

    if paprika["max_pages_reached"]:
        print(
            "WARNUNG: Seitenlimit erreicht. "
            "Weitere Pools wurden noch nicht geladen."
        )

    print("Abgerufene Seiten:", paprika["pages_fetched"])
    print("Pools insgesamt:", len(pools))
    print("Doppelte Pools entfernt:", paprika["duplicates_removed"])
    print("Weitere Seiten vorhanden:", paprika["has_next_page"])
    print("Vorprüfung abgelehnt:", rejected)
    print("Unvollständig / weitere Daten nötig:", incomplete)
    print("Bereit für vollständige Risikoprüfung:", ready)

    if prepared:
        print("\nBEISPIELE DER AUFBEREITETEN DATEN:")

        for item in prepared[:3]:
            print(json.dumps(item, indent=2, ensure_ascii=False))

    print("\nHinweis: FDV ist nicht Marktkapitalisierung.")
    print("Live-Trading: Deaktiviert")
    print("Paper-Trading: Nicht aktiviert")
