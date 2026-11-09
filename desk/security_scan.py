"""Einzige Prüfkette für Token-Sicherheit, Liquidität, Holder und
Marktkapitalisierung.

Ablauf pro Pool:
1. Marktdaten-Vorprüfung (desk.market_data.prepare_pools), inkl.
   Ausschluss von Stablecoins und Staking-Token
2. RugCheck: Mint-/Freeze-Authority, LP-Sperre, Pool-Kennzeichnungen
3. Liquidität: DexPaprika gegen RugCheck
4. Solana-RPC: Holder-Stichprobe (nur ohne vorherige Ablehnung)
5. Holder-Verteilung (desk.holders) und Marktkapitalisierung
   (desk.market_cap)

Externe Daten werden je Token nur einmal abgefragt; bewertet wird je
Pool. Alle Grenzwerte kommen aus config.yaml.
"""

from desk.data_quality import CONFIRMED, REJECTED, check_liquidity
from desk.holders import evaluate_holders
from desk.market_cap import evaluate_market_cap
from desk.market_data import prepare_pools
from desk.security import (
    SecurityDataError,
    check_token_security,
)
from desk.solana_rpc import (
    SolanaRpcError,
    fetch_holder_snapshot,
)


def _status(item):
    if item["rejection_reasons"]:
        return "rejected"
    if item["missing_checks"]:
        return "incomplete"
    return "ready_for_full_risk_screen"


def _reject(item, reason):
    if reason not in item["rejection_reasons"]:
        item["rejection_reasons"].append(reason)


def _resolve(item, check):
    if check in item["missing_checks"]:
        item["missing_checks"].remove(check)


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


def _fetch_security(mint, risk_filters, counters):
    """RugCheck-Abfrage, genau einmal je Token."""
    counters["checks_attempted"] += 1

    try:
        security = check_token_security(
            mint,
            lp_locked_min_pct=risk_filters["lp_locked_min_pct"],
        )

    except SecurityDataError as error:
        counters["checks_failed"] += 1
        return {"security_error": str(error)}

    if (
        not isinstance(security, dict)
        or security.get("mint") != mint
    ):
        counters["checks_failed"] += 1
        return {
            "security_error": (
                "Security report does not match the requested token"
            )
        }

    counters["checks_succeeded"] += 1
    return {"security": security}


def _fetch_holders(mint, security, counters):
    """Holder-Stichprobe von der Blockchain, genau einmal je Token."""
    counters["holder_checks_attempted"] += 1

    try:
        snapshot = fetch_holder_snapshot(
            mint,
            creator_address=security.get("creator_address"),
        )

    except SolanaRpcError as error:
        counters["holder_checks_failed"] += 1
        return {"holder_error": str(error)}

    if (
        not isinstance(snapshot, dict)
        or snapshot.get("mint") != mint
    ):
        counters["holder_checks_failed"] += 1
        return {
            "holder_error": (
                "Holder snapshot does not match the requested token"
            )
        }

    counters["holder_checks_succeeded"] += 1
    return {"holder_snapshot": snapshot}


def _evaluate_item(item, data, config, counters):
    risk_filters = config["risk_filters"]
    mint = item["token_address"]

    if "security_error" in data:
        item["security_check_status"] = "error"
        item["security_error"] = data["security_error"]
        item["holder_check_status"] = "not_checked"
        return

    security = data["security"]

    item["security_check_status"] = "completed"
    item["security"] = security
    item["mint_authority_revoked"] = security.get("mint_authority_revoked")
    item["freeze_authority_revoked"] = security.get("freeze_authority_revoked")
    item["lp_burned_or_locked"] = security.get("lp_burned_or_locked")

    rejections, resolved = _evaluate_security(security, risk_filters)

    for reason in rejections:
        _reject(item, reason)

    for check in resolved:
        _resolve(item, check)

    # Liquidität: DexPaprika gegen RugCheck (gleicher Pool)
    market_liquidity = security.get("market_liquidity_usd_by_pool")

    liquidity = check_liquidity(
        dexpaprika_liquidity_usd=item.get("liquidity_usd"),
        rugcheck_market_liquidity_usd=(
            market_liquidity.get(item.get("pool_address"))
            if isinstance(market_liquidity, dict)
            else None
        ),
        min_liquidity_usd=risk_filters["reject_liquidity_below_usd"],
        max_deviation_pct=config["data_quality"]["max_liquidity_deviation_pct"],
    )
    item["liquidity_check"] = liquidity

    if liquidity["status"] == CONFIRMED:
        _resolve(item, "liquidity_cross_check")
    elif liquidity["status"] == REJECTED:
        _resolve(item, "liquidity_cross_check")
        _reject(item, "liquidity_below_minimum_cross_checked")

    if item["rejection_reasons"]:
        item["holder_check_status"] = "skipped_security_rejection"
        return

    if "holder_snapshot" not in data and "holder_error" not in data:
        data.update(_fetch_holders(mint, security, counters))

    if "holder_error" in data:
        item["holder_check_status"] = "error"
        item["holder_error"] = data["holder_error"]
        return

    snapshot = data["holder_snapshot"]

    item["holder_check_status"] = "completed"
    item["holder_snapshot"] = {
        key: value
        for key, value in snapshot.items()
        if key != "accounts"
    }

    holders = evaluate_holders(
        snapshot=snapshot,
        security=security,
        pool_address=item.get("pool_address"),
        risk_filters=risk_filters,
    )
    item["holder_evaluation"] = holders

    for reason in holders["rejection_reasons"]:
        _reject(item, reason)

    if holders["top10_status"] in ("confirmed", "rejected"):
        _resolve(item, "top10_share_pct")

    if holders["dev_status"] in ("confirmed", "rejected"):
        _resolve(item, "dev_share_pct")

    # Marktkapitalisierung
    if "market_cap_usd" in item["missing_checks"]:
        market_cap = evaluate_market_cap(
            config=config,
            reported_market_cap_usd=item.get("market_cap_usd_reported"),
            geckoterminal_price_usd=item.get("price_usd_geckoterminal"),
            rugcheck_price_usd=security.get("rugcheck_price_usd"),
            total_supply_raw=snapshot.get("supply_amount_raw"),
            supply_decimals=snapshot.get("supply_decimals"),
            burned_amount_raw=holders.get("burned_amount_raw", 0),
        )
        item["market_cap_evaluation"] = market_cap
        item["market_cap_usd"] = market_cap["market_cap_usd"]
        item["market_cap_basis"] = market_cap["basis"]

        if market_cap["status"] == CONFIRMED:
            _resolve(item, "market_cap_usd")
        elif market_cap["status"] == REJECTED:
            _resolve(item, "market_cap_usd")
            _reject(item, market_cap["reason"])


def scan_pool_security(pools, config, max_checks=None):
    """Vorprüfung plus Sicherheits-, Liquiditäts-, Holder- und
    Marktkapitalisierungs-Prüfung aller Pools.

    max_checks begrenzt die Zahl der geprüften Token pro Lauf. Ohne
    Angabe gilt scanner.max_security_checks_per_run aus config.yaml.
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

    data_by_mint = {}

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
        if mint not in data_by_mint:
            if counters["checks_attempted"] >= max_checks:
                item["security_check_status"] = "skipped_run_limit"
                item["holder_check_status"] = "skipped_run_limit"
                continue

            data_by_mint[mint] = _fetch_security(
                mint,
                risk_filters,
                counters,
            )

        _evaluate_item(item, data_by_mint[mint], config, counters)
        item["status"] = _status(item)

    return {
        "pools": prepared,
        **counters,
        "max_checks_per_run": max_checks,
    }
