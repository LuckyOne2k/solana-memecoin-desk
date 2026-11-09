
from desk.config import ConfigError, load_config
from desk.logging_utils import log_event
from desk.market_data import (
    MarketDataError,
    empty_market_cap_diagnostics,
    fetch_pools_by_age,
)
from desk.security_scan import scan_pool_security


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
        "holders": {
            "top10_status": holders.get("top10_status"),
            "top10_share_pct": holders.get("top10_share_pct"),
            "top10_confirmed": holders.get("top10_confirmed"),
            "top10_share_of_non_pool_supply_pct": holders.get(
                "top10_share_of_non_pool_supply_pct"
            ),
            "pool_share_pct": holders.get("pool_share_pct"),
            "burned_amount_raw": holders.get("burned_amount_raw"),
            "counted_owners_in_sample": holders.get(
                "counted_owners_in_sample"
            ),
            "dev_status": holders.get("dev_status"),
            "dev_share_pct": holders.get("dev_share_pct"),
            "dev_share_upper_bound_pct": holders.get(
                "dev_share_upper_bound_pct"
            ),
            "unconfirmed_reasons": holders.get("unconfirmed_reasons"),
        },
        "market_cap": {
            "status": market_cap.get("status"),
            "reason": market_cap.get("reason"),
            "market_cap_usd": market_cap.get("market_cap_usd"),
            "basis": market_cap.get("basis"),
            "is_estimate": market_cap.get("is_estimate"),
            "reported_market_cap_usd": pool.get("market_cap_usd_reported"),
            "fdv_onchain_usd": market_cap.get("fdv_onchain_usd"),
            "price_status": price.get("status"),
            "price_reason": price.get("reason"),
            "price_used_usd": price.get("price_used_usd"),
        },
        "fdv_usd_dexpaprika": pool.get("fdv_usd_dexpaprika"),
        "fdv_usd_geckoterminal": pool.get("fdv_usd_geckoterminal"),
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

    result = scan_pool_security(
        pools,
        config,
        max_checks=max_checks,
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

    print("\nKI-Agenten: Noch nicht verbunden")
    print("Paper-Trading: Noch nicht aktiviert")
    print("Live-Trading: Deaktiviert")

    print("\nScan abgeschlossen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
