
import requests


BASE_URL = "https://api.geckoterminal.com/api/v2"


class MarketDataError(Exception):
    pass


def fetch_new_pools(page=1, timeout=15):
    if page < 1:
        raise ValueError("Page must be at least 1")

    url = f"{BASE_URL}/networks/solana/new_pools"

    headers = {
        "Accept": "application/json;version=20230203"
    }

    try:
        response = requests.get(
            url,
            params={
                "page": page,
                "include": "base_token,quote_token",
            },
            headers=headers,
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
            "Invalid JSON response from GeckoTerminal"
        ) from error

    if not isinstance(payload, dict):
        raise MarketDataError(
            "Unexpected API response format"
        )

    if not isinstance(payload.get("data"), list):
        raise MarketDataError(
            "API response contains no valid pool list"
        )

    return payload


if __name__ == "__main__":
    result = fetch_new_pools()
    pools = result["data"]
    included = result.get("included", [])

    print("GeckoTerminal Verbindung erfolgreich.")
    print(f"Gefundene Pools: {len(pools)}")
    print()

    print("VERFÜGBARE DATENFELDER:")
    if pools and isinstance(pools[0].get("attributes"), dict):
        attributes = pools[0]["attributes"]
        print(", ".join(sorted(attributes.keys())))
    else:
        print("Keine Pool-Attribute gefunden.")

    print()
    print("BEISPIELDATEN DER ERSTEN 3 POOLS:")

    for index, pool in enumerate(pools[:3], start=1):
        attributes = pool.get("attributes", {})
        if not isinstance(attributes, dict):
            continue

        print()
        print(f"Pool {index}:")
        for key in (
            "name",
            "address",
            "pool_created_at",
            "reserve_in_usd",
            "market_cap_usd",
            "fdv_usd",
            "base_token_price_usd",
        ):
            if key in attributes:
                print(f"  {key}: {attributes[key]}")

    print()
    print("Eingebundene Zusatzdaten:", len(included))
    
