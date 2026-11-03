from dataclasses import dataclass
from typing import Optional

@dataclass
class Candidate:
    pair_age_hours: float
    market_cap_usd: float
    mint_authority_revoked: Optional[bool]
    lp_burned_or_locked: Optional[bool]
    liquidity_usd: float
    top10_share_pct: float
    dev_share_pct: float

@dataclass
class FilterResult:
    accepted: bool
    reasons: list[str]

def screen_candidate(c: Candidate, cfg: dict) -> FilterResult:
    s = cfg["scanner"]
    r = cfg["risk_filters"]
    reasons = []

    if not s["pair_age_hours_min"] <= c.pair_age_hours <= s["pair_age_hours_max"]:
        reasons.append("pair_age_out_of_range")
    if not s["market_cap_usd_min"] <= c.market_cap_usd <= s["market_cap_usd_max"]:
        reasons.append("market_cap_out_of_range")
    if r["require_mint_authority_revoked"] and c.mint_authority_revoked is not True:
        reasons.append("mint_authority_not_confirmed_revoked")
    if r["require_lp_burned_or_locked"] and c.lp_burned_or_locked is not True:
        reasons.append("lp_burn_or_lock_not_verified")
    if c.liquidity_usd < r["reject_liquidity_below_usd"]:
        reasons.append("liquidity_below_minimum")
    if c.top10_share_pct > r["reject_top10_share_above_pct"]:
        reasons.append("top10_concentration_too_high")
    if c.dev_share_pct > r["reject_dev_share_above_pct"]:
        reasons.append("developer_share_too_high")

    return FilterResult(accepted=not reasons, reasons=reasons)
