import pytest

from desk.solana_rpc import (
    SolanaRpcError,
    analyze_largest_accounts,
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
    assert result["owner_mapping_complete"] is False


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
    assert result["owner_mapping_complete"] is False


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
