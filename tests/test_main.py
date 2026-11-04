
from unittest.mock import patch

from desk import __main__ as app
from desk.market_data import MarketDataError


def test_main_runs_market_data_and_security_scan(capsys):
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

    mocked_market.assert_called_once_with()

    mocked_scan.assert_called_once_with(
        market_data["results"],
        max_checks=10,
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
  
