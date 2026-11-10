
from desk.check_queue import load_check_history
from desk.config import ConfigError, load_config
from desk.logging_utils import log_event, log_file_path
from desk.market_data import (
    MarketDataError,
    empty_market_cap_diagnostics,
    fetch_pools_by_age,
)
from desk.security import MAX_LOGGED_MARKETS
from desk.security_scan import scan_pool_security


def _onchain_details(pool):
    """Eigene On-Chain-Spanne, kompakt. Die Untergrenze ist eine
    HEURISTISCHE Wallet-Untergrenze aus technisch wallet-ähnlichen
    Konten, keine nachgewiesene wirtschaftliche Umlaufmenge; das steht
    ausdrücklich in lower_bound_kind und den *_proven-Feldern."""
    bounds = pool.get("market_cap_onchain_bounds")

    if not isinstance(bounds, dict):
        return None

    lower = bounds.get("wallet_like_lower_bound") or {}
    decision = bounds.get("decision") or {}

    return {
        "status": bounds.get("status"),
        "reason": bounds.get("reason") or lower.get("reason"),
        "note": lower.get("note"),
        "lower_bound_kind": lower.get("lower_bound_kind"),
        "private_key_proven": lower.get("private_key_proven"),
        "free_transferability_proven": lower.get("free_transferability_proven"),
        "economic_circulating_supply_proven": lower.get(
            "economic_circulating_supply_proven"
        ),
        "residual_risk": lower.get("residual_risk"),
        "decision_status": decision.get("status"),
        "decision_reason": decision.get("reason"),
        "market_cap_heuristic_lower_bound_usd": decision.get("market_cap_heuristic_lower_bound_usd"),
        "market_cap_upper_bound_usd": decision.get("market_cap_upper_bound_usd"),
        "price_low_usd": decision.get("price_low_usd"),
        "price_high_usd": decision.get("price_high_usd"),
        "wallet_like_lower_bound_raw": lower.get("wallet_like_lower_bound_raw"),
        "target_amount_raw": lower.get("target_amount_raw"),
        "target_reached": lower.get("target_reached"),
        "account_calls": lower.get("account_calls"),
        "max_account_calls": lower.get("max_account_calls"),
        "authorities_queried": lower.get("authorities_queried"),
        "unqueried_candidates": lower.get("unqueried_candidates"),
        "classified": lower.get("classified"),
        "excluded_authorities": lower.get("excluded_authorities"),
        "frozen_accounts": lower.get("frozen_accounts"),
        "slot_span": lower.get("slot_span"),
    }


RUGCHECK_RISK_FIELDS = ("name", "level", "score", "value")
MAX_RUGCHECK_RISKS = 30
MAX_RISK_TEXT = 200


def _rugcheck_risks(pool):
    """RugCheck-Risikoliste, nur zur Protokollierung (keine Bewertung).
    Begrenzt auf feste Felder und Längen; None, wenn nicht vorhanden."""
    security = pool.get("security")
    risks = security.get("rugcheck_risks") if isinstance(security, dict) else None

    if not isinstance(risks, list):
        return None

    result = []

    for risk in risks[:MAX_RUGCHECK_RISKS]:
        if not isinstance(risk, dict):
            continue

        entry = {}

        for field in RUGCHECK_RISK_FIELDS:
            value = risk.get(field)

            if isinstance(value, str):
                entry[field] = value[:MAX_RISK_TEXT]
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                entry[field] = value

        result.append(entry)

    return result


def _lp_details(pool):
    """LP-Angaben nur zur Protokollierung, keine Bewertung. Die
    LP-Prüfung nutzt unverändert den tokenweiten Wert
    (token_lp_locked_pct gegen risk_filters.lp_locked_min_pct)."""
    security = pool.get("security")

    if not isinstance(security, dict):
        return None

    evaluated = pool.get("pool_address")
    markets = security.get("market_lp")
    entries = None
    evaluated_entry = None
    evaluated_position = None
    total = None
    truncated = None

    if isinstance(markets, list):
        readable = [market for market in markets if isinstance(market, dict)]
        total = len(readable)
        truncated = total > MAX_LOGGED_MARKETS
        entries = []

        for position, market in enumerate(readable, start=1):
            is_evaluated = (
                bool(evaluated)
                and market.get("pool_address") == evaluated
                and evaluated_entry is None
            )
            entry = {**market, "is_evaluated_pool": is_evaluated}

            if is_evaluated:
                evaluated_entry = entry
                evaluated_position = position

            # Erste MAX_LOGGED_MARKETS Märkte; der untersuchte Pool wird
            # immer zusätzlich ausgegeben, auch weiter hinten.
            if position <= MAX_LOGGED_MARKETS or is_evaluated:
                entries.append(entry)

    return {
        "basis": "token_wide_lp_locked_pct",
        "token_lp_locked_pct": security.get("lp_locked_pct"),
        "lp_burned_or_locked": security.get("lp_burned_or_locked"),
        "evaluated_pool_address": evaluated,
        "evaluated_pool_found_in_markets": evaluated_entry is not None,
        # 1-basierte Position in der RugCheck-Marktliste
        "evaluated_pool_market_position": evaluated_position,
        "evaluated_pool_lp_locked_pct": (
            evaluated_entry.get("lp_locked_pct") if evaluated_entry else None
        ),
        "evaluated_pool_lp_locked_usd": (
            evaluated_entry.get("lp_locked_usd") if evaluated_entry else None
        ),
        "markets_total_count": total,
        "markets_truncated": truncated,
        "markets": entries,
    }


def _check_details(pool):
    """Kompakte, nachvollziehbare Prüfdetails für das Protokoll."""
    liquidity = pool.get("liquidity_check") or {}
    holders = pool.get("holder_evaluation") or {}
    market_cap = pool.get("market_cap_evaluation") or {}
    price = market_cap.get("price") or {}

    return {
        "pool_address": pool.get("pool_address"),
        "dex_name": pool.get("dex_name"),
        "token_classification": pool.get("token_classification"),
        "token_classification_detail": pool.get(
            "token_classification_detail"
        ),
        "liquidity_check": {
            "status": liquidity.get("status"),
            "reason": liquidity.get("reason"),
            "dexpaprika_usd": liquidity.get("dexpaprika_liquidity_usd"),
            "rugcheck_usd": liquidity.get("rugcheck_market_liquidity_usd"),
            "deviation_pct": liquidity.get("deviation_pct"),
        },
        "token": {
            "mint_info_status": pool.get("mint_info_status"),
            "mint_info_error": pool.get("mint_info_error"),
            "token_program": pool.get("token_program"),
            "token2022_extensions": pool.get("token2022_extensions"),
            "unknown_token_extensions": pool.get("unknown_token_extensions"),
            "unsafe_token_extensions": pool.get("unsafe_token_extensions"),
            "permanent_delegate_status": pool.get("permanent_delegate_status"),
            "permanent_delegate": pool.get("permanent_delegate"),
            "token_extension_findings": pool.get("token_extension_findings"),
            "mint_authority_revoked_onchain": pool.get(
                "mint_authority_revoked_onchain"
            ),
            "freeze_authority_revoked_onchain": pool.get(
                "freeze_authority_revoked_onchain"
            ),
            "token_check_reasons": pool.get("token_check_reasons"),
        },
        "holders": {
            "holder_source": pool.get("holder_source"),
            "full_holder_list_error": pool.get("full_holder_list_error"),
            "data_scope": holders.get("data_scope"),
            "is_complete_distribution": holders.get("is_complete_distribution"),
            "top10_status": holders.get("top10_status"),
            "top10_share_lower_bound_pct": holders.get(
                "top10_share_lower_bound_pct"
            ),
            "top10_share_upper_bound_pct": holders.get(
                "top10_share_upper_bound_pct"
            ),
            "unseen_share_pct": holders.get("unseen_share_pct"),
            "pool_share_pct": holders.get("pool_share_pct"),
            "burned_amount_raw": holders.get("burned_amount_raw"),
            "counted_owners_in_sample": holders.get(
                "counted_owners_in_sample"
            ),
            "dev_status": holders.get("dev_status"),
            "dev_share_lower_bound_pct": holders.get(
                "dev_share_lower_bound_pct"
            ),
            "dev_share_upper_bound_pct": holders.get(
                "dev_share_upper_bound_pct"
            ),
            "unconfirmed_reasons": holders.get("unconfirmed_reasons"),
        },
        "market_cap": {
            "status": market_cap.get("status"),
            "reason": market_cap.get("reason"),
            "basis": market_cap.get("basis"),
            "market_cap_usd": market_cap.get("market_cap_usd"),
            "market_cap_upper_bound_usd": market_cap.get(
                "market_cap_upper_bound_usd"
            ),
            "reported_market_cap_usd": pool.get("market_cap_usd_reported"),
            "fdv_onchain_usd": market_cap.get("fdv_onchain_usd"),
            "price_status": price.get("status"),
            "price_reason": price.get("reason"),
            "price_deviation_pct": price.get("deviation_pct"),
            "geckoterminal_price_usd": price.get("geckoterminal_price_usd"),
            "rugcheck_price_usd": price.get("rugcheck_price_usd"),
            "price_used_usd": market_cap.get("price_used_usd"),
            "price_used_source": market_cap.get("price_used_source"),
            "price_used_basis": market_cap.get("price_used_basis"),
            "onchain_bounds": _onchain_details(pool),
        },
        "fdv_usd_dexpaprika": pool.get("fdv_usd_dexpaprika"),
        "fdv_usd_geckoterminal": pool.get("fdv_usd_geckoterminal"),
        # Nur protokolliert, nicht bewertet (keine Filter, keine Strategie)
        "pair_age_hours": pool.get("pair_age_hours"),
        "volume_usd_24h": pool.get("volume_usd_24h"),
        "transactions_24h": pool.get("transactions_24h"),
        "rugcheck_risks": _rugcheck_risks(pool),
        "lp": _lp_details(pool),
    }


def main():
    print("SOLANA MEMECOIN DESK")
    print("===================")

    try:
        config = load_config()

    except ConfigError as error:
        log_event(
            "config_error",
            {
                "error": str(error),
                "live_trading": False,
            },
        )

        print("Fehler in der Konfiguration:")
        print(error)
        return 2

    scanner = config["scanner"]
    risk_filters = config["risk_filters"]
    max_checks = scanner["max_security_checks_per_run"]

    print("Modus: PAPER-RESEARCH")
    print("Live-Trading: Deaktiviert")
    print("Sicherheitsprüfungen pro Lauf:", max_checks)

    log_event(
        "system_start",
        {
            "status": "scanning",
            "mode": "paper_research",
            "live_trading": False,
            "ai_agents_connected": False,
            "market_data_connected": False,
        },
    )

    print("\n1. Marktdaten abrufen")

    try:
        market_data = fetch_pools_by_age(
            min_age_hours=scanner["pair_age_hours_min"],
            max_age_hours=scanner["pair_age_hours_max"],
            min_liquidity_usd=risk_filters["reject_liquidity_below_usd"],
        )

    except MarketDataError as error:
        log_event(
            "market_data_error",
            {
                "error": str(error),
                "live_trading": False,
            },
        )

        print("Fehler beim Abruf der Marktdaten:")
        print(error)
        return 1

    pools = market_data["results"]

    print("Verbindung erfolgreich.")
    print("Quelle: DexPaprika")
    print("Pools abgerufen:", len(pools))
    print("Pagination vollständig:", market_data["pagination_complete"])

    print("\n2. Sicherheitsscanner starten")

    # Frühere Scans nur für die Prüfreihenfolge (gegen Starvation).
    check_history = load_check_history(log_file_path())
    print("Prüfreihenfolge:", check_history["source"])

    result = scan_pool_security(
        pools,
        config,
        max_checks=max_checks,
        check_history=check_history,
    )

    scanned_pools = result["pools"]

    rejected = sum(
        pool["status"] == "rejected"
        for pool in scanned_pools
    )

    incomplete = sum(
        pool["status"] == "incomplete"
        for pool in scanned_pools
    )

    ready = sum(
        pool["status"] == "ready_for_full_risk_screen"
        for pool in scanned_pools
    )

    checked_pool_results = []

    for pool in scanned_pools:
        if pool["security_check_status"] == "not_checked":
            continue

        checked_pool_results.append(
            {
                "token_name": pool.get("token_name"),
                "token_symbol": pool.get("token_symbol"),
                "token_address": pool.get("token_address"),
                "status": pool.get("status"),
                "security_check_status": pool.get(
                    "security_check_status"
                ),
                "check_queue_position": pool.get("check_queue_position"),
                "queue_reason": pool.get("queue_reason"),
                "previous_rejection_at": pool.get("previous_rejection_at"),
                "holder_check_status": pool.get(
                    "holder_check_status"
                ),
                "rejection_reasons": pool.get(
                    "rejection_reasons",
                    [],
                ),
                "missing_checks": pool.get(
                    "missing_checks",
                    [],
                ),
                "security_error": pool.get("security_error"),
                "holder_error": pool.get("holder_error"),
                **_check_details(pool),
            }
        )

    summary = {
        "mode": "paper_research",
        "source": "dexpaprika",
        "total_pools": len(scanned_pools),
        "rejected_pools": rejected,
        "incomplete_pools": incomplete,
        "ready_for_full_risk_screen": ready,
        "security_checks_attempted": result["checks_attempted"],
        "security_checks_succeeded": result["checks_succeeded"],
        "security_checks_failed": result["checks_failed"],
        "holder_checks_attempted": result["holder_checks_attempted"],
        "holder_checks_succeeded": result["holder_checks_succeeded"],
        "holder_checks_failed": result["holder_checks_failed"],
        "mint_info_checks_attempted": result.get("mint_info_checks_attempted"),
        "mint_info_checks_failed": result.get("mint_info_checks_failed"),
        "full_holder_list_attempted": result.get("full_holder_list_attempted"),
        "full_holder_list_succeeded": result.get("full_holder_list_succeeded"),
        "full_holder_list_failed": result.get("full_holder_list_failed"),
        "holder_fallbacks": result.get("holder_fallbacks"),
        "onchain_bounds_attempted": result.get("onchain_bounds_attempted"),
        "onchain_bounds_failed": result.get("onchain_bounds_failed"),
        "authority_account_calls": result.get("authority_account_calls"),
        "frozen_account_checks": result.get("frozen_account_checks"),
        "check_order_source": result.get("check_order_source"),
        "deprioritized_tokens": result.get("deprioritized_tokens"),
        "pagination_complete": market_data["pagination_complete"],
        "market_cap_diagnostics": (
            scanned_pools[0].get("market_cap_diagnostics")
            if scanned_pools
            else empty_market_cap_diagnostics()
        ),
        "live_trading": False,
        "ai_agents_connected": False,
        "paper_trading_enabled": False,
        "checked_pools": checked_pool_results,
    }

    log_event(
        "security_scan_completed",
        summary,
    )

    print("\n3. Ergebnis")
    print("Pools insgesamt:", len(scanned_pools))
    print("Abgelehnt:", rejected)
    print("Unvollständig:", incomplete)
    print("Bereit für vollständige Risikoprüfung:", ready)

    print("\nSicherheitsprüfungen:")
    print("Versucht:", result["checks_attempted"])
    print("Erfolgreich:", result["checks_succeeded"])
    print("Fehlgeschlagen:", result["checks_failed"])

    print("\nHolder-Prüfungen:")
    print("Versucht:", result["holder_checks_attempted"])
    print("Erfolgreich:", result["holder_checks_succeeded"])
    print("Fehlgeschlagen:", result["holder_checks_failed"])
    print("Vollständige Liste erfolgreich:", result.get("full_holder_list_succeeded"))
    print("Rückfall auf Stichprobe:", result.get("holder_fallbacks"))

    print("\nKI-Agenten: Noch nicht verbunden")
    print("Paper-Trading: Noch nicht aktiviert")
    print("Live-Trading: Deaktiviert")

    print("\nScan abgeschlossen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
