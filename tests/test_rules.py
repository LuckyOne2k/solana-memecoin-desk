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
