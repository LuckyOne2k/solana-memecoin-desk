
from desk.market_data import prepare_pools
from desk.security import (
    SecurityDataError,
    check_token_security,
)


MAX_SECURITY_CHECKS_PER_RUN = 10


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

    for item in prepared:
        item["security_check_status"] = "not_checked"

        if item["status"] == "rejected":
            item["security_check_status"] = "skipped_pool_rejected"
            continue

        mint = item.get("token_address")

        if not mint:
            item["security_check_status"] = "skipped_missing_mint"
            continue

        if checks_attempted >= max_checks:
            item["security_check_status"] = "skipped_run_limit"
            continue

        checks_attempted += 1

        try:
            security = check_token_security(mint)

        except SecurityDataError as error:
            checks_failed += 1
            item["security_check_status"] = "error"
            item["security_error"] = str(error)
            continue

        if security.get("mint") != mint:
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
        "max_checks_per_run": max_checks,
    }
