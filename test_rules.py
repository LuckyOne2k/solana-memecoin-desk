from desk.rules import Candidate, screen_candidate

CFG = {
 "scanner":{"pair_age_hours_min":30,"pair_age_hours_max":72,"market_cap_usd_min":300000,"market_cap_usd_max":20000000},
 "risk_filters":{"require_mint_authority_revoked":True,"require_lp_burned_or_locked":True,
 "reject_liquidity_below_usd":40000,"reject_top10_share_above_pct":25,"reject_dev_share_above_pct":3}
}
def test_candidate_passes():
 c=Candidate(48,1_000_000,True,True,80_000,20,1)
 assert screen_candidate(c,CFG).accepted
def test_unknown_authority_is_rejected():
 c=Candidate(48,1_000_000,None,True,80_000,20,1)
 assert not screen_candidate(c,CFG).accepted
def test_concentration_is_rejected():
 c=Candidate(48,1_000_000,True,True,80_000,30,1)
 assert not screen_candidate(c,CFG).accepted
