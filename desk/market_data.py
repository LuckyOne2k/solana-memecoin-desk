
import json
import requests


BASE_URL = "https://api.geckoterminal.com/api/v2"
DEXPAPRIKA_URL = "https://api.dexpaprika.com"


class MarketDataError(Exception):
    pass


def fetch_new_pools(page=1, timeout=15):
    if page < 1:
        raise ValueError("Page must be at least 1")

    url = f"{BASE_URL}/networks/solana/new_pools"

    try:
        response = requests.get(
            url,
            params={
                "page": page,
                "include": "base_token,quote_token",
            },
            headers={
                "Accept": "application/json;version=20230203"
            },
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

    if not isinstance(payload, dict):
        raise MarketDataError("Unexpected API response")

    if not isinstance(payload.get("data"), list):
        raise MarketDataError("Invalid pool list")

    return payload


def fetch_pools_by_age(timeout=20):
    url = f"{DEXPAPRIKA_URL}/networks/solana/pools/search"

    try:
        response = requests.get(
            url,
            params={
                "order_by": "created_at",
                "sort": "desc",
                "created_after": "-72h",
                "created_before": "-30h",
                "limit": 50,
                "detailed": "true",
            },
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

    if not isinstance(payload, dict):
        raise MarketDataError(
            "Unexpected DexPaprika response"
        )

    if not isinstance(payload.get("results"), list):
        raise MarketDataError(
            "DexPaprika returned no valid results list"
        )

    return payload


if __name__ == "__main__":
    print("SOLANA MEMECOIN DESK")
    print("===================")

    print()
    print("1. GeckoTerminal")

    gecko = fetch_new_pools()
    print("Verbindung erfolgreich.")
    print("Neue Pools:", len(gecko["data"]))

    print()
    print("2. DexPaprika")

    paprika = fetch_pools_by_age()
    pools = paprika["results"]

    print("Verbindung erfolgreich.")
    print("Pools im Altersfenster 30-72 Stunden:", len(pools))

    if pools:
        print()
        print("VERFUEGBARE DATENFELDER:")
        print(", ".join(sorted(pools[0].keys())))

        print()
        print("BEISPIELDATEN:")

        for index, pool in enumerate(pools[:3], start=1):
            print()
            print(f"Pool {index}:")
            print(json.dumps(
                pool,
                indent=2,
                ensure_ascii=False
            )[:2500])
    else:
        print("Keine Pools im Altersfenster gefunden.")

    print()
    print("Live-Trading: Deaktiviert")
    print("Paper-Trading: Noch nicht aktiviert")
