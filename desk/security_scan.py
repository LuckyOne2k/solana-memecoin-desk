"""Einzige Prüfkette für Token-Sicherheit, Liquidität, Holder und
Marktkapitalisierung.

Ablauf pro Pool:
1. Marktdaten-Vorprüfung (desk.market_data.prepare_pools), inkl.
   Ausschluss von Stablecoins und Staking-Token
2. RugCheck: Mint-/Freeze-Authority, LP-Sperre, Pool-Kennzeichnungen
3. Liquidität: DexPaprika gegen RugCheck
4. Solana-RPC (nur ohne vorherige Ablehnung):
   a) Mint-Info: Token-Programm und Token-2022-Erweiterungen
      (Pflichtprüfungen permanent_delegate_absent und
      token_extensions_safe; nie Ablehnung, nur offen)
   b) vollständige Holder-Liste (fetch_full_holder_list)
   c) nur wenn b) fehlschlägt: Rückfall auf die Top-20-Stichprobe.
      Die Stichprobe kann nur ablehnen oder "unklar" liefern, nie
      bestätigen.
5. Holder-Verteilung (desk.holders) und Marktkapitalisierung
   (desk.market_cap)

Externe Daten werden je Token nur einmal abgefragt; bewertet wird je
Pool. Alle Grenzwerte kommen aus config.yaml.

Welche Token das Kontingent (max_checks) erhalten, legt
desk.check_queue fest: Token, die zuletzt abgelehnt wurden, rücken nach
hinten. Das ändert nur die Reihenfolge, nie das Ergebnis einer Prüfung.
"""

from desk.check_queue import order_check_mints
from desk.data_quality import CONFIRMED, REJECTED, check_liquidity
from desk.holders import evaluate_holders
from desk.market_cap import evaluate_market_cap
from desk.market_data import prepare_pools
from desk.security import (
    SecurityDataError,
    check_token_security,
)
from desk.solana_rpc import (
    ABSENT,
    PRESENT,
    HolderListError,
    SolanaRpcError,
    fetch_full_holder_list,
    fetch_holder_snapshot,
    fetch_mint_info,
)


FULL_LIST_SCOPE = "full_token_account_list"
SAMPLE_SCOPE = "largest_20_token_accounts_sample"

# Pflichtprüfungen aus der Mint-Info. Werden nur geklärt, nie abgelehnt
# (eine Ablehnungsregel für Permanent Delegate folgt gesondert).
TOKEN_CHECKS = ("permanent_delegate_absent", "token_extensions_safe")

# Token-2022-Erweiterungen, die ausdrücklich als unbedenklich eingestuft
# sind: reine Metadaten- und Gruppenangaben ohne Einfluss auf Bestände,
# Überweisungen oder Rechte. Jede andere Erweiterung (auch bekannte wie
# transferFeeConfig, transferHook, pausableConfig, defaultAccountState,
# nonTransferable, confidentialTransferMint, interestBearingConfig,
# scaledUiAmountConfig, mintCloseAuthority, permanentDelegate) und jede
# unbekannte oder nicht lesbare Erweiterung -> Prüfung bleibt offen.
SAFE_TOKEN_2022_EXTENSIONS = frozenset(
    {
        "metadataPointer",
        "tokenMetadata",
        "groupPointer",
        "tokenGroup",
        "groupMemberPointer",
        "tokenGroupMember",
    }
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


def _fetch_mint(mint, counters):
    """Mint-Info (Token-Programm, Erweiterungen), genau einmal je Token."""
    counters["mint_info_checks_attempted"] += 1

    try:
        info = fetch_mint_info(mint)

    except HolderListError as error:
        counters["mint_info_checks_failed"] += 1
        return {"mint_info_error": error.reason}

    except SolanaRpcError:
        counters["mint_info_checks_failed"] += 1
        return {"mint_info_error": "rpc_error"}

    if not isinstance(info, dict) or info.get("mint") != mint:
        counters["mint_info_checks_failed"] += 1
        return {"mint_info_error": "mint_info_mismatch"}

    return {"mint_info": info}


def _valid_full_list(snapshot, mint, mint_info):
    return (
        isinstance(snapshot, dict)
        and snapshot.get("mint") == mint
        and snapshot.get("data_scope") == FULL_LIST_SCOPE
        and snapshot.get("token_program") == mint_info.get("token_program")
        and isinstance(snapshot.get("accounts"), list)
    )


def _fetch_full_list(mint, data, holder_config, counters):
    """Vollständige Holder-Liste -> (Snapshot, None) oder (None, Grund)."""
    if "mint_info" not in data:
        return None, "mint_info_unavailable"

    counters["full_holder_list_attempted"] += 1

    try:
        snapshot = fetch_full_holder_list(
            mint,
            data["mint_info"],
            max_pages=holder_config["max_pages"],
            max_slot_span=holder_config["max_slot_span"],
        )

    except HolderListError as error:
        counters["full_holder_list_failed"] += 1
        return None, error.reason

    except SolanaRpcError:
        counters["full_holder_list_failed"] += 1
        return None, "rpc_error"

    if not _valid_full_list(snapshot, mint, data["mint_info"]):
        counters["full_holder_list_failed"] += 1
        return None, "full_list_response_invalid"

    counters["full_holder_list_succeeded"] += 1
    return snapshot, None


def _fetch_holders(mint, security, data, holder_config, counters):
    """Holder-Daten, genau einmal je Token: vollständige Liste, nur bei
    deren Fehlschlag die Top-20-Stichprobe."""
    counters["holder_checks_attempted"] += 1

    snapshot, full_error = _fetch_full_list(
        mint, data, holder_config, counters
    )

    if snapshot is not None:
        counters["holder_checks_succeeded"] += 1
        return {"holder_snapshot": snapshot, "holder_source": "full_holder_list"}

    counters["holder_fallbacks"] += 1
    fallback = {
        "holder_source": "sample_fallback",
        "full_holder_list_error": full_error,
    }

    try:
        sample = fetch_holder_snapshot(
            mint,
            creator_address=security.get("creator_address"),
        )

    except SolanaRpcError as error:
        counters["holder_checks_failed"] += 1
        return {**fallback, "holder_error": str(error)}

    if (
        not isinstance(sample, dict)
        or sample.get("mint") != mint
    ):
        counters["holder_checks_failed"] += 1
        return {
            **fallback,
            "holder_error": (
                "Holder snapshot does not match the requested token"
            ),
        }

    counters["holder_checks_succeeded"] += 1

    # Der Rückfall ist immer eine Stichprobe, egal was die Antwort
    # behauptet: Sie kann in desk.holders nie bestätigen.
    sample = {**sample, "data_scope": SAMPLE_SCOPE}
    return {**fallback, "holder_snapshot": sample}


def _evaluate_mint_info(item, data):
    """Token-Programm und Erweiterungen: klärt TOKEN_CHECKS oder lässt
    sie offen. Nie Ablehnung, nie bloßes Protokollieren bei Risiko."""
    if "mint_info_error" in data:
        item["mint_info_status"] = "error"
        item["mint_info_error"] = data["mint_info_error"]
        item["token_check_reasons"] = ["mint_info_unavailable"]
        return

    info = data["mint_info"]
    names = info.get("token2022_extensions")
    delegate_status = info.get("permanent_delegate_status")
    reasons = []

    item["mint_info_status"] = "completed"
    item["token_program"] = info.get("token_program")
    item["token2022_extensions"] = names
    item["unknown_token_extensions"] = info.get("unknown_extensions")
    item["permanent_delegate_status"] = delegate_status
    item["permanent_delegate"] = info.get("permanent_delegate")

    if delegate_status == ABSENT:
        _resolve(item, "permanent_delegate_absent")
    elif delegate_status == PRESENT:
        reasons.append("permanent_delegate_present")
    else:
        reasons.append("permanent_delegate_unknown")

    if info.get("extensions_readable") is not True or not isinstance(names, list):
        reasons.append("token_extensions_unreadable")
    else:
        unsafe = sorted(
            {name for name in names if name not in SAFE_TOKEN_2022_EXTENSIONS}
        )

        if unsafe:
            reasons.append("token_extension_not_classified_safe")
            item["unsafe_token_extensions"] = unsafe
        else:
            _resolve(item, "token_extensions_safe")

    item["token_check_reasons"] = reasons


def _evaluate_item(item, data, config, counters):
    risk_filters = config["risk_filters"]
    mint = item["token_address"]

    # Neue Pflichtprüfungen sind immer offen, bis die Mint-Info sie klärt.
    for check in TOKEN_CHECKS:
        if check not in item["missing_checks"]:
            item["missing_checks"].append(check)

    item["mint_info_status"] = "not_checked"

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
        item["mint_info_status"] = "skipped_security_rejection"
        return

    if "mint_info" not in data and "mint_info_error" not in data:
        data.update(_fetch_mint(mint, counters))

    _evaluate_mint_info(item, data)

    if "holder_snapshot" not in data and "holder_error" not in data:
        data.update(
            _fetch_holders(mint, security, data, config["holders"], counters)
        )

    item["holder_source"] = data.get("holder_source")

    if data.get("full_holder_list_error") is not None:
        item["full_holder_list_error"] = data["full_holder_list_error"]

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
        # Nur ein bestätigter oder bewiesener Wert; die Obergrenze steht
        # getrennt in market_cap_upper_bound_usd.
        item["market_cap_usd"] = market_cap["market_cap_usd"]
        item["market_cap_upper_bound_usd"] = market_cap[
            "market_cap_upper_bound_usd"
        ]
        item["market_cap_basis"] = market_cap["basis"]

        if market_cap["status"] == CONFIRMED:
            _resolve(item, "market_cap_usd")
        elif market_cap["status"] == REJECTED:
            _resolve(item, "market_cap_usd")
            _reject(item, market_cap["reason"])


def scan_pool_security(pools, config, max_checks=None, check_history=None):
    """Vorprüfung plus Sicherheits-, Liquiditäts-, Holder- und
    Marktkapitalisierungs-Prüfung aller Pools.

    max_checks begrenzt die Zahl der geprüften Token pro Lauf. Ohne
    Angabe gilt scanner.max_security_checks_per_run aus config.yaml.

    check_history (desk.check_queue.load_check_history) legt nur fest,
    welche Token zuerst geprüft werden. Ohne Historie gilt die
    bisherige Reihenfolge.
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
        "mint_info_checks_attempted": 0,
        "mint_info_checks_failed": 0,
        "full_holder_list_attempted": 0,
        "full_holder_list_succeeded": 0,
        "full_holder_list_failed": 0,
        "holder_fallbacks": 0,
    }

    # Prüfreihenfolge: eindeutige Token, die überhaupt geprüft würden.
    candidates = []

    for item in prepared:
        mint = item.get("token_address")

        if item["status"] != "rejected" and mint and mint not in candidates:
            candidates.append(mint)

    queue = order_check_mints(candidates, check_history)
    selected = {mint for mint, _, _ in queue[:max_checks]}
    queue_info = {
        mint: (position, reason, previous)
        for position, (mint, reason, previous) in enumerate(queue, start=1)
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

        position, reason, previous = queue_info[mint]
        item["check_queue_position"] = position
        item["queue_reason"] = reason
        item["previous_rejection_at"] = previous

        # Nicht ausgewählt: bleibt ungeprüft und damit "incomplete".
        if mint not in selected:
            item["security_check_status"] = "skipped_run_limit"
            item["holder_check_status"] = "skipped_run_limit"
            continue

        # Bereits geprüfte Token nicht erneut abfragen.
        if mint not in data_by_mint:

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
        "check_order_source": (
            check_history.get("source")
            if isinstance(check_history, dict)
            else "not_provided"
        ),
        "deprioritized_tokens": sum(
            1 for _, reason, _ in queue if reason == "rejected_in_previous_scan"
        ),
    }
