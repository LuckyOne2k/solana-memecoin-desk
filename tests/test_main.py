
from unittest.mock import patch

from desk import __main__ as app
from desk.config import ConfigError
from desk.market_data import MarketDataError


def test_main_runs_market_data_and_security_scan(capsys, desk_config):
    market_data = {
        "results": [{"id": "POOL1"}],
        "pagination_complete": True,
    }

    scan_result = {
        "pools": [
            {
                "token_name": "Example",
                "token_symbol": "EX",
                "token_address": "TOKEN123",
                "status": "incomplete",
                "security_check_status": "completed",
                "holder_check_status": "completed",
                "rejection_reasons": [],
                "missing_checks": ["market_cap_usd"],
            }
        ],
        "checks_attempted": 1,
        "checks_succeeded": 1,
        "checks_failed": 0,
        "holder_checks_attempted": 1,
        "holder_checks_succeeded": 1,
        "holder_checks_failed": 0,
        "max_checks_per_run": 10,
    }

    with patch.object(
        app,
        "fetch_pools_by_age",
        return_value=market_data,
    ) as mocked_market, patch.object(
        app,
        "scan_pool_security",
        return_value=scan_result,
    ) as mocked_scan, patch.object(
        app,
        "log_event",
    ) as mocked_log:

        exit_code = app.main()

    assert exit_code == 0

    mocked_market.assert_called_once_with(
        min_age_hours=desk_config["scanner"]["pair_age_hours_min"],
        max_age_hours=desk_config["scanner"]["pair_age_hours_max"],
        min_liquidity_usd=(
            desk_config["risk_filters"]["reject_liquidity_below_usd"]
        ),
    )

    mocked_scan.assert_called_once_with(
        market_data["results"],
        desk_config,
        max_checks=desk_config["scanner"]["max_security_checks_per_run"],
    )

    assert mocked_log.call_count == 2

    output = capsys.readouterr().out

    assert "Sicherheitsscanner starten" in output
    assert "Scan abgeschlossen." in output
    assert "Live-Trading: Deaktiviert" in output


def test_main_handles_market_data_error(capsys):
    with patch.object(
        app,
        "fetch_pools_by_age",
        side_effect=MarketDataError("API unavailable"),
    ), patch.object(
        app,
        "scan_pool_security",
    ) as mocked_scan, patch.object(
        app,
        "log_event",
    ) as mocked_log:

        exit_code = app.main()

    assert exit_code == 1

    mocked_scan.assert_not_called()

    assert mocked_log.call_count == 2

    output = capsys.readouterr().out

    assert "API unavailable" in output


def test_main_stops_on_invalid_config(capsys):
    with patch.object(
        app,
        "load_config",
        side_effect=ConfigError("mode muss 'paper' sein"),
    ), patch.object(
        app,
        "fetch_pools_by_age",
    ) as mocked_market, patch.object(
        app,
        "scan_pool_security",
    ) as mocked_scan, patch.object(
        app,
        "log_event",
    ) as mocked_log:

        exit_code = app.main()

    assert exit_code == 2

    mocked_market.assert_not_called()
    mocked_scan.assert_not_called()

    mocked_log.assert_called_once()
    assert mocked_log.call_args.args[0] == "config_error"

    assert "Fehler in der Konfiguration" in capsys.readouterr().out


def test_check_details_show_price_basis():
    pool = {
        "market_cap_evaluation": {
            "status": "unclear",
            "price": {
                "status": "confirmed",
                "reason": "price_sources_agree",
                "deviation_pct": 9.09,
                "geckoterminal_price_usd": 0.0005,
                "rugcheck_price_usd": 0.00055,
            },
            "price_used_usd": 0.00055,
            "price_used_source": "rugcheck",
            "price_used_basis": "higher_of_two_agreeing_sources",
            "market_cap_upper_bound_usd": 550000.0,
        }
    }

    details = app._check_details(pool)["market_cap"]

    assert details["geckoterminal_price_usd"] == 0.0005
    assert details["rugcheck_price_usd"] == 0.00055
    assert details["price_used_usd"] == 0.00055
    assert details["price_used_source"] == "rugcheck"
    assert details["price_used_basis"] == "higher_of_two_agreeing_sources"
    assert details["market_cap_upper_bound_usd"] == 550000.0
