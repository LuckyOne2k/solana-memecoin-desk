
from desk.market_data import prepare_pools
from desk.security import (
    SecurityDataError,
    check_token_security,
)
from desk.solana_rpc import (
    SolanaRpcError,
    fetch_holder_snapshot,
)


MAX_SECURITY_CHECKS_PER_RUN = 10

MAX_TOP10_SHARE_PCT = 25.0
MAX_CREATOR_SHARE_PCT = 3.0


def scan_pool_security(
    pools,
    max_checks=MAX_SECURITY_CHECKS_PER_RUN,
):
    if not isinstance(max_checks, int) or isinstance(max_checks, bool):
        raise ValueError("max_checks must be an integer")

    if max_checks < 0:
        raise ValueError("max_checks cannot be negative")

    prepared = prepare_pools(pools)

    checks_attempted = 0
    checks_succeeded = 0
    checks_failed = 0

    holder_checks_attempted = 0
    holder_checks_succeeded = 0
    holder_checks_failed = 0

    for item in prepared:
        item["security_check_status"] = "not_checked"
        item["holder_check_status"] = "not_checked"

        if item["status"] == "rejected":
            item["security_check_status"] = "skipped_pool_rejected"
            item["holder_check_status"] = "skipped_pool_rejected"
            continue

        mint = item.get("token_address")

        if not mint:
            item["security_check_status"] = "skipped_missing_mint"
            item["holder_check_status"] = "skipped_missing_mint"
            continue

        if checks_attempted >= max_checks:
            item["security_check_status"] = "skipped_run_limit"
            item["holder_check_status"] = "skipped_run_limit"
            continue

        checks_attempted += 1

        try:
            security = check_token_security(mint)

        except SecurityDataError as error:
            checks_failed += 1
            item["security_check_status"] = "error"
            item["security_error"] = str(error)
            continue

        if not isinstance(security, dict) or security.get("mint") != mint:
            checks_failed += 1
            item["security_check_status"] = "error"
            item["security_error"] = (
                "Security report does not match the requested token"
            )
            continue

        checks_succeeded += 1
        item["security_check_status"] = "completed"
        item["security"] = security

        mint_revoked = security.get("mint_authority_revoked")
        lp_locked = security.get("lp_burned_or_locked")

        item["mint_authority_revoked"] = mint_revoked
        item["lp_burned_or_locked"] = lp_locked

        if mint_revoked is True:
            if "mint_authority_revoked" in item["missing_checks"]:
                item["missing_checks"].remove("mint_authority_revoked")

        elif mint_revoked is False:
            item["rejection_reasons"].append(
                "mint_authority_not_confirmed_revoked"
            )

        if lp_locked is True:
            if "lp_burned_or_locked" in item["missing_checks"]:
                item["missing_checks"].remove("lp_burned_or_locked")

        elif lp_locked is False:
            item["rejection_reasons"].append(
                "lp_burn_or_lock_not_verified"
            )

        # Keine Holder-RPC-Anfragen für bereits abgelehnte Tokens.
        if item["rejection_reasons"]:
            item["status"] = "rejected"
            item["holder_check_status"] = "skipped_security_rejection"
            continue

        holder_checks_attempted += 1
        item["holder_check_status"] = "checking"

        try:
            holder_snapshot = fetch_holder_snapshot(
                mint,
                creator_address=security.get("creator_address"),
            )

        except SolanaRpcError as error:
            holder_checks_failed += 1
            item["holder_check_status"] = "error"
            item["holder_error"] = str(error)

        else:
            if (
                not isinstance(holder_snapshot, dict)
                or holder_snapshot.get("mint") != mint
            ):
                holder_checks_failed += 1
                item["holder_check_status"] = "error"
                item["holder_error"] = (
                    "Holder snapshot does not match the requested token"
                )

            else:
                holder_checks_succeeded += 1
                item["holder_check_status"] = "completed"
                item["holder_snapshot"] = holder_snapshot

                top10_lower_bound = holder_snapshot.get(
                    "top10_observed_owner_share_lower_bound_pct"
                )

                creator_lower_bound = holder_snapshot.get(
                    "creator_share_lower_bound_pct"
                )

                item["top10_observed_share_lower_bound_pct"] = (
                    top10_lower_bound
                )

                item["creator_observed_share_lower_bound_pct"] = (
                    creator_lower_bound
                )

                if (
                    isinstance(top10_lower_bound, (int, float))
                    and not isinstance(top10_lower_bound, bool)
                    and top10_lower_bound > MAX_TOP10_SHARE_PCT
                ):
                    item["rejection_reasons"].append(
                        "observed_top10_holder_share_above_limit"
                    )

                if (
                    isinstance(creator_lower_bound, (int, float))
                    and not isinstance(creator_lower_bound, bool)
                    and creator_lower_bound > MAX_CREATOR_SHARE_PCT
                ):
                    item["rejection_reasons"].append(
                        "observed_creator_share_above_limit"
                    )

        if item["rejection_reasons"]:
            item["status"] = "rejected"

        elif item["missing_checks"]:
            item["status"] = "incomplete"

        else:
            item["status"] = "ready_for_full_risk_screen"

    return {
        "pools": prepared,
        "checks_attempted": checks_attempted,
        "checks_succeeded": checks_succeeded,
        "checks_failed": checks_failed,
        "holder_checks_attempted": holder_checks_attempted,
        "holder_checks_succeeded": holder_checks_succeeded,
        "holder_checks_failed": holder_checks_failed,
        "max_checks_per_run": max_checks,
    }
