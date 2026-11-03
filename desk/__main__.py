from desk.logging_utils import log_event


def main():
    log_event(
        "system_start",
        {
            "status": "demo",
            "message": "Solana Memecoin Desk gestartet.",
            "live_trading": False,
            "ai_agents_connected": False,
            "market_data_connected": False,
        },
    )

    print("Solana Memecoin Desk gestartet.")
    print("Modus: DEMO")
    print("Marktdaten: Nicht verbunden")
    print("KI-Agenten: Nicht verbunden")
    print("Live-Trading: Deaktiviert")


if __name__ == "__main__":
    main()
