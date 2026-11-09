"""Einzige Prüfkette für Token-Sicherheit und Holder-Verteilung.

Ablauf pro Lauf:
1. Marktdaten-Vorprüfung (desk.market_data.prepare_pools)
2. RugCheck: Mint-/Freeze-Authority, LP-Sperre
3. Solana-RPC: Holder-Stichprobe (nur wenn Schritt 2 nichts ablehnt)

Alle Grenzwerte kommen aus config.yaml.
"""

from desk.market_data import prepare_pools
from desk.security import (
    SecurityDataError,
    check_token_security,
)
from desk.solana_rpc import (
    SolanaRpcError,
    fetch_holder_snapshot,
)


# Felder, die aus einem Prüfergebnis auf jeden Pool desselben Tokens
# übertragen werden.
_OUTCOME_FIELDS = (
    "security",
    "holder_snapshot",
    "security_error",
    "holder_error",
    "mint_authority_revoked",
    "freeze_authority_revoked",
    "lp_burned_or_locked",
    "top10_observed_share_lower_bound_pct",
    "creator_observed_share_lower_bound_pct",
)


def _is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _status(item):
    if item["rejection_reasons"]:
        return "rejected"
    if item["missing_checks"]:
        return "incomplete"
    return "ready_for_full_risk_screen"


def _evaluate_security(security, risk_filters):
    """Bewertet das RugCheck-Ergebnis.

    Gibt (Ablehnungsgründe, geklärte Prüfungen) zurück. Eine Prüfung
    gilt als geklärt, sobald ein eindeutiges Ja oder Nein vorliegt.
    Unbekannte Werte (None) bleiben offen.
    """
    rejections = []
    resolved = []

    mint_revoked = security.get("mint_authority_revoked")
    freeze_revoked = security.get("freeze_authority_revoked")
    lp_locked = security.get("lp_burned_or_locked")

    if mint_revoked is True:
        resolved.append("mint_authority_revoked")
    elif mint_revoked is False:
        resolved.append("mint_authority_revoked")
        if risk_filters["require_mint_authority_revoked"]:
            rejections.append("mint_authority_not_confirmed_revoked")

    # Für die Freeze Authority gibt es keinen Schalter in config.yaml.
    # Eine aktive Freeze Authority führt immer zur Ablehnung.
    if freeze_revoked is True:
        resolved.append("freeze_authority_revoked")
    elif freeze_revoked is False:
        resolved.append("freeze_authority_revoked")
        rejections.append("freeze_authority_not_revoked")

    if lp_locked is True:
        resolved.append("lp_burned_or_locked")
    elif lp_locked is False:
        resolved.append("lp_burned_or_locked")
        if risk_filters["require_lp_burned_or_locked"]:
            rejections.append("lp_burn_or_lock_not_verified")

    return rejections, resolved


def _evaluate_holders(snapshot, risk_filters):
    """Bewertet die Holder-Stichprobe.

    Eine Stichprobe kann eine Überschreitung beweisen, aber keine
    vollständige Verteilung bestätigen. Deshalb werden top10_share_pct
    und dev_share_pct hier nie als geklärt markiert.
    """
    rejections = []

    top10 = snapshot.get("top10_observed_owner_share_lower_bound_pct")
    creator = snapshot.get("creator_share_lower_bound_pct")

    if (
        _is_number(top10)
        and top10 > risk_filters["reject_top10_share_above_pct"]
    ):
        rejections.append("observed_top10_holder_share_above_limit")

    if (
        _is_number(creator)
        and creator > risk_filters["reject_dev_share_above_pct"]
    ):
        rejections.append("observed_creator_share_above_limit")

    return rejections


def _check_token(mint, risk_filters, counters):
    """Führt alle externen Prüfungen für ein Token genau einmal aus."""
    outcome = {
        "security_check_status": "error",
        "holder_check_status": "not_checked",
        "added_rejection_reasons": [],
        "resolved_checks": [],
    }

    counters["checks_attempted"] += 1

    try:
        security = check_token_security(
            mint,
            lp_locked_min_pct=risk_filters["lp_locked_min_pct"],
        )

    except SecurityDataError as error:
        counters["checks_failed"] += 1
        outcome["security_error"] = str(error)
        return outcome

    if (
        not isinstance(security, dict)
        or security.get("mint") != mint
    ):
        counters["checks_failed"] += 1
        outcome["security_error"] = (
            "Security report does not match the requested token"
        )
        return outcome

    counters["checks_succeeded"] += 1

    outcome["security_check_status"] = "completed"
    outcome["security"] = security
    outcome["mint_authority_revoked"] = security.get(
        "mint_authority_revoked"
    )
    outcome["freeze_authority_revoked"] = security.get(
        "freeze_authority_revoked"
    )
    outcome["lp_burned_or_locked"] = security.get(
        "lp_burned_or_locked"
    )

    rejections, resolved = _evaluate_security(security, risk_filters)

    outcome["added_rejection_reasons"].extend(rejections)
    outcome["resolved_checks"].extend(resolved)

    if rejections:
        outcome["holder_check_status"] = "skipped_security_rejection"
        return outcome

    counters["holder_checks_attempted"] += 1

    try:
        snapshot = fetch_holder_snapshot(
            mint,
            creator_address=security.get("creator_address"),
        )

    except SolanaRpcError as error:
        counters["holder_checks_failed"] += 1
        outcome["holder_check_status"] = "error"
        outcome["holder_error"] = str(error)
        return outcome

    if (
        not isinstance(snapshot, dict)
        or snapshot.get("mint") != mint
    ):
        counters["holder_checks_failed"] += 1
        outcome["holder_check_status"] = "error"
        outcome["holder_error"] = (
            "Holder snapshot does not match the requested token"
        )
        return outcome

    counters["holder_checks_succeeded"] += 1

    outcome["holder_check_status"] = "completed"
    outcome["holder_snapshot"] = snapshot
    outcome["top10_observed_share_lower_bound_pct"] = snapshot.get(
        "top10_observed_owner_share_lower_bound_pct"
    )
    outcome["creator_observed_share_lower_bound_pct"] = snapshot.get(
        "creator_share_lower_bound_pct"
    )
    outcome["added_rejection_reasons"].extend(
        _evaluate_holders(snapshot, risk_filters)
    )

    return outcome


def _apply_outcome(item, outcome):
    for key in _OUTCOME_FIELDS:
        if key in outcome:
            item[key] = outcome[key]

    item["security_check_status"] = outcome["security_check_status"]
    item["holder_check_status"] = outcome["holder_check_status"]

    item["rejection_reasons"] = list(
        dict.fromkeys(
            item.get("rejection_reasons", [])
            + outcome["added_rejection_reasons"]
        )
    )

    item["missing_checks"] = [
        check
        for check in item.get("missing_checks", [])
        if check not in outcome["resolved_checks"]
    ]

    item["status"] = _status(item)


def scan_pool_security(pools, config, max_checks=None):
    """Vorprüfung plus Sicherheits- und Holder-Prüfung aller Pools.

    max_checks begrenzt die externen Abfragen pro Lauf. Ohne Angabe gilt
    scanner.max_security_checks_per_run aus config.yaml.
    """
    if max_checks is None:
        max_checks = config["scanner"]["max_security_checks_per_run"]

    if not isinstance(max_checks, int) or isinstance(max_checks, bool):
        raise ValueError("max_checks must be an integer")

    if max_checks < 0:
        raise ValueError("max_checks cannot be negative")

    risk_filters = config["risk_filters"]

    prepared = prepare_pools(pools, config)

    counters = {
        "checks_attempted": 0,
        "checks_succeeded": 0,
        "checks_failed": 0,
        "holder_checks_attempted": 0,
        "holder_checks_succeeded": 0,
        "holder_checks_failed": 0,
    }

    outcomes_by_mint = {}

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
        if mint not in outcomes_by_mint:
            if counters["checks_attempted"] >= max_checks:
                item["security_check_status"] = "skipped_run_limit"
                item["holder_check_status"] = "skipped_run_limit"
                continue

            outcomes_by_mint[mint] = _check_token(
                mint,
                risk_filters,
                counters,
            )

        _apply_outcome(item, outcomes_by_mint[mint])

    return {
        "pools": prepared,
        **counters,
        "max_checks_per_run": max_checks,
    }
