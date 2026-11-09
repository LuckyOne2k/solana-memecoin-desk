
import pytest
import requests
from unittest.mock import patch

from desk.solana_rpc import (
    SolanaRpcError,
    _rpc_call,
    analyze_largest_accounts,
    fetch_holder_snapshot,
)


MINT = "TestMint123"


def make_largest_account(amount, address):
    return {
        "address": address,
        "amount": str(amount),
    }


def make_parsed_account(owner, mint=MINT):
    return {
        "data": {
            "parsed": {
                "info": {
                    "mint": mint,
                    "owner": owner,
                }
            }
        }
    }


def test_multiple_token_accounts_of_same_owner_are_aggregated():
    largest = [
        make_largest_account(100, "ACCOUNT1"),
        make_largest_account(200, "ACCOUNT2"),
        make_largest_account(300, "ACCOUNT3"),
    ]

    parsed = [
        make_parsed_account("WALLET_A"),
        make_parsed_account("WALLET_A"),
        make_parsed_account("WALLET_B"),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
    )

    assert result["distinct_owners_observed"] == 2
    assert result["top10_observed_owner_share_lower_bound_pct"] == 6.0
    assert result["invalid_account_rows"] == 0


def test_creator_share_is_calculated_when_creator_is_found():
    largest = [
        make_largest_account(500, "ACCOUNT1"),
        make_largest_account(300, "ACCOUNT2"),
    ]

    parsed = [
        make_parsed_account("CREATOR"),
        make_parsed_account("OTHER"),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
        creator_address="CREATOR",
    )

    assert result["creator_found_in_sample"] is True
    assert result["creator_share_lower_bound_pct"] == 5.0


def test_creator_share_is_unknown_when_creator_is_not_found():
    largest = [
        make_largest_account(500, "ACCOUNT1"),
    ]

    parsed = [
        make_parsed_account("OTHER"),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
        creator_address="CREATOR",
    )

    assert result["creator_found_in_sample"] is False
    assert result["creator_share_lower_bound_pct"] is None


def test_wrong_mint_is_counted_as_invalid_row():
    largest = [
        make_largest_account(500, "ACCOUNT1"),
    ]

    parsed = [
        make_parsed_account("WALLET_A", mint="WRONG_MINT"),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
    )

    assert result["invalid_account_rows"] == 1
    assert result["distinct_owners_observed"] == 0
    assert result["sample_owner_mapping_complete"] is False


def test_missing_owner_is_not_counted_as_valid_mapping():
    largest = [
        make_largest_account(500, "ACCOUNT1"),
    ]

    parsed = [
        make_parsed_account(None),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
    )

    assert result["invalid_account_rows"] == 1
    assert result["sample_owner_mapping_complete"] is False


def test_top10_share_is_never_marked_fully_verified():
    largest = [
        make_largest_account(1000, "ACCOUNT1"),
    ]

    parsed = [
        make_parsed_account("WALLET_A"),
    ]

    result = analyze_largest_accounts(
        MINT,
        largest,
        parsed,
        "10000",
    )

    assert result["top10_share_fully_verified"] is False
    assert result["data_scope"] == "largest_20_token_accounts_sample"


@pytest.mark.parametrize(
    "supply",
    [
        None,
        "",
        "0",
        "-100",
        "abc",
        10000,
    ],
)
def test_invalid_total_supply_is_rejected(supply):
    with pytest.raises(ValueError):
        analyze_largest_accounts(
            MINT,
            [],
            [],
            supply,
        )


def test_fetch_holder_snapshot_wraps_invalid_supply_as_rpc_error():
    largest_result = {
        "context": {"slot": 123},
        "value": [
            make_largest_account(500, "ACCOUNT1"),
        ],
    }

    parsed_result = {
        "value": [
            make_parsed_account("WALLET_A"),
        ],
    }

    supply_result = {
        "value": {
            "amount": "invalid",
            "decimals": 6,
        },
    }

    with patch(
        "desk.solana_rpc._rpc_call",
        side_effect=[
            largest_result,
            parsed_result,
            supply_result,
        ],
    ):
        with pytest.raises(
            SolanaRpcError,
            match="Unable to analyze holder snapshot",
        ):
            fetch_holder_snapshot(MINT)

def make_rpc_response(status_code, content=b'{"result":{"ok":true}}'):
    response = requests.Response()
    response.status_code = status_code
    response._content = content
    return response


def test_rpc_call_retries_http_429_then_succeeds():
    responses = [
        make_rpc_response(429),
        make_rpc_response(429),
        make_rpc_response(200),
    ]

    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=responses,
    ) as mock_post, patch(
        "desk.solana_rpc.time.sleep",
    ) as mock_sleep:

        result = _rpc_call("getHealth", [])

    assert result == {"ok": True}
    assert mock_post.call_count == 3
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1, 2]


def test_rpc_call_does_not_retry_http_400():
    response = make_rpc_response(400)

    with patch(
        "desk.solana_rpc.requests.post",
        return_value=response,
    ) as mock_post, patch(
        "desk.solana_rpc.time.sleep",
    ) as mock_sleep:

        with pytest.raises(
            SolanaRpcError,
            match="Solana RPC request failed",
        ):
            _rpc_call("getHealth", [])

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


def test_rpc_call_retries_timeout_three_times():
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=requests.Timeout("Connection timed out"),
    ) as mock_post, patch(
        "desk.solana_rpc.time.sleep",
    ) as mock_sleep:

        with pytest.raises(
            SolanaRpcError,
            match="Solana RPC request failed",
        ):
            _rpc_call("getHealth", [])

    assert mock_post.call_count == 3
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1, 2]

def test_rpc_call_retries_minimum_context_slot_error():
    error_response = (
        b'{"error":{"code":-32016,'
        b'"message":"Minimum context slot has not been reached"}}'
    )

    responses = [
        make_rpc_response(200, error_response),
        make_rpc_response(200, error_response),
        make_rpc_response(200),
    ]

    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=responses,
    ) as mock_post, patch(
        "desk.solana_rpc.time.sleep",
    ) as mock_sleep:

        result = _rpc_call("getHealth", [])

    assert result == {"ok": True}
    assert mock_post.call_count == 3
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1, 2]


SECRET_URL = "https://rpc.example.invalid/?api-key=GEHEIM-12345"


def test_rpc_http_error_does_not_leak_url_or_key():
    response = make_rpc_response(401)
    response.url = SECRET_URL

    with patch(
        "desk.solana_rpc.requests.post",
        return_value=response,
    ), patch("desk.solana_rpc.time.sleep"):
        with pytest.raises(SolanaRpcError) as excinfo:
            _rpc_call("getHealth", [], rpc_url=SECRET_URL)

    message = str(excinfo.value)
    assert "GEHEIM" not in message
    assert "api-key" not in message
    assert "HTTP 401" in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_rpc_connection_error_does_not_leak_url_or_key():
    error = requests.ConnectionError(
        "HTTPSConnectionPool(host='rpc.example.invalid', port=443): "
        "Max retries exceeded with url: /?api-key=GEHEIM-12345"
    )

    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=error,
    ), patch("desk.solana_rpc.time.sleep"):
        with pytest.raises(SolanaRpcError) as excinfo:
            _rpc_call("getHealth", [], rpc_url=SECRET_URL)

    message = str(excinfo.value)
    assert "GEHEIM" not in message
    assert "api-key" not in message
    assert message == "Solana RPC request failed: ConnectionError"
