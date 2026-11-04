
from unittest.mock import patch

import pytest

from desk.security import SecurityDataError
from desk.security_scan import scan_pool_security


def make_pool(
    mint="TOKEN123",
    status="incomplete",
    rejection_reasons=None,
    missing_checks=None,
):
    return {
        "token_address": mint,
        "status": status,
        "rejection_reasons": rejection_reasons or [],
        "missing_checks": (
            missing_checks
            if missing_checks is not None
            else [
                "mint_authority_revoked",
                "lp_burned_or_locked",
                "top10_share_pct",
                "dev_share_pct",
                "market_cap_usd",
            ]
        ),
    }


def make_security(mint="TOKEN123", mint_revoked=True, lp_locked=True):
    return {
        "mint": mint,
        "mint_authority_revoked": mint_revoked,
        "lp_burned_or_locked": lp_locked,
    }


def test_successful_security_check_updates_pool():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ) as mocked_check:
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert result["checks_attempted"] == 1
    assert result["checks_succeeded"] == 1
    assert result["checks_failed"] == 0
    assert item["security_check_status"] == "completed"
    assert item["mint_authority_revoked"] is True
    assert item["lp_burned_or_locked"] is True
    assert "mint_authority_revoked" not in item["missing_checks"]
    assert "lp_burned_or_locked" not in item["missing_checks"]
    assert item["status"] == "incomplete"
    mocked_check.assert_called_once_with("TOKEN123")


def test_rejected_pool_is_skipped():
    pool = make_pool(
        status="rejected",
        rejection_reasons=["liquidity_below_minimum"],
    )

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
    ) as mocked_check:
        result = scan_pool_security([{}])

    assert result["checks_attempted"] == 0
    assert result["pools"][0]["security_check_status"] == (
        "skipped_pool_rejected"
    )
    mocked_check.assert_not_called()


def test_pool_without_mint_is_skipped():
    pool = make_pool(mint=None)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
    ) as mocked_check:
        result = scan_pool_security([{}])

    assert result["checks_attempted"] == 0
    assert result["pools"][0]["security_check_status"] == (
        "skipped_missing_mint"
    )
    mocked_check.assert_not_called()


def test_security_api_error_is_recorded():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=SecurityDataError("API unavailable"),
    ):
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert result["checks_attempted"] == 1
    assert result["checks_succeeded"] == 0
    assert result["checks_failed"] == 1
    assert item["security_check_status"] == "error"
    assert item["security_error"] == "API unavailable"


def test_wrong_token_report_is_rejected():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(mint="WRONG_TOKEN"),
    ):
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert result["checks_failed"] == 1
    assert item["security_check_status"] == "error"
    assert "does not match" in item["security_error"]


def test_run_limit_is_respected():
    pools = [
        make_pool(mint=f"TOKEN{i}")
        for i in range(12)
    ]

    def fake_check(mint):
        return make_security(mint=mint)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=fake_check,
    ) as mocked_check:
        result = scan_pool_security([{}], max_checks=10)

    assert result["checks_attempted"] == 10
    assert result["checks_succeeded"] == 10
    assert result["max_checks_per_run"] == 10
    assert mocked_check.call_count == 10

    assert all(
        item["security_check_status"] == "completed"
        for item in result["pools"][:10]
    )

    assert all(
        item["security_check_status"] == "skipped_run_limit"
        for item in result["pools"][10:]
    )


def test_invalid_run_limit_is_rejected():
    with pytest.raises(ValueError):
        scan_pool_security([], max_checks=-1)

    with pytest.raises(ValueError):
        scan_pool_security([], max_checks=1.5)

    with pytest.raises(ValueError):
        scan_pool_security([], max_checks=True)
      
