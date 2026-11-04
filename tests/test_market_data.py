
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
import requests

from desk.market_data import (
    MarketDataError,
    fetch_new_pools,
    fetch_token_market_caps,
    prepare_pool,
    prepare_pools,
)

QUOTE_SOL = "So11111111111111111111111111111111111111112"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def make_pool(**overrides):
    pool = {
        "id": "pool-address",
        "created_at": "2026-11-01T12:00:00Z",
        "liquidity_usd": 50000,
        "volume_usd_24h": 12000,
        "transactions_24h": 80,
        "tokens": [
            {"id": QUOTE_SOL, "name": "Wrapped SOL"},
            {
                "id": "TokenMintAddress123",
                "name": "Example",
                "symbol": "EX",
                "fdv": 900000,
            },
        ],
    }
    pool.update(overrides)
    return pool


# --------------------------------------------------
# GeckoTerminal: neue Pools
# --------------------------------------------------

def test_fetch_new_pools_returns_data():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"data": [{"id": "test-pool"}]}

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ) as mocked_get:
        result = fetch_new_pools()

    assert result["data"][0]["id"] == "test-pool"
    mocked_get.assert_called_once()


def test_fetch_new_pools_rejects_invalid_response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"unexpected": []}

    with patch("desk.market_data.requests.get", return_value=response):
        with pytest.raises(MarketDataError):
            fetch_new_pools()


def test_fetch_new_pools_handles_http_errors():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError(
        "API unavailable"
    )

    with patch("desk.market_data.requests.get", return_value=response):
        with pytest.raises(MarketDataError):
            fetch_new_pools()


def test_fetch_new_pools_rejects_invalid_page():
    with pytest.raises(ValueError):
        fetch_new_pools(page=0)


# --------------------------------------------------
# Pool-Vorprüfung
# --------------------------------------------------

def test_prepare_pool_marks_missing_security_checks():
    result = prepare_pool(make_pool(), now=NOW)

    assert result["pair_age_hours"] == 48
    assert result["token_address"] == "TokenMintAddress123"
    assert result["fdv_usd"] == 900000
    assert result["market_cap_usd"] is None
    assert result["status"] == "incomplete"
    assert "mint_authority_revoked" in result["missing_checks"]


def test_prepare_pool_rejects_low_liquidity_and_zero_activity():
    pool = make_pool(
        liquidity_usd=0,
        volume_usd_24h=0,
        transactions_24h=0,
    )

    result = prepare_pool(pool, now=NOW)

    assert result["status"] == "rejected"
    assert "liquidity_below_minimum" in result["rejection_reasons"]
    assert "no_24h_trading_activity" in result["rejection_reasons"]


def test_prepare_pool_does_not_guess_unknown_token_pair():
    pool = make_pool(
        tokens=[
            {"id": "token-a", "symbol": "AAA"},
            {"id": "token-b", "symbol": "BBB"},
        ]
    )

    result = prepare_pool(pool, now=NOW)

    assert result["token_address"] is None
    assert "unambiguous_base_token" in result["missing_checks"]
    assert result["status"] == "incomplete"


# --------------------------------------------------
# GeckoTerminal: Marktkapitalisierung
# --------------------------------------------------

def test_fetch_token_market_caps_returns_market_caps():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {
                "id": "solana_TokenMintAddress123",
                "attributes": {
                    "address": "TokenMintAddress123",
                    "market_cap_usd": "150000",
                },
            }
        ]
    }

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ) as mocked_get:
        result = fetch_token_market_caps(
            ["TokenMintAddress123"]
        )

    assert result["tokenmintaddress123"] == 150000.0
    mocked_get.assert_called_once()


def test_fetch_token_market_caps_handles_empty_input():
    result = fetch_token_market_caps([])

    assert result == {}


def test_fetch_token_market_caps_handles_api_error():
    with patch(
        "desk.market_data.requests.get",
        side_effect=requests.RequestException("API unavailable"),
    ):
        with pytest.raises(MarketDataError):
            fetch_token_market_caps(["TokenMintAddress123"])


def test_fetch_token_market_caps_does_not_use_fdv_as_market_cap():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {
                "id": "solana_TokenMintAddress123",
                "attributes": {
                    "address": "TokenMintAddress123",
                    "fdv_usd": "250000",
                    "market_cap_usd": None,
                },
            }
        ]
    }

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ):
        result = fetch_token_market_caps(
            ["TokenMintAddress123"]
        )

    assert "tokenmintaddress123" in result
    assert result["tokenmintaddress123"] is None


# --------------------------------------------------
# Zusammenführung der Marktdaten
# --------------------------------------------------

def test_prepare_pools_fetches_and_assigns_market_caps():
    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={"tokenmintaddress123": 150000.0},
    ):
        results = prepare_pools(
            [make_pool()],
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd"] == 150000.0
    assert results[0]["market_cap_source"] == "geckoterminal"


def test_prepare_pools_handles_missing_market_cap():
    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={"tokenmintaddress123": None},
    ):
        results = prepare_pools(
            [make_pool()],
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]


def test_prepare_pools_handles_geckoterminal_errors_safely():
    with patch(
        "desk.market_data.fetch_token_market_caps",
        side_effect=MarketDataError("API unavailable"),
    ):
        results = prepare_pools(
            [make_pool()],
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]
    assert "market_data_error" in results[0]
    assert results[0]["status"] == "incomplete"
