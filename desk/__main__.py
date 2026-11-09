
from desk.config import ConfigError, load_config
from desk.logging_utils import log_event
from desk.market_data import (
    MarketDataError,
    empty_market_cap_diagnostics,
    fetch_pools_by_age,
)
from desk.security_scan import scan_pool_security


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
                "top10_observed_share_lower_bound_pct": pool.get(
                    "top10_observed_share_lower_bound_pct"
                ),
                "creator_observed_share_lower_bound_pct": pool.get(
                    "creator_observed_share_lower_bound_pct"
                ),
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
