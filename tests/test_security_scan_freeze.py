from unittest.mock import patch

from desk.security_scan import scan_pool_security


def make_pool():
    return {
        "token_address": "TOKEN123",
        "status": "incomplete",
        "rejection_reasons": [],
        "missing_checks": [
            "mint_authority_revoked",
            "freeze_authority_revoked",
            "lp_burned_or_locked",
            "top10_share_pct",
            "dev_share_pct",
        ],
    }


def make_security(freeze_revoked=True):
    return {
        "mint": "TOKEN123",
        "mint_authority_revoked": True,
        "freeze_authority_revoked": freeze_revoked,
        "lp_burned_or_locked": True,
        "creator_address": "CREATOR",
    }


def make_holder_snapshot():
    return {
        "mint": "TOKEN123",
        "top10_observed_owner_share_lower_bound_pct": 10.0,
        "creator_share_lower_bound_pct": 1.0,
        "top10_share_fully_verified": False,
        "data_scope": "largest_20_token_accounts_sample",
    }


def test_confirmed_revoked_freeze_authority_removes_missing_check():
    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(True),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ):
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert item["freeze_authority_revoked"] is True
    assert "freeze_authority_revoked" not in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_active_freeze_authority_rejects_pool():
    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(False),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert item["status"] == "rejected"
    assert "freeze_authority_not_revoked" in item["rejection_reasons"]
    assert item["holder_check_status"] == "skipped_security_rejection"
    mocked_holder.assert_not_called()


def test_unknown_freeze_authority_stays_incomplete():
    security = make_security(None)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=security,
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ):
        result = scan_pool_security([{}])

    item = result["pools"][0]

    assert item["freeze_authority_revoked"] is None
    assert "freeze_authority_revoked" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_duplicate_token_reuses_freeze_authority_result():
    pools = [make_pool(), make_pool()]

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(True),
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ):
        result = scan_pool_security([{}, {}])

    assert mocked_security.call_count == 1

    for item in result["pools"]:
        assert item["freeze_authority_revoked"] is True
        assert "freeze_authority_revoked" not in item["missing_checks"]
      
