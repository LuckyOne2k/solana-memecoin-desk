from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from desk.market_data import (
    MarketDataError,
    fetch_token_market_caps,
    prepare_pools,
)


def make_pool(**overrides):
    pool = {
        "chain": "solana",
        "address": "TokenMintAddress123",
        "created_at": "2026-11-03T10:00:00Z",
        "liquidity_usd": 100_000,
        "volume_24h_usd": 50_000,
        "market_cap_usd": None,
        "fdv_usd": 200_000,
        "missing_checks": [],
        "status": "pending",
    }
    pool.update(overrides)
    return pool


def test_fetch_token_market_caps_returns_market_caps():
    response = {
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
        "desk.market_data.requests.get"
    ) as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = response
        mock_get.return_value.raise_for_status.return_value = None

        result = fetch_token_market_caps(
            ["TokenMintAddress123"]
        )

    assert result["TokenMintAddress123"] == 150000.0


def test_fetch_token_market_caps_handles_empty_input():
    result = fetch_token_market_caps([])

    assert result == {}


def test_fetch_token_market_caps_handles_api_error():
    with patch(
        "desk.market_data.requests.get",
        side_effect=Exception("API unavailable"),
    ):
        with pytest.raises(MarketDataError):
            fetch_token_market_caps(
                ["TokenMintAddress123"]
            )


def test_fetch_token_market_caps_does_not_use_fdv_as_market_cap():
    response = {
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
        "desk.market_data.requests.get"
    ) as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = response
        mock_get.return_value.raise_for_status.return_value = None

        result = fetch_token_market_caps(
            ["TokenMintAddress123"]
        )

    assert result["TokenMintAddress123"] is None


def test_prepare_pools_fetches_and_assigns_market_caps():
    pool = make_pool()

    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={"TokenMintAddress123": 150000.0},
    ):
        results = prepare_pools([pool])

    assert len(results) == 1
    assert results[0]["market_cap_usd"] == 150000.0


def test_prepare_pools_handles_missing_market_cap():
    pool = make_pool()

    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={"TokenMintAddress123": None},
    ):
        results = prepare_pools([pool])

    assert len(results) == 1
    assert results[0]["market_cap_usd"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]


def test_prepare_pools_handles_geckoterminal_errors_safely():
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    with patch(
        "desk.market_data.fetch_token_market_caps",
        side_effect=MarketDataError("API unavailable"),
    ):
        results = prepare_pools(
            [make_pool()],
            now=now,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]
    assert "market_data_error" in results[0]
    assert results[0]["status"] == "incomplete"
