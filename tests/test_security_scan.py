import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from desk.security import SecurityDataError
from desk.config import load_config
from desk.security_scan import scan_pool_security


CONFIG = load_config()
from desk.solana_rpc import SolanaRpcError


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


def make_security(
    mint="TOKEN123",
    mint_revoked=True,
    lp_locked=True,
    creator_address="CREATOR",
):
    return {
        "mint": mint,
        "mint_authority_revoked": mint_revoked,
        "lp_burned_or_locked": lp_locked,
        "creator_address": creator_address,
    }


def make_holder_snapshot(
    mint="TOKEN123",
    top10_share=10.0,
    creator_share=None,
):
    return {
        "mint": mint,
        "top10_observed_owner_share_lower_bound_pct": top10_share,
        "creator_share_lower_bound_pct": creator_share,
        "top10_share_fully_verified": False,
        "data_scope": "largest_20_token_accounts_sample",
    }


def test_successful_security_and_holder_checks_update_pool():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert result["checks_attempted"] == 1
    assert result["checks_succeeded"] == 1
    assert result["checks_failed"] == 0

    assert result["holder_checks_attempted"] == 1
    assert result["holder_checks_succeeded"] == 1
    assert result["holder_checks_failed"] == 0

    assert item["security_check_status"] == "completed"
    assert item["holder_check_status"] == "completed"

    assert item["mint_authority_revoked"] is True
    assert item["lp_burned_or_locked"] is True

    assert "mint_authority_revoked" not in item["missing_checks"]
    assert "lp_burned_or_locked" not in item["missing_checks"]

    assert "top10_share_pct" in item["missing_checks"]
    assert "dev_share_pct" in item["missing_checks"]

    assert item["status"] == "incomplete"

    mocked_security.assert_called_once_with(
        "TOKEN123",
        lp_locked_min_pct=CONFIG["risk_filters"]["lp_locked_min_pct"],
    )
    mocked_holder.assert_called_once_with(
        "TOKEN123",
        creator_address="CREATOR",
    )


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
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    assert result["checks_attempted"] == 0
    assert result["holder_checks_attempted"] == 0

    assert result["pools"][0]["security_check_status"] == (
        "skipped_pool_rejected"
    )

    mocked_security.assert_not_called()
    mocked_holder.assert_not_called()


def test_pool_without_mint_is_skipped():
    pool = make_pool(mint=None)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    assert result["checks_attempted"] == 0
    assert result["holder_checks_attempted"] == 0

    assert result["pools"][0]["security_check_status"] == (
        "skipped_missing_mint"
    )

    mocked_security.assert_not_called()
    mocked_holder.assert_not_called()


def test_security_api_error_is_recorded():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=SecurityDataError("API unavailable"),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert result["checks_attempted"] == 1
    assert result["checks_failed"] == 1
    assert result["holder_checks_attempted"] == 0

    assert item["security_check_status"] == "error"
    assert item["security_error"] == "API unavailable"

    mocked_holder.assert_not_called()


def test_wrong_token_report_is_rejected():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(mint="WRONG_TOKEN"),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert result["checks_failed"] == 1
    assert item["security_check_status"] == "error"
    assert "does not match" in item["security_error"]

    mocked_holder.assert_not_called()


def test_holder_rpc_error_is_recorded_without_passing_check():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=SolanaRpcError("RPC unavailable"),
    ):
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert result["checks_succeeded"] == 1
    assert result["holder_checks_attempted"] == 1
    assert result["holder_checks_failed"] == 1

    assert item["holder_check_status"] == "error"
    assert item["holder_error"] == "RPC unavailable"

    assert "top10_share_pct" in item["missing_checks"]
    assert "dev_share_pct" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_holder_snapshot_with_wrong_mint_is_rejected():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(mint="WRONG_TOKEN"),
    ):
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert result["holder_checks_failed"] == 1
    assert item["holder_check_status"] == "error"
    assert "does not match" in item["holder_error"]
    assert item["status"] == "incomplete"


def test_top10_lower_bound_above_limit_rejects_pool():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(top10_share=26.0),
    ):
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert item["status"] == "rejected"
    assert "observed_top10_holder_share_above_limit" in (
        item["rejection_reasons"]
    )


def test_creator_lower_bound_above_limit_rejects_pool():
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(creator_share=3.5),
    ):
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert item["status"] == "rejected"
    assert "observed_creator_share_above_limit" in (
        item["rejection_reasons"]
    )


def test_run_limit_is_respected_for_both_checks():
    pools = [
        make_pool(mint=f"TOKEN{i}")
        for i in range(12)
    ]

    def fake_security(mint, **kwargs):
        return make_security(mint=mint)

    def fake_holder(mint, creator_address=None):
        return make_holder_snapshot(mint=mint)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=fake_security,
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=fake_holder,
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG, max_checks=10)

    assert result["checks_attempted"] == 10
    assert result["checks_succeeded"] == 10

    assert result["holder_checks_attempted"] == 10
    assert result["holder_checks_succeeded"] == 10

    assert result["max_checks_per_run"] == 10

    assert mocked_security.call_count == 10
    assert mocked_holder.call_count == 10

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
        scan_pool_security([], CONFIG, max_checks=-1)

    with pytest.raises(ValueError):
        scan_pool_security([], CONFIG, max_checks=1.5)

    with pytest.raises(ValueError):
        scan_pool_security([], CONFIG, max_checks=True)


@pytest.mark.parametrize(
    "security_kwargs, expected_reason",
    [
        (
            {"mint_revoked": False},
            "mint_authority_not_confirmed_revoked",
        ),
        (
            {"lp_locked": False},
            "lp_burn_or_lock_not_verified",
        ),
    ],
)
def test_security_rejection_skips_holder_rpc(
    security_kwargs,
    expected_reason,
):
    pool = make_pool()

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[pool],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(**security_kwargs),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert item["status"] == "rejected"
    assert expected_reason in item["rejection_reasons"]
    assert item["holder_check_status"] == "skipped_security_rejection"

    assert result["holder_checks_attempted"] == 0
    mocked_holder.assert_not_called()


def test_duplicate_token_mints_are_not_checked_twice():
    pools = [
        make_pool(mint="TOKEN_A"),
        make_pool(mint="TOKEN_A"),
        make_pool(mint="TOKEN_B"),
    ]

    def fake_security(mint, **kwargs):
        return make_security(mint=mint)

    def fake_holder(mint, creator_address=None):
        return make_holder_snapshot(mint=mint)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=fake_security,
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=fake_holder,
    ) as mocked_holder:
        result = scan_pool_security([{}], CONFIG, max_checks=2)

    assert result["checks_attempted"] == 2
    assert result["holder_checks_attempted"] == 2

    assert mocked_security.call_count == 2
    assert [
        call.args[0] for call in mocked_security.call_args_list
    ] == ["TOKEN_A", "TOKEN_B"]

    assert mocked_holder.call_count == 2
    assert [
        call.args[0] for call in mocked_holder.call_args_list
    ] == ["TOKEN_A", "TOKEN_B"]


def test_default_run_limit_comes_from_config():
    pools = [
        make_pool(mint=f"TOKEN{i}")
        for i in range(15)
    ]

    def fake_security(mint, **kwargs):
        return make_security(mint=mint)

    def fake_holder(mint, creator_address=None):
        return make_holder_snapshot(mint=mint)

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=fake_security,
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        side_effect=fake_holder,
    ):
        result = scan_pool_security([{}], CONFIG)

    expected = CONFIG["scanner"]["max_security_checks_per_run"]

    assert result["max_checks_per_run"] == expected
    assert result["checks_attempted"] == expected


def test_holder_limits_come_from_config():
    config = copy.deepcopy(CONFIG)
    config["risk_filters"]["reject_top10_share_above_pct"] = 50

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(top10_share=26.0),
    ):
        result = scan_pool_security([{}], config)

    item = result["pools"][0]

    assert "observed_top10_holder_share_above_limit" not in (
        item["rejection_reasons"]
    )


def test_require_flags_from_config_are_respected():
    config = copy.deepcopy(CONFIG)
    config["risk_filters"]["require_mint_authority_revoked"] = False
    config["risk_filters"]["require_lp_burned_or_locked"] = False

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(mint_revoked=False, lp_locked=False),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ):
        result = scan_pool_security([{}], config)

    item = result["pools"][0]

    assert item["rejection_reasons"] == []
    assert item["holder_check_status"] == "completed"


def test_definite_negative_answer_is_not_listed_as_missing():
    with patch(
        "desk.security_scan.prepare_pools",
        return_value=[make_pool()],
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(lp_locked=False),
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
    ):
        result = scan_pool_security([{}], CONFIG)

    item = result["pools"][0]

    assert "lp_burn_or_lock_not_verified" in item["rejection_reasons"]
    assert "lp_burned_or_locked" not in item["missing_checks"]


def test_duplicate_token_with_security_error_is_not_retried():
    pools = [make_pool(), make_pool()]

    with patch(
        "desk.security_scan.prepare_pools",
        return_value=pools,
    ), patch(
        "desk.security_scan.check_token_security",
        side_effect=SecurityDataError("API unavailable"),
    ) as mocked_security:
        result = scan_pool_security([{}, {}], CONFIG)

    assert mocked_security.call_count == 1

    for item in result["pools"]:
        assert item["security_check_status"] == "error"
        assert item["security_error"] == "API unavailable"
        assert item["status"] == "incomplete"


def test_full_chain_with_real_market_data_preparation():
    """Vorprüfung und Sicherheitsprüfung laufen als eine Kette."""
    pool = {
        "id": "pool-address",
        "created_at": (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(),
        "liquidity_usd": 50000,
        "volume_usd_24h": 12000,
        "transactions_24h": 80,
        "tokens": [
            {"id": "So11111111111111111111111111111111111111112"},
            {"id": "TOKEN123", "name": "Example", "symbol": "EX"},
        ],
    }

    with patch(
        "desk.market_data.fetch_token_market_caps",
        return_value={"token123": 500000.0},
    ), patch(
        "desk.security_scan.check_token_security",
        return_value={
            **make_security(),
            "freeze_authority_revoked": True,
        },
    ) as mocked_security, patch(
        "desk.security_scan.fetch_holder_snapshot",
        return_value=make_holder_snapshot(),
    ):
        result = scan_pool_security([pool], CONFIG)

    item = result["pools"][0]

    mocked_security.assert_called_once()
    assert item["security_check_status"] == "completed"
    assert item["holder_check_status"] == "completed"
    assert item["missing_checks"] == ["top10_share_pct", "dev_share_pct"]
    assert item["status"] == "incomplete"
