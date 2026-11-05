
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

    checked_tokens = {}

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

        # Bereits geprüfte Token nicht erneut abfragen.
        if mint in checked_tokens:
            cached = checked_tokens[mint]

            for key in (
                "security",
                "holder_snapshot",
                "security_error",
                "holder_error",
                "mint_authority_revoked",
                "freeze_authority_revoked",
                "lp_burned_or_locked",
                "top10_observed_share_lower_bound_pct",
                "creator_observed_share_lower_bound_pct",
            ):
                if key in cached:
                    item[key] = cached[key]

            item["security_check_status"] = (
                cached["security_check_status"]
            )
            item["holder_check_status"] = (
                cached["holder_check_status"]
            )

            item["rejection_reasons"] = list(
                dict.fromkeys(
                    item.get("rejection_reasons", [])
                    + cached.get("added_rejection_reasons", [])
                )
            )

            item["missing_checks"] = [
                check
                for check in item.get("missing_checks", [])
                if check not in cached.get("removed_missing_checks", [])
            ]

            if cached.get("status") == "rejected":
                item["status"] = "rejected"
            elif item["rejection_reasons"]:
                item["status"] = "rejected"
            elif item["missing_checks"]:
                item["status"] = "incomplete"
            else:
                item["status"] = "ready_for_full_risk_screen"

            continue

        if checks_attempted >= max_checks:
            item["security_check_status"] = "skipped_run_limit"
            item["holder_check_status"] = "skipped_run_limit"
            continue

        checks_attempted += 1

        original_rejections = set(
            item.get("rejection_reasons", [])
        )
        original_missing = set(
            item.get("missing_checks", [])
        )

        try:
            security = check_token_security(mint)

        except SecurityDataError as error:
            checks_failed += 1
            item["security_check_status"] = "error"
            item["security_error"] = str(error)

            checked_tokens[mint] = {
                "security_check_status": "error",
                "holder_check_status": "not_checked",
                "security_error": str(error),
                "added_rejection_reasons": [],
                "removed_missing_checks": [],
                "status": item["status"],
            }
            continue

        if (
            not isinstance(security, dict)
            or security.get("mint") != mint
        ):
            checks_failed += 1
            item["security_check_status"] = "error"
            item["security_error"] = (
                "Security report does not match the requested token"
            )

            checked_tokens[mint] = {
                "security_check_status": "error",
                "holder_check_status": "not_checked",
                "security_error": item["security_error"],
                "added_rejection_reasons": [],
                "removed_missing_checks": [],
                "status": item["status"],
            }
            continue

        checks_succeeded += 1
        item["security_check_status"] = "completed"
        item["security"] = security

        mint_revoked = security.get("mint_authority_revoked")
        freeze_revoked = security.get("freeze_authority_revoked")
        lp_locked = security.get("lp_burned_or_locked")

        item["mint_authority_revoked"] = mint_revoked
        item["freeze_authority_revoked"] = freeze_revoked
        item["lp_burned_or_locked"] = lp_locked

        # Mint Authority bewerten.
        if mint_revoked is True:
            if "mint_authority_revoked" in item["missing_checks"]:
                item["missing_checks"].remove(
                    "mint_authority_revoked"
                )
        elif mint_revoked is False:
            item["rejection_reasons"].append(
                "mint_authority_not_confirmed_revoked"
            )

        # Freeze Authority bewerten.
        if freeze_revoked is True:
            if "freeze_authority_revoked" in item["missing_checks"]:
                item["missing_checks"].remove(
                    "freeze_authority_revoked"
                )
        elif freeze_revoked is False:
            item["rejection_reasons"].append(
                "freeze_authority_not_revoked"
            )

        # Liquidity-Pool-Sicherung bewerten.
        if lp_locked is True:
            if "lp_burned_or_locked" in item["missing_checks"]:
                item["missing_checks"].remove(
                    "lp_burned_or_locked"
                )
        elif lp_locked is False:
            item["rejection_reasons"].append(
                "lp_burn_or_lock_not_verified"
            )

        holder_snapshot = None
        holder_error = None
        holder_status = "not_checked"

        if item["rejection_reasons"]:
            item["status"] = "rejected"
            holder_status = "skipped_security_rejection"

        else:
            holder_checks_attempted += 1
            holder_status = "checking"

            try:
                holder_snapshot = fetch_holder_snapshot(
                    mint,
                    creator_address=security.get("creator_address"),
                )

            except SolanaRpcError as error:
                holder_checks_failed += 1
                holder_status = "error"
                holder_error = str(error)
                item["holder_error"] = holder_error

            else:
                if (
                    not isinstance(holder_snapshot, dict)
                    or holder_snapshot.get("mint") != mint
                ):
                    holder_checks_failed += 1
                    holder_status = "error"
                    holder_error = (
                        "Holder snapshot does not match the requested token"
                    )
                    item["holder_error"] = holder_error

                else:
                    holder_checks_succeeded += 1
                    holder_status = "completed"
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

                    # Eine Stichprobe kann eine Überschreitung beweisen,
                    # aber keine vollständige Verteilung bestätigen.
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

        item["holder_check_status"] = holder_status

        if item["rejection_reasons"]:
            item["status"] = "rejected"
        elif item["missing_checks"]:
            item["status"] = "incomplete"
        else:
            item["status"] = "ready_for_full_risk_screen"

        added_rejections = [
            reason
            for reason in item["rejection_reasons"]
            if reason not in original_rejections
        ]

        removed_missing = [
            check
            for check in original_missing
            if check not in item["missing_checks"]
        ]

        checked_tokens[mint] = {
            "security_check_status": item["security_check_status"],
            "holder_check_status": holder_status,
            "security": security,
            "holder_snapshot": holder_snapshot,
            "added_rejection_reasons": added_rejections,
            "removed_missing_checks": removed_missing,
            "status": item["status"],
            "mint_authority_revoked": mint_revoked,
            "freeze_authority_revoked": freeze_revoked,
            "lp_burned_or_locked": lp_locked,
            "top10_observed_share_lower_bound_pct": item.get(
                "top10_observed_share_lower_bound_pct"
            ),
            "creator_observed_share_lower_bound_pct": item.get(
                "creator_observed_share_lower_bound_pct"
            ),
            "holder_error": holder_error,
        }

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
