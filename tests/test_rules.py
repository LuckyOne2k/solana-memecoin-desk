from desk.rules import Candidate, screen_candidate


CONFIG = {
    "scanner": {
        "pair_age_hours_min": 30,
        "pair_age_hours_max": 72,
        "market_cap_usd_min": 300000,
        "market_cap_usd_max": 20000000,
    },
    "risk_filters": {
        "require_mint_authority_revoked": True,
        "require_lp_burned_or_locked": True,
        "reject_liquidity_below_usd": 40000,
        "reject_top10_share_above_pct": 25,
        "reject_dev_share_above_pct": 3,
    },
}


def test_valid_candidate_is_accepted():
    candidate = Candidate(
        pair_age_hours=48,
        market_cap_usd=1000000,
        mint_authority_revoked=True,
        lp_burned_or_locked=True,
        liquidity_usd=100000,
        top10_share_pct=20,
        dev_share_pct=1,
    )

    result = screen_candidate(candidate, CONFIG)
    assert result.accepted is True


def test_invalid_candidate_is_rejected():
    candidate = Candidate(
        pair_age_hours=10,
        market_cap_usd=100000,
        mint_authority_revoked=False,
        lp_burned_or_locked=False,
        liquidity_usd=10000,
        top10_share_pct=40,
        dev_share_pct=8,
    )

    result = screen_candidate(candidate, CONFIG)
    assert result.accepted is False
    assert len(result.reasons) >= 5

def test_unknown_holder_values_are_rejected():
    candidate = Candidate(
        pair_age_hours=48,
        market_cap_usd=1000000,
        mint_authority_revoked=True,
        lp_burned_or_locked=True,
        liquidity_usd=100000,
        top10_share_pct=None,
        dev_share_pct=None,
    )

    result = screen_candidate(candidate, CONFIG)

    assert result.accepted is False
    assert "top10_concentration_unknown" in result.reasons
    assert "developer_share_unknown" in result.reasons


def test_known_holder_values_within_limits_are_accepted():
    candidate = Candidate(
        pair_age_hours=48,
        market_cap_usd=1000000,
        mint_authority_revoked=True,
        lp_burned_or_locked=True,
        liquidity_usd=100000,
        top10_share_pct=20,
        dev_share_pct=1,
    )

    result = screen_candidate(candidate, CONFIG)

    assert result.accepted is True
    assert result.reasons == []


def test_rules_work_with_project_config(desk_config):
    candidate = Candidate(
        pair_age_hours=48,
        market_cap_usd=1000000,
        mint_authority_revoked=True,
        lp_burned_or_locked=True,
        liquidity_usd=100000,
        top10_share_pct=20,
        dev_share_pct=1,
    )

    assert screen_candidate(candidate, desk_config).accepted is True


def test_rules_market_cap_range_comes_from_config(desk_config):
    candidate = Candidate(
        pair_age_hours=48,
        market_cap_usd=250000,
        mint_authority_revoked=True,
        lp_burned_or_locked=True,
        liquidity_usd=100000,
        top10_share_pct=20,
        dev_share_pct=1,
    )

    result = screen_candidate(candidate, desk_config)

    assert result.accepted is False
    assert "market_cap_out_of_range" in result.reasons

