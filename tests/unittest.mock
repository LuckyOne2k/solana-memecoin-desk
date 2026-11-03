from unittest.mock import Mock, patch

import pytest
import requests

from desk.market_data import (
    MarketDataError,
    fetch_new_pools,
)


def test_fetch_new_pools_returns_data():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [{"id": "test-pool"}]
    }

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

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ):
        with pytest.raises(MarketDataError):
            fetch_new_pools()


def test_fetch_new_pools_handles_http_errors():
    response = Mock()
    response.raise_for_status.side_effect = (
        requests.HTTPError("API unavailable")
    )

    with patch(
        "desk.market_data.requests.get",
        return_value=response,
    ):
        with pytest.raises(MarketDataError):
            fetch_new_pools()


def test_fetch_new_pools_rejects_invalid_page():
    with pytest.raises(ValueError):
        fetch_new_pools(page=0)
