import requests


BASE_URL = (
    "https://api.geckoterminal.com/api/v2"
)


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

    print("GeckoTerminal Verbindung erfolgreich.")
    print(f"Gefundene Pools: {len(pools)}")
