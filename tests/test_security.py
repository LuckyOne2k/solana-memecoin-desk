from unittest.mock import patch

import pytest

from desk.security import (
    SecurityDataError,
    analyze_rugcheck_report,
    fetch_rugcheck_report,
    fetch_rugcheck_summary,
)


def test_mint_authority_explicitly_revoked():
    report = {
        "mint": "TOKEN123",
        "token": {
            "mintAuthority": None,
            "freezeAuthority": None,
        },
    }

    result = analyze_rugcheck_report(report, {})

    assert result["mint_authority_revoked"] is True
    assert result["freeze_authority_revoked"] is True


def test_active_authority_is_not_revoked():
    report = {
        "mint": "TOKEN123",
        "token": {
            "mintAuthority": "SOME_AUTHORITY",
            "freezeAuthority": "SOME_AUTHORITY",
        },
    }

    result = analyze_rugcheck_report(report, {})

    assert result["mint_authority_revoked"] is False
    assert result["freeze_authority_revoked"] is False


def test_missing_authority_data_stays_unknown():
    report = {
        "mint": "TOKEN123",
        "token": {},
    }

    result = analyze_rugcheck_report(report, {})

    assert result["mint_authority_revoked"] is None
    assert result["freeze_authority_revoked"] is None


def test_lp_lock_threshold():
    report = {"mint": "TOKEN123"}

    locked = analyze_rugcheck_report(
        report,
        {"lpLockedPct": 100},
    )

    not_locked = analyze_rugcheck_report(
        report,
        {"lpLockedPct": 95},
    )

    unknown = analyze_rugcheck_report(
        report,
        {},
    )

    assert locked["lp_burned_or_locked"] is True
    assert not_locked["lp_burned_or_locked"] is False
    assert unknown["lp_burned_or_locked"] is None


def test_same_owner_balances_are_aggregated():
    report = {
        "mint": "TOKEN123",
        "creator": "WALLET_A",
        "topHolders": [
            {"owner": "WALLET_A", "pct": 4},
            {"owner": "WALLET_A", "pct": 3},
            {"owner": "WALLET_B", "pct": 5},
        ],
    }

    result = analyze_rugcheck_report(report, {})

    assert result["creator_share_sampled_pct"] == 7
    assert result["largest_reported_holder_pct"] == 7
    assert result["distinct_reported_owners"] == 2


def test_creator_missing_from_sample_stays_unknown():
    report = {
        "mint": "TOKEN123",
        "creator": "CREATOR_WALLET",
        "topHolders": [
            {"owner": "WALLET_A", "pct": 10},
            {"owner": "WALLET_B", "pct": 5},
        ],
    }

    result = analyze_rugcheck_report(report, {})

    assert result["creator_share_sampled_pct"] is None


def test_top10_sample_is_never_fully_verified():
    holders = [
        {"owner": f"WALLET_{i}", "pct": 2}
        for i in range(12)
    ]

    report = {
        "mint": "TOKEN123",
        "topHolders": holders,
    }

    result = analyze_rugcheck_report(report, {})

    assert result["top10_share_sampled_pct"] == 20
    assert result["top10_share_fully_verified"] is False


def test_report_rejects_wrong_token():
    with patch(
        "desk.security._get_json",
        return_value={"mint": "WRONG_TOKEN"},
    ):
        with pytest.raises(SecurityDataError):
            fetch_rugcheck_report("TOKEN123")


def test_report_fetches_correct_token():
    expected = {"mint": "TOKEN123"}

    with patch(
        "desk.security._get_json",
        return_value=expected,
    ):
        result = fetch_rugcheck_report("TOKEN123")

    assert result == expected


def test_summary_fetches_data():
    expected = {"lpLockedPct": 100}

    with patch(
        "desk.security._get_json",
        return_value=expected,
    ):
        result = fetch_rugcheck_summary("TOKEN123")

    assert result["lpLockedPct"] == 100


def test_invalid_mint_is_rejected():
    with pytest.raises(ValueError):
        fetch_rugcheck_report("")

    with pytest.raises(ValueError):
        fetch_rugcheck_summary("")


def test_invalid_analysis_input_is_rejected():
    with pytest.raises(ValueError):
        analyze_rugcheck_report([], {})

    with pytest.raises(ValueError):
        analyze_rugcheck_report({}, [])
