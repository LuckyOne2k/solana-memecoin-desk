
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


def fetch_pools_by_age(timeout=20, max_pages=10):
    api_key = os.getenv("DEXPAPRIKA_API_KEY")
    cursor = None
    all_pools = []
    seen_ids = set()
    pages_fetched = 0
    duplicates = 0

    for page in range(max_pages):
        params = {
            "order_by": "created_at",
            "sort": "desc",
            "created_after": "-72h",
            "created_before": "-30h",
            "limit": 100,
            "detailed": "true",
        }

        if cursor:
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

        for pool in payload["results"]:
            pool_id = pool.get("id")

            if pool_id and pool_id in seen_ids:
                duplicates += 1
                continue

            if pool_id:
                seen_ids.add(pool_id)

            all_pools.append(pool)

        if not payload.get("has_next_page"):
            break

        cursor = payload.get("next_cursor")

        if not cursor:
            break

        if not api_key:
            break

    return {
        "results": all_pools,
        "has_next_page": bool(payload.get("has_next_page")),
        "next_cursor": cursor,
        "pages_fetched": pages_fetched,
        "duplicates_removed": duplicates,
        "api_key_used": bool(api_key),
    }
