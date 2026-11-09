from unittest.mock import patch

import pytest

from desk.security import (
    SecurityDataError,
    analyze_rugcheck_report,
    check_token_security,
    fetch_rugcheck_report,
    fetch_rugcheck_summary,
)
from desk.config import load_config


LP_LOCKED_MIN_PCT = load_config()["risk_filters"]["lp_locked_min_pct"]


def analyze(report, summary):
    return analyze_rugcheck_report(
        report,
        summary,
        lp_locked_min_pct=LP_LOCKED_MIN_PCT,
    )


def test_mint_authority_explicitly_revoked():
    report = {
        "mint": "TOKEN123",
        "token": {
            "mintAuthority": None,
            "freezeAuthority": None,
        },
    }

    result = analyze(report, {})

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

    result = analyze(report, {})

    assert result["mint_authority_revoked"] is False
    assert result["freeze_authority_revoked"] is False


def test_missing_authority_data_stays_unknown():
    report = {
        "mint": "TOKEN123",
        "token": {},
    }

    result = analyze(report, {})

    assert result["mint_authority_revoked"] is None
    assert result["freeze_authority_revoked"] is None


def test_lp_lock_threshold():
    report = {"mint": "TOKEN123"}

    locked = analyze(
        report,
        {"lpLockedPct": 100},
    )

    not_locked = analyze(
        report,
        {"lpLockedPct": 95},
    )

    unknown = analyze(
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

    result = analyze(report, {})

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

    result = analyze(report, {})

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

    result = analyze(report, {})

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
        analyze([], {})

    with pytest.raises(ValueError):
        analyze({}, [])

def test_missing_owner_makes_holder_analysis_incomplete():
    report = {
        "mint": "TOKEN123",
        "topHolders": [
            {"owner": "WALLET_A", "pct": 10},
            {"address": "TOKEN_ACCOUNT_B", "pct": 5},
        ],
    }

    result = analyze(report, {})

    assert result["owner_attribution_complete"] is False
    assert result["largest_reported_holder_pct"] is None
    assert result["top10_share_sampled_pct"] is None


def test_invalid_holder_percentage_makes_analysis_incomplete():
    report = {
        "mint": "TOKEN123",
        "topHolders": [
            {"owner": "WALLET_A", "pct": 10},
            {"owner": "WALLET_B", "pct": None},
        ],
    }

    result = analyze(report, {})

    assert result["owner_attribution_complete"] is False
    assert result["invalid_holder_rows"] == 1
    assert result["largest_reported_holder_pct"] is None


def test_lp_lock_threshold_comes_from_parameter():
    report = {"mint": "TOKEN123"}

    strict = analyze_rugcheck_report(
        report,
        {"lpLockedPct": 99.5},
        lp_locked_min_pct=99.99,
    )

    relaxed = analyze_rugcheck_report(
        report,
        {"lpLockedPct": 99.5},
        lp_locked_min_pct=99.0,
    )

    assert strict["lp_burned_or_locked"] is False
    assert relaxed["lp_burned_or_locked"] is True


@pytest.mark.parametrize("threshold", [None, "99", True])
def test_invalid_lp_lock_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        analyze_rugcheck_report(
            {"mint": "TOKEN123"},
            {},
            lp_locked_min_pct=threshold,
        )


def test_check_token_security_passes_threshold():
    with patch(
        "desk.security.fetch_rugcheck_report",
        return_value={"mint": "TOKEN123"},
    ), patch(
        "desk.security.fetch_rugcheck_summary",
        return_value={"lpLockedPct": 99.5},
    ):
        result = check_token_security(
            "TOKEN123",
            lp_locked_min_pct=99.0,
        )

    assert result["mint"] == "TOKEN123"
    assert result["lp_burned_or_locked"] is True


# --------------------------------------------------
# Kennzeichnungen aus echten RugCheck-Antworten (09.11.2026)
# --------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

REAL = json.loads(
    (Path(__file__).parent / "fixtures" / "real_samples_2026-11-09.json")
    .read_text(encoding="utf-8")
)


def test_real_labels_and_market_liquidity_are_extracted():
    case = REAL["HABIBI"]
    result = analyze(case["rugcheck_report"], {"lpLockedPct": 100})
    labels = result["holder_labels"]
    pool = case["dexpaprika_pool"]["id"]

    assert labels["labels_complete"] is True
    assert pool in labels["pool_owner_addresses"]
    assert "36QvFvbc4H3ghiJ2BhaC2f5nkzkDTokdrFYqPmaja9ht" in labels["pool_vault_accounts"]
    assert round(result["market_liquidity_usd_by_pool"][pool]) == 1921
    assert result["rugcheck_supply_raw"] == 977905989042610


def test_real_report_without_markets_has_incomplete_labels():
    result = analyze(REAL["KHELLY"]["rugcheck_report"], {})

    assert result["holder_labels"]["labels_complete"] is False
    assert result["market_liquidity_usd_by_pool"] == {}


def test_malformed_markets_make_labels_incomplete():
    report = {
        "mint": "TOKEN123",
        "markets": [{"pubkey": None}, "x", {"pubkey": "P", "mintA": "TOKEN123"}],
        "knownAccounts": {"A": "not-a-dict"},
    }

    result = analyze(report, {})

    assert result["holder_labels"]["labels_complete"] is False
    assert result["holder_labels"]["invalid_label_rows"] == 4


def test_exchange_label_is_read_only_when_explicit():
    report = {
        "mint": "TOKEN123",
        "markets": [],
        "knownAccounts": {
            "EX1": {"name": "Some CEX", "type": "EXCHANGE"},
            "AMM1": {"name": "Pool", "type": "AMM"},
            "LOCK": {"name": "Locker", "type": "LOCKER"},
            "OTHER": {"name": "Something", "type": "CREATOR"},
        },
    }

    labels = analyze(report, {})["holder_labels"]

    assert labels["exchange_addresses"] == ["EX1"]
    assert labels["pool_owner_addresses"] == ["AMM1"]
    assert labels["locker_addresses"] == ["LOCK"]
