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
TOKEN_ADDRESS = "ExampleTokenAddress123"


def make_pool():
    return {
        "id": "pool-address",
        "created_at": "2026-11-01T12:00:00Z",
        "liquidity_usd": 50000,
        "volume_usd_24h": 12000,
        "transactions_24h": 80,
        "tokens": [
            {"id": QUOTE_SOL, "name": "Wrapped SOL"},
            {
                "id": TOKEN_ADDRESS,
                "name": "Example",
                "symbol": "EX",
                "fdv": 900000,
            },
        ],
    }


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


def test_fetch_token_market_caps_returns_real_market_cap():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {
                "attributes": {
                    "address": TOKEN_ADDRESS,
                    "market_cap_usd": "125000.50",
                }
            }
        ]
    }

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ) as mocked_get:
        result = fetch_token_market_caps(
            [TOKEN_ADDRESS, TOKEN_ADDRESS]
        )

    assert result[TOKEN_ADDRESS.lower()] == 125000.50
    mocked_get.assert_called_once()

    requested_url = mocked_get.call_args[0][0]
    assert "/networks/solana/tokens/multi/" in requested_url
    assert TOKEN_ADDRESS in requested_url


def test_fetch_token_market_caps_keeps_missing_market_cap_as_none():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {
                "attributes": {
                    "address": TOKEN_ADDRESS,
                    "market_cap_usd": None,
                }
            }
        ]
    }

    with patch("desk.market_data.requests.get", return_value=response):
        result = fetch_token_market_caps([TOKEN_ADDRESS])

    assert result[TOKEN_ADDRESS.lower()] is None


def test_fetch_token_market_caps_rejects_invalid_response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"unexpected": []}

    with patch("desk.market_data.requests.get", return_value=response):
        with pytest.raises(MarketDataError):
            fetch_token_market_caps([TOKEN_ADDRESS])


def test_fetch_token_market_caps_handles_http_errors():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError(
        "API unavailable"
    )

    with patch("desk.market_data.requests.get", return_value=response):
        with pytest.raises(MarketDataError):
            fetch_token_market_caps([TOKEN_ADDRESS])


def test_prepare_pool_marks_missing_security_checks():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    result = prepare_pool(make_pool(), now=now)

    assert result["pair_age_hours"] == 48
    assert result["token_address"] == TOKEN_ADDRESS
    assert result["fdv_usd"] == 900000
    assert result["market_cap_usd"] is None
    assert result["status"] == "incomplete"
    assert "market_cap_usd" in result["missing_checks"]
    assert "mint_authority_revoked" in result["missing_checks"]


def test_prepare_pool_uses_market_cap_without_substituting_fdv():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    result = prepare_pool(
        make_pool(),
        now=now,
        market_caps={TOKEN_ADDRESS.lower(): 125000.50},
    )

    assert result["market_cap_usd"] == 125000.50
    assert result["market_cap_source"] == "geckoterminal"
    assert result["fdv_usd"] == 900000
    assert "market_cap_usd" not in result["missing_checks"]


def test_prepare_pool_does_not_accept_null_market_cap():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    result = prepare_pool(
        make_pool(),
        now=now,
        market_caps={TOKEN_ADDRESS.lower(): None},
    )

    assert result["market_cap_usd"] is None
    assert result["market_cap_source"] is None
    assert "market_cap_usd" in result["missing_checks"]


def test_prepare_pools_fetches_and_assigns_market_caps():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={TOKEN_ADDRESS.lower(): 125000.50},
    ) as mocked_fetch:
        results = prepare_pools(
            [make_pool()],
            now=now,
        )

    mocked_fetch.assert_called_once_with([TOKEN_ADDRESS])

    assert len(results) == 1
    assert results[0]["market_cap_usd"] == 125000.50
    assert results[0]["market_cap_source"] == "geckoterminal"
    assert "market_cap_usd" not in results[0]["missing_checks"]


def test_prepare_pools_handles_geckoterminal_errors_safely():
    with patch(
        "desk.market_data.fetch_token_market_caps",
        side_effect=MarketDataError("API unavailable"),
    ):
        results = prepare_pools([make_pool()])

    assert len(results) == 1
    assert results[0]["market_cap_usd"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]
    assert "market_data_error" in results[0]
    assert results[0]["status"] == "incomplete"


def test_prepare_pool_rejects_low_liquidity_and_zero_activity():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    pool = {
        "id": "pool-address",
        "created_at": "2026-11-01T12:00:00Z",
        "liquidity_usd": 0,
        "volume_usd_24h": 0,
        "transactions_24h": 0,
        "tokens": [],
    }

    result = prepare_pool(pool, now=now)

    assert result["status"] == "rejected"
    assert "liquidity_below_minimum" in result["rejection_reasons"]
    assert "no_24h_trading_activity" in result["rejection_reasons"]


def test_prepare_pool_does_not_guess_unknown_token_pair():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    pool = {
        "created_at": "2026-11-01T12:00:00Z",
        "liquidity_usd": 50000,
        "volume_usd_24h": 100,
        "transactions_24h": 2,
        "tokens": [
            {"id": "token-a", "symbol": "AAA"},
            {"id": "token-b", "symbol": "BBB"},
        ],
    }

    result = prepare_pool(pool, now=now)

    assert result["token_address"] is None
    assert "unambiguous_base_token" in result["missing_checks"]
    assert result["status"] == "incomplete"
