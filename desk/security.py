
import time
from datetime import datetime, timezone

import requests


RUGCHECK_URL = "https://api.rugcheck.xyz/v1"
REQUEST_INTERVAL_SECONDS = 1.1

_last_request_at = 0.0


class SecurityDataError(Exception):
    pass


def _wait_for_rate_limit():
    global _last_request_at

    elapsed = time.monotonic() - _last_request_at
    remaining = REQUEST_INTERVAL_SECONDS - elapsed

    if remaining > 0:
        time.sleep(remaining)

    _last_request_at = time.monotonic()


def _get_json(url, timeout=25):
    _wait_for_rate_limit()

    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()

    except requests.RequestException as error:
        raise SecurityDataError(
            f"RugCheck request failed: {error}"
        ) from error

    except ValueError as error:
        raise SecurityDataError(
            "RugCheck returned invalid JSON"
        ) from error

    if not isinstance(data, dict):
        raise SecurityDataError(
            "RugCheck returned an unexpected response"
        )

    return data


def fetch_rugcheck_report(mint):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    url = f"{RUGCHECK_URL}/tokens/{mint}/report"
    report = _get_json(url)

    if report.get("mint") != mint:
        raise SecurityDataError(
            "RugCheck report does not match the requested mint"
        )

    return report


def fetch_rugcheck_summary(mint):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    url = f"{RUGCHECK_URL}/tokens/{mint}/report/summary"
    return _get_json(url)


def _number(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_rugcheck_report(report, summary):
    if not isinstance(report, dict):
        raise ValueError("Report must be a dictionary")

    if not isinstance(summary, dict):
        raise ValueError("Summary must be a dictionary")

    token = report.get("token")
    if not isinstance(token, dict):
        token = {}

    holders = report.get("topHolders")
    if not isinstance(holders, list):
        holders = []

    creator = report.get("creator")

    owner_balances = {}
    creator_share = None
    largest_holder_pct = None

    for holder in holders:
        if not isinstance(holder, dict):
            continue

        owner = holder.get("owner") or holder.get("address")
        pct = _number(holder.get("pct"))

        if not owner or pct is None or pct < 0:
            continue

        owner_balances[owner] = (
            owner_balances.get(owner, 0.0) + pct
        )

        if creator and owner == creator:
            creator_share = (
                (creator_share or 0.0) + pct
            )

    if owner_balances:
        largest_holder_pct = max(owner_balances.values())

    top10_sample_pct = None

    if len(owner_balances) >= 10:
        top10_sample_pct = sum(
            sorted(
                owner_balances.values(),
                reverse=True
            )[:10]
        )

    mint_authority = token.get("mintAuthority")
    freeze_authority = token.get("freezeAuthority")

    mint_revoked = (
        mint_authority is None
        if "mintAuthority" in token
        else None
    )

    freeze_revoked = (
        freeze_authority is None
        if "freezeAuthority" in token
        else None
    )

    lp_locked_pct = _number(
        summary.get("lpLockedPct")
    )

    lp_burned_or_locked = (
        lp_locked_pct >= 99.99
        if lp_locked_pct is not None
        else None
    )

    return {
        "mint": report.get("mint"),
        "creator_address": creator,
        "mint_authority_revoked": mint_revoked,
        "freeze_authority_revoked": freeze_revoked,
        "lp_locked_pct": lp_locked_pct,
        "lp_burned_or_locked": lp_burned_or_locked,
        "largest_reported_holder_pct": largest_holder_pct,
        "creator_share_sampled_pct": creator_share,
        "top10_share_sampled_pct": top10_sample_pct,
        "top_holder_accounts_returned": len(holders),
        "distinct_reported_owners": len(owner_balances),
        "top10_share_fully_verified": False,
        "rugcheck_score": report.get("score"),
        "rugcheck_score_normalised": report.get(
            "score_normalised"
        ),
        "rugcheck_risks": report.get("risks", []),
        "checked_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def check_token_security(mint):
    report = fetch_rugcheck_report(mint)
    summary = fetch_rugcheck_summary(mint)

    return analyze_rugcheck_report(
        report,
        summary
    )
