
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
import requests

from desk.market_data import (
    MarketDataError,
    fetch_new_pools,
    prepare_pool,
)

QUOTE_SOL = "So11111111111111111111111111111111111111112"


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


def test_prepare_pool_marks_missing_security_checks():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    pool = {
        "id": "pool-address",
        "created_at": "2026-11-01T12:00:00Z",
        "liquidity_usd": 50000,
        "volume_usd_24h": 12000,
        "transactions_24h": 80,
        "tokens": [
            {"id": QUOTE_SOL, "name": "Wrapped SOL"},
            {
                "id": "token-address",
                "name": "Example",
                "symbol": "EX",
                "fdv": 900000,
            },
        ],
    }

    result = prepare_pool(pool, now=now)

    assert result["pair_age_hours"] == 48
    assert result["token_address"] == "token-address"
    assert result["fdv_usd"] == 900000
    assert result["market_cap_usd"] is None
    assert result["status"] == "incomplete"
    assert "mint_authority_revoked" in result["missing_checks"]


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
