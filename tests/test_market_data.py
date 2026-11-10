
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
import requests

import copy

from desk.config import load_config
from desk.market_data import (
    SECURITY_CHECKS,
    MarketDataError,
    fetch_pools_by_age,
    fetch_token_market_data,
    prepare_pool,
    prepare_pools,
)

QUOTE_SOL = "So11111111111111111111111111111111111111112"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
CONFIG = load_config()


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
# Pool-Vorprüfung
# --------------------------------------------------

def test_prepare_pool_marks_missing_security_checks():
    result = prepare_pool(make_pool(), CONFIG, now=NOW)

    assert result["pair_age_hours"] == 48
    assert result["token_address"] == "TokenMintAddress123"
    assert result["fdv_usd_dexpaprika"] == 900000
    assert result["market_cap_usd_reported"] is None
    assert result["status"] == "incomplete"
    assert "mint_authority_revoked" in result["missing_checks"]


def test_prepare_pool_rejects_low_liquidity_and_zero_activity():
    pool = make_pool(
        liquidity_usd=0,
        volume_usd_24h=0,
        transactions_24h=0,
    )

    result = prepare_pool(pool, CONFIG, now=NOW)

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

    result = prepare_pool(pool, CONFIG, now=NOW)

    assert result["token_address"] is None
    assert "unambiguous_base_token" in result["missing_checks"]
    assert result["status"] == "incomplete"


# --------------------------------------------------
# GeckoTerminal: Marktkapitalisierung
# --------------------------------------------------

def test_fetch_token_market_data_returns_market_caps():
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
        result = fetch_token_market_data(
            ["TokenMintAddress123"]
        )

    assert result["tokenmintaddress123"]["market_cap_usd"] == 150000.0
    mocked_get.assert_called_once()


def test_fetch_token_market_data_handles_empty_input():
    result = fetch_token_market_data([])

    assert result == {}


def test_fetch_token_market_data_handles_api_error():
    with patch(
        "desk.market_data.requests.get",
        side_effect=requests.RequestException("API unavailable"),
    ):
        with pytest.raises(MarketDataError):
            fetch_token_market_data(["TokenMintAddress123"])


def test_fetch_token_market_data_does_not_use_fdv_as_market_cap():
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
        result = fetch_token_market_data(
            ["TokenMintAddress123"]
        )

    assert "tokenmintaddress123" in result
    assert result["tokenmintaddress123"]["market_cap_usd"] is None
    assert result["tokenmintaddress123"]["fdv_usd"] == 250000.0


# --------------------------------------------------
# Zusammenführung der Marktdaten
# --------------------------------------------------

def test_prepare_pools_fetches_and_assigns_market_caps():
    with patch(
        "desk.market_data.fetch_token_market_data",
        return_value={
            "tokenmintaddress123": {
                "market_cap_usd": 500000.0,
                "price_usd": 0.0005,
                "fdv_usd": 500000.0,
            }
        },
    ):
        results = prepare_pools(
            [make_pool()],
            CONFIG,
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd_reported"] == 500000.0
    assert results[0]["market_cap_reported_source"] == "geckoterminal"
    assert results[0]["price_usd_geckoterminal"] == 0.0005
    # Bestätigt wird erst in der Prüfkette mit Blockchain-Daten.
    assert "market_cap_usd" in results[0]["missing_checks"]


def test_prepare_pools_handles_missing_market_cap():
    with patch(
        "desk.market_data.fetch_token_market_data",
        return_value={
            "tokenmintaddress123": {
                "market_cap_usd": None,
                "price_usd": None,
                "fdv_usd": None,
            }
        },
    ):
        results = prepare_pools(
            [make_pool()],
            CONFIG,
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd_reported"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]


def test_prepare_pools_handles_geckoterminal_errors_safely():
    with patch(
        "desk.market_data.fetch_token_market_data",
        side_effect=MarketDataError("API unavailable"),
    ):
        results = prepare_pools(
            [make_pool()],
            CONFIG,
            now=NOW,
        )

    assert len(results) == 1
    assert results[0]["market_cap_usd_reported"] is None
    assert "market_cap_usd" in results[0]["missing_checks"]
    assert "market_data_error" in results[0]
    assert results[0]["status"] == "incomplete"


# --------------------------------------------------
# Konfiguration und einheitliche Prüfkette
# --------------------------------------------------

def test_prepare_pool_marks_all_security_checks_as_open():
    result = prepare_pool(
        make_pool(),
        CONFIG,
        now=NOW,
        market_caps={"tokenmintaddress123": {"market_cap_usd": 500000}},
    )

    for check in SECURITY_CHECKS:
        assert check in result["missing_checks"]

    assert "security_data" not in result
    assert result["status"] == "incomplete"


def test_prepare_pool_uses_liquidity_threshold_from_config():
    config = copy.deepcopy(CONFIG)
    config["risk_filters"]["reject_liquidity_below_usd"] = 60000

    strict = prepare_pool(make_pool(liquidity_usd=50000), config, now=NOW)
    default = prepare_pool(make_pool(liquidity_usd=50000), CONFIG, now=NOW)

    assert "liquidity_below_minimum" in strict["rejection_reasons"]
    assert "liquidity_below_minimum" not in default["rejection_reasons"]


def test_prepare_pool_uses_age_window_from_config():
    config = copy.deepcopy(CONFIG)
    config["scanner"]["pair_age_hours_min"] = 50

    result = prepare_pool(make_pool(), config, now=NOW)

    assert "pair_age_out_of_range" in result["rejection_reasons"]


def test_market_data_module_no_longer_runs_security_checks():
    import desk.market_data as market_data

    assert not hasattr(market_data, "check_token_security")


def test_fetch_pools_by_age_uses_given_thresholds():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": [{"id": "pool-1"}],
        "has_next_page": False,
    }

    with patch(
        "desk.market_data.time.time",
        return_value=1_000_000,
    ), patch(
        "desk.market_data.requests.get",
        return_value=response,
    ) as mocked_get:
        result = fetch_pools_by_age(
            min_age_hours=30,
            max_age_hours=72,
            min_liquidity_usd=40000,
        )

    params = mocked_get.call_args.kwargs["params"]

    assert params["created_after"] == 1_000_000 - 72 * 3600
    assert params["created_before"] == 1_000_000 - 30 * 3600
    assert params["liquidity_usd_min"] == 40000
    assert result["liquidity_filter_usd"] == 40000
    assert len(result["results"]) == 1


def test_fetch_pools_by_age_keeps_time_window_across_pages():
    first = Mock()
    first.raise_for_status.return_value = None
    first.json.return_value = {
        "results": [{"id": "pool-1"}],
        "has_next_page": True,
        "next_cursor": "cursor-2",
    }

    second = Mock()
    second.raise_for_status.return_value = None
    second.json.return_value = {
        "results": [{"id": "pool-2"}],
        "has_next_page": False,
    }

    with patch(
        "desk.market_data.time.time",
        side_effect=[1_000_000, 1_000_500, 1_001_000],
    ), patch(
        "desk.market_data.time.sleep",
    ), patch(
        "desk.market_data.requests.get",
        side_effect=[first, second],
    ) as mocked_get:
        result = fetch_pools_by_age(
            min_age_hours=30,
            max_age_hours=72,
            min_liquidity_usd=40000,
        )

    windows = [
        (
            call.kwargs["params"]["created_after"],
            call.kwargs["params"]["created_before"],
        )
        for call in mocked_get.call_args_list
    ]

    assert windows[0] == windows[1]
    assert mocked_get.call_args_list[1].kwargs["params"]["cursor"] == (
        "cursor-2"
    )
    assert len(result["results"]) == 2


def test_fetch_pools_by_age_rejects_inverted_window():
    with pytest.raises(ValueError):
        fetch_pools_by_age(
            min_age_hours=80,
            max_age_hours=72,
            min_liquidity_usd=40000,
        )


# --------------------------------------------------
# DexPaprika: begrenzte Wiederholungen
# --------------------------------------------------

def _http_error(status):
    import requests as _requests
    error = _requests.HTTPError(f"{status}")
    error.response = Mock(status_code=status)
    failing = Mock()
    failing.raise_for_status.side_effect = error
    return failing


def _ok_page():
    ok = Mock()
    ok.raise_for_status.return_value = None
    ok.json.return_value = {"results": [{"id": "pool-1"}], "has_next_page": False}
    return ok


def test_fetch_pools_by_age_retries_429_then_succeeds():
    waits = []
    with patch("desk.market_data._wait", waits.append), patch(
        "desk.market_data.requests.get",
        side_effect=[_http_error(429), _http_error(503), _ok_page()],
    ) as mocked_get:
        result = fetch_pools_by_age(min_age_hours=24, max_age_hours=168,
                                    min_liquidity_usd=40000)

    assert mocked_get.call_count == 3
    assert waits == [5, 10]
    assert len(result["results"]) == 1


def test_fetch_pools_by_age_gives_up_after_three_attempts():
    with patch("desk.market_data._wait"), patch(
        "desk.market_data.requests.get",
        side_effect=[_http_error(429)] * 3,
    ) as mocked_get:
        with pytest.raises(MarketDataError):
            fetch_pools_by_age(min_age_hours=24, max_age_hours=168,
                               min_liquidity_usd=40000)

    assert mocked_get.call_count == 3


def test_fetch_pools_by_age_does_not_retry_client_errors():
    with patch("desk.market_data._wait") as wait, patch(
        "desk.market_data.requests.get",
        side_effect=[_http_error(401)],
    ) as mocked_get:
        with pytest.raises(MarketDataError):
            fetch_pools_by_age(min_age_hours=24, max_age_hours=168,
                               min_liquidity_usd=40000)

    assert mocked_get.call_count == 1
    wait.assert_not_called()
