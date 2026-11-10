"""Einzige Prüfkette für Token-Sicherheit, Liquidität, Holder und
Marktkapitalisierung.

Ablauf pro Pool:
1. Marktdaten-Vorprüfung (desk.market_data.prepare_pools), inkl.
   Ausschluss von Stablecoins und Staking-Token
2. RugCheck: Mint-/Freeze-Authority, LP-Sperre, Pool-Kennzeichnungen
3. Liquidität: DexPaprika gegen RugCheck
4. Solana-RPC (nur ohne vorherige Ablehnung):
   a) Mint-Info: Token-Programm, Token-2022-Erweiterungen und
      Gegenprobe der Mint-/Freeze-Authority zu RugCheck (Pflicht-
      prüfungen permanent_delegate_absent, token_extensions_safe,
      authority_cross_check). Abgelehnt wird nur bei eindeutig
      belegtem Risiko einer Erweiterung (classify_token_extension);
      Abweichung, unbekannte oder unlesbare Daten -> offen. Nach einer
      Ablehnung keine weiteren RPC-Abfragen für diesen Pool.
   b) vollständige Holder-Liste (fetch_full_holder_list)
   c) nur wenn b) fehlschlägt: Rückfall auf die Top-20-Stichprobe.
      Die Stichprobe kann nur ablehnen oder "unklar" liefern, nie
      bestätigen.
5. Holder-Verteilung (desk.holders) und Marktkapitalisierung
   (desk.market_cap)
6. Nur mit market_cap_onchain_bounds.allow_onchain_bounds (Standard an,
   false schaltet ab) und nur, wenn allein die Marktkapitalisierung
   offen ist: eigene
   On-Chain-Spanne aus heuristischer Wallet-Untergrenze
   (desk.circulating_bounds) und
   Obergrenze. Jede Lücke -> bleibt unvollständig.

Externe Daten werden je Token nur einmal abgefragt; bewertet wird je
Pool. Alle Grenzwerte kommen aus config.yaml.

Welche Token das Kontingent (max_checks) erhalten, legt
desk.check_queue fest: Token, die zuletzt abgelehnt wurden, rücken nach
hinten. Das ändert nur die Reihenfolge, nie das Ergebnis einer Prüfung.
"""

from desk.check_queue import order_check_mints
from desk.circulating_bounds import COMPLETED, compute_wallet_lower_bound
from desk.data_quality import CONFIRMED, REJECTED, check_liquidity
from desk.holders import evaluate_holders
from desk.market_cap import (
    ONCHAIN_BOUNDS,
    TWO_SOURCES_AGREE,
    evaluate_market_cap,
    evaluate_onchain_bounds,
    onchain_target_amount_raw,
)
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
    fetch_authority_accounts,
    fetch_frozen_token_accounts,
    fetch_full_holder_list,
    fetch_holder_snapshot,
    fetch_mint_info,
)


FULL_LIST_SCOPE = "full_token_account_list"
SAMPLE_SCOPE = "largest_20_token_accounts_sample"

# Pflichtprüfungen aus der Mint-Info. Bleiben offen, bis die Mint-Info
# sie eindeutig klärt. Ablehnungen kommen nur aus
# classify_token_extension (eindeutig belegtes Risiko).
TOKEN_CHECKS = (
    "permanent_delegate_absent",
    "token_extensions_safe",
    "authority_cross_check",
)

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


EXTENSION_SAFE = "safe"
EXTENSION_REJECT = "reject"
EXTENSION_UNCLEAR = "unclear"


def _address(value):
    return isinstance(value, str) and bool(value.strip())


def _fee_bps(fee):
    """Gebühr in Basispunkten oder None, wenn nicht eindeutig lesbar."""
    if not isinstance(fee, dict):
        return None

    bps = fee.get("transferFeeBasisPoints")

    if isinstance(bps, bool) or not isinstance(bps, int) or bps < 0:
        return None

    return bps


def classify_token_extension(name, state):
    """Bewertet eine Token-2022-Erweiterung des Mints.

    Gibt (Entscheidung, Grund) zurück. "reject" nur bei eindeutig
    belegtem Risiko (Feld vorhanden, Wert eindeutig). "safe" nur für die
    Positivliste SAFE_TOKEN_2022_EXTENSIONS. Alles andere (fehlende oder
    falsch typisierte Felder, unbekannte, nicht lesbare oder nicht
    ausdrücklich freigegebene Erweiterungen) -> "unclear", nie Freigabe.
    """
    if name in SAFE_TOKEN_2022_EXTENSIONS:
        return EXTENSION_SAFE, "classified_safe"

    # Kein Zustand nötig: Token ist grundsätzlich nicht übertragbar.
    if name == "nonTransferable":
        return EXTENSION_REJECT, "non_transferable"

    if not isinstance(state, dict):
        return EXTENSION_UNCLEAR, "extension_state_unreadable"

    if name == "permanentDelegate":
        if _address(state.get("delegate")):
            return EXTENSION_REJECT, "permanent_delegate_active"
        return EXTENSION_UNCLEAR, "permanent_delegate_not_active_unverified"

    if name == "pausableConfig":
        if _address(state.get("authority")):
            return EXTENSION_REJECT, "pausable_authority_active"
        if state.get("paused") is True:
            return EXTENSION_REJECT, "pausable_paused"
        return EXTENSION_UNCLEAR, "pausable_not_active_unverified"

    if name == "defaultAccountState":
        if state.get("accountState") == "frozen":
            return EXTENSION_REJECT, "default_account_state_frozen"
        return EXTENSION_UNCLEAR, "default_account_state_not_classified_safe"

    if name == "transferHook":
        if _address(state.get("programId")):
            return EXTENSION_REJECT, "transfer_hook_program_active"
        if _address(state.get("authority")):
            return EXTENSION_REJECT, "transfer_hook_authority_active"
        return EXTENSION_UNCLEAR, "transfer_hook_not_active_unverified"

    if name == "transferFeeConfig":
        if _address(state.get("transferFeeConfigAuthority")):
            return EXTENSION_REJECT, "transfer_fee_authority_active"
        for key in ("olderTransferFee", "newerTransferFee"):
            bps = _fee_bps(state.get(key))
            if bps is not None and bps > 0:
                return EXTENSION_REJECT, "transfer_fee_nonzero"
        return EXTENSION_UNCLEAR, "transfer_fee_zero_unverified"

    return EXTENSION_UNCLEAR, "token_extension_not_classified_safe"


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


def _evaluate_authorities(item, info, security, reasons):
    """Gegenprobe Mint-/Freeze-Authority: RPC gegen RugCheck. Geklärt nur,
    wenn beide Quellen für beide Authorities eindeutig übereinstimmen;
    Abweichung oder fehlender Wert -> offen (nie Freigabe)."""
    pairs = (
        (
            "mint_authority",
            info.get("mint_authority_revoked_onchain"),
            security.get("mint_authority_revoked"),
        ),
        (
            "freeze_authority",
            info.get("freeze_authority_revoked_onchain"),
            security.get("freeze_authority_revoked"),
        ),
    )
    agree = True

    for name, onchain, rugcheck in pairs:
        item[f"{name}_revoked_onchain"] = onchain

        if onchain not in (True, False) or rugcheck not in (True, False):
            reasons.append(f"{name}_cross_check_data_missing")
            agree = False
        elif onchain != rugcheck:
            reasons.append(f"{name}_sources_disagree")
            agree = False

    if agree:
        _resolve(item, "authority_cross_check")


def _evaluate_mint_info(item, data, security):
    """Token-Programm, Erweiterungen und Authority-Gegenprobe: klärt
    TOKEN_CHECKS, lässt sie offen oder lehnt bei eindeutig belegtem
    Risiko einer Erweiterung ab."""
    if "mint_info_error" in data:
        item["mint_info_status"] = "error"
        item["mint_info_error"] = data["mint_info_error"]
        item["token_check_reasons"] = ["mint_info_unavailable"]
        return

    info = data["mint_info"]
    names = info.get("token2022_extensions")
    states = info.get("token2022_extension_states")
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

        # Zustände gehören eindeutig zu den Namen; sonst keine Bewertung
        # (bleibt offen, kann aber nicht ablehnen).
        state_names = (
            [entry.get("extension") for entry in states if isinstance(entry, dict)]
            if isinstance(states, list)
            else None
        )

        if state_names != names:
            if unsafe:
                reasons.append("token_extension_states_unreadable")
        else:
            findings = []

            for entry in states:
                decision, reason = classify_token_extension(
                    entry["extension"], entry.get("state")
                )

                if decision == EXTENSION_SAFE:
                    continue

                findings.append(
                    {
                        "extension": entry["extension"],
                        "decision": decision,
                        "reason": reason,
                    }
                )

                if decision == EXTENSION_REJECT:
                    _reject(item, f"token_extension_{reason}")

            if findings:
                item["token_extension_findings"] = findings

        if not unsafe:
            _resolve(item, "token_extensions_safe")

    _evaluate_authorities(item, info, security, reasons)

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

    _evaluate_mint_info(item, data, security)

    if item["rejection_reasons"]:
        item["holder_check_status"] = "skipped_token_rejection"
        return

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
        elif market_cap["reason"] == "circulating_supply_not_confirmed":
            _evaluate_onchain_bounds(
                item, data, config, counters, security, snapshot,
                holders, market_cap,
            )


def _onchain_precondition_error(item, security, holders, market_cap):
    """Grund, warum die On-Chain-Spanne nicht versucht wird; sonst None.

    Alle anderen Prüfungen müssen bestätigt sein (spart Credits: sonst
    wäre der Pool ohnehin nicht bereit). Ausdrücklich zusätzlich: Mint-
    und Freeze-Authority widerrufen, kein Permanent Delegate, vollständige
    Holder-Liste, zwei übereinstimmende Preise. Sonst könnten neue Token
    erzeugt, Konten eingefroren oder Bestände bewegt werden.
    """
    if item["rejection_reasons"]:
        return "already_rejected"

    if item["missing_checks"] != ["market_cap_usd"]:
        return "other_checks_open"

    if security.get("mint_authority_revoked") is not True:
        return "mint_authority_not_revoked"

    if security.get("freeze_authority_revoked") is not True:
        return "freeze_authority_not_revoked"

    if item.get("mint_info_status") != "completed":
        return "mint_info_unavailable"

    if item.get("permanent_delegate_status") != ABSENT:
        return "permanent_delegate_not_absent"

    if item.get("holder_source") != "full_holder_list":
        return "full_holder_list_required"

    if (
        holders.get("data_scope") != FULL_LIST_SCOPE
        or holders.get("is_complete_distribution") is not True
        or holders.get("unseen_amount_raw") != 0
    ):
        return "full_holder_list_incomplete"

    if holders.get("top10_status") != CONFIRMED or holders.get("dev_status") != CONFIRMED:
        return "holder_checks_not_confirmed"

    price = market_cap.get("price") or {}

    if price.get("status") != CONFIRMED or price.get("reason") != TWO_SOURCES_AGREE:
        return "two_agreeing_prices_required"

    return None


def _evaluate_onchain_bounds(
    item, data, config, counters, security, snapshot, holders, market_cap
):
    """Eigene On-Chain-Spanne. Bestätigt nur bei vollständig belegten
    Grenzen; jede Lücke lässt die Prüfung offen."""
    settings = config.get("market_cap_onchain_bounds") or {}

    if settings.get("allow_onchain_bounds") is not True:
        item["market_cap_onchain_bounds"] = {"status": "disabled"}
        return

    precondition = _onchain_precondition_error(
        item, security, holders, market_cap
    )

    if precondition is not None:
        item["market_cap_onchain_bounds"] = {
            "status": "skipped",
            "reason": precondition,
        }
        return

    price = market_cap["price"]
    prices = (price.get("geckoterminal_price_usd"), price.get("rugcheck_price_usd"))
    price_low, price_high = min(prices), max(prices)

    supply = snapshot["supply_amount_raw"]
    upper_amount = supply - holders.get("burned_amount_raw", 0)
    decimals = snapshot.get("supply_decimals")

    target = onchain_target_amount_raw(
        config=config,
        price_low_usd=price_low,
        price_high_usd=price_high,
        upper_amount_raw=upper_amount,
        decimals=decimals,
    )

    if target is None:
        item["market_cap_onchain_bounds"] = {
            "status": "skipped",
            "reason": "onchain_bounds_input_invalid",
        }
        return

    # Einmal je Token (mehrere Pools desselben Tokens teilen das Ergebnis).
    if "onchain_lower_bound" not in data:
        counters["onchain_bounds_attempted"] += 1
        lower = compute_wallet_lower_bound(
            mint=item["token_address"],
            snapshot=snapshot,
            security=security,
            pool_addresses=data.get("pool_addresses") or (),
            target_amount_raw=target,
            max_calls=settings["max_account_calls_per_token"],
            max_slot_span=settings["max_slot_span"],
            fetch_accounts=fetch_authority_accounts,
            fetch_frozen=fetch_frozen_token_accounts,
        )
        counters["authority_account_calls"] += lower["account_calls"]
        counters["frozen_account_checks"] += lower["frozen_check_calls"]

        if lower["status"] != COMPLETED:
            counters["onchain_bounds_failed"] += 1

        data["onchain_lower_bound"] = lower

    lower = data["onchain_lower_bound"]
    log = {"status": lower["status"], "wallet_like_lower_bound": lower}

    if lower["status"] != COMPLETED:
        log["decision"] = {"status": "unclear", "reason": lower["reason"]}
        item["market_cap_onchain_bounds"] = log
        return

    decision = evaluate_onchain_bounds(
        config=config,
        price_low_usd=price_low,
        price_high_usd=price_high,
        lower_amount_raw=lower["wallet_like_lower_bound_raw"],
        upper_amount_raw=upper_amount,
        decimals=decimals,
    )
    log["decision"] = decision
    item["market_cap_onchain_bounds"] = log

    if decision["status"] == CONFIRMED:
        _resolve(item, "market_cap_usd")
        item["market_cap_basis"] = ONCHAIN_BOUNDS
        item["market_cap_heuristic_lower_bound_usd"] = decision["market_cap_heuristic_lower_bound_usd"]
        market_cap.update(
            status=CONFIRMED,
            reason=decision["reason"],
            basis=ONCHAIN_BOUNDS,
        )
    elif decision["status"] == REJECTED:
        _resolve(item, "market_cap_usd")
        _reject(item, decision["reason"])
        item["market_cap_basis"] = ONCHAIN_BOUNDS
        market_cap.update(
            status=REJECTED,
            reason=decision["reason"],
            basis=ONCHAIN_BOUNDS,
        )


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
        "onchain_bounds_attempted": 0,
        "onchain_bounds_failed": 0,
        "authority_account_calls": 0,
        "frozen_account_checks": 0,
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

    # Alle DexPaprika-Pool-Adressen je Token: werden bei der
    # Wallet-Untergrenze gemeinsam herausgerechnet.
    pool_addresses = {}

    for item in prepared:
        mint = item.get("token_address")
        address = item.get("pool_address")

        if mint and isinstance(address, str) and address:
            pool_addresses.setdefault(mint, set()).add(address)

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
            data_by_mint[mint]["pool_addresses"] = sorted(
                pool_addresses.get(mint, ())
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
