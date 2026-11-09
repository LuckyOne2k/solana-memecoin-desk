"""GeckoTerminal-Token-Abruf: begrenzte Wiederholungen und Protokoll.

Regeln:
- Netzwerkfehler, HTTP 429 und HTTP 5xx: höchstens 3 Versuche.
- HTTP 403: höchstens 2 Versuche.
- Andere HTTP-Fehler und ungültige Antworten: keine Wiederholung.
- Nach dem letzten Fehlversuch: MarketDataError, keine alten Werte.
- Statuscode, Versuchszahl und Fehlergrund stehen in der Diagnose.
"""

from unittest.mock import Mock, patch

import pytest
import requests

from desk import market_data
from desk.market_data import (
    GECKOTERMINAL_MAX_ATTEMPTS,
    GECKOTERMINAL_MAX_ATTEMPTS_403,
    MarketDataError,
    fetch_token_market_data,
    prepare_pools,
)

MINT = "TokenMintAddress123"


def ok_response(price="0.5", market_cap="400000"):
    response = Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [
            {
                "id": f"solana_{MINT}",
                "attributes": {
                    "address": MINT,
                    "price_usd": price,
                    "market_cap_usd": market_cap,
                },
            }
        ]
    }
    return response


def http_error_response(status, headers=None):
    response = Mock()
    response.status_code = status
    response.headers = headers or {}
    response.raise_for_status.side_effect = requests.HTTPError(
        f"{status} Client Error", response=response
    )
    return response


def network_error():
    return requests.ConnectionError("connection reset")


def run(side_effect):
    diagnostics = {}

    with patch(
        "desk.market_data.requests.get", side_effect=side_effect
    ) as mocked_get:
        try:
            result = fetch_token_market_data([MINT], diagnostics=diagnostics)
            error = None
        except MarketDataError as exc:
            result = None
            error = exc

    return result, error, diagnostics, mocked_get


# --------------------------------------------------
# Grenzen der Wiederholungen
# --------------------------------------------------

def test_success_first_try_logs_one_attempt():
    result, error, diag, mocked = run([ok_response()])

    assert error is None
    assert mocked.call_count == 1
    assert diag["request_attempts"] == 1
    assert diag["retries"] == 0
    assert diag["last_http_status"] == 200
    assert diag["error_reason"] is None
    assert diag["request_log"] == [
        {"batch": 1, "attempt": 1, "http_status": 200, "reason": "ok"}
    ]
    assert result[MINT.lower()]["price_usd"] == 0.5


@pytest.mark.parametrize(
    "failure, reason",
    [
        (lambda: network_error(), "network_error"),
        (lambda: http_error_response(429), "http_429_rate_limited"),
        (lambda: http_error_response(500), "http_5xx_server_error"),
        (lambda: http_error_response(503), "http_5xx_server_error"),
    ],
)
def test_retryable_error_then_success(failure, reason):
    result, error, diag, mocked = run([failure(), ok_response()])

    assert error is None
    assert mocked.call_count == 2
    assert diag["request_attempts"] == 2
    assert diag["retries"] == 1
    assert diag["request_log"][0]["reason"] == reason
    assert diag["request_log"][1]["reason"] == "ok"
    assert diag["error_reason"] is None
    assert result[MINT.lower()]["price_usd"] == 0.5


@pytest.mark.parametrize(
    "failure, status, reason",
    [
        (lambda: network_error(), None, "network_error"),
        (lambda: http_error_response(429), 429, "http_429_rate_limited"),
        (lambda: http_error_response(502), 502, "http_5xx_server_error"),
    ],
)
def test_retryable_error_stops_after_max_attempts(failure, status, reason):
    # Mehr Fehler bereitstellen als erlaubt: es darf nicht weiter gehen.
    failures = [failure() for _ in range(GECKOTERMINAL_MAX_ATTEMPTS + 3)]
    result, error, diag, mocked = run(failures)

    assert isinstance(error, MarketDataError)
    assert result is None
    assert mocked.call_count == GECKOTERMINAL_MAX_ATTEMPTS == 3
    assert diag["request_attempts"] == 3
    assert diag["retries"] == 2
    assert diag["last_http_status"] == status
    assert diag["error_reason"] == reason
    assert [entry["attempt"] for entry in diag["request_log"]] == [1, 2, 3]
    assert diag["api_error"]


def test_403_gets_exactly_one_retry_then_fails():
    failures = [http_error_response(403) for _ in range(5)]
    result, error, diag, mocked = run(failures)

    assert isinstance(error, MarketDataError)
    assert mocked.call_count == GECKOTERMINAL_MAX_ATTEMPTS_403 == 2
    assert diag["request_attempts"] == 2
    assert diag["retries"] == 1
    assert diag["last_http_status"] == 403
    assert diag["error_reason"] == "http_403_forbidden"


def test_403_then_success_is_used():
    result, error, diag, mocked = run([http_error_response(403), ok_response()])

    assert error is None
    assert mocked.call_count == 2
    assert result[MINT.lower()]["price_usd"] == 0.5


@pytest.mark.parametrize("status", [400, 401, 404, 410])
def test_other_http_errors_are_not_retried(status):
    result, error, diag, mocked = run(
        [http_error_response(status), ok_response()]
    )

    assert isinstance(error, MarketDataError)
    assert mocked.call_count == 1
    assert diag["retries"] == 0
    assert diag["last_http_status"] == status
    assert diag["error_reason"] == f"http_{status}"


def test_invalid_json_is_not_retried():
    response = Mock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("bad json")

    result, error, diag, mocked = run([response, ok_response()])

    assert isinstance(error, MarketDataError)
    assert mocked.call_count == 1
    assert diag["error_reason"] == "invalid_json"


def test_mixed_errors_never_exceed_limit():
    # 429, 5xx, Netzwerk: zusammen höchstens 3 Versuche.
    failures = [
        http_error_response(429),
        http_error_response(500),
        network_error(),
        ok_response(),
    ]
    result, error, diag, mocked = run(failures)

    assert isinstance(error, MarketDataError)
    assert mocked.call_count == 3


def test_403_after_other_errors_stops_at_403_limit():
    # Ein 403 beim 2. Versuch: 403 erlaubt höchstens 2 Versuche -> Ende.
    failures = [network_error(), http_error_response(403), ok_response()]
    result, error, diag, mocked = run(failures)

    assert isinstance(error, MarketDataError)
    assert mocked.call_count == 2
    assert diag["error_reason"] == "http_403_forbidden"


# --------------------------------------------------
# Wartezeiten
# --------------------------------------------------

def test_wait_times_are_bounded(monkeypatch):
    waits = []
    monkeypatch.setattr(market_data, "_wait", waits.append)

    run([http_error_response(500) for _ in range(3)])

    assert waits == list(market_data.GECKOTERMINAL_RETRY_DELAYS_SECONDS)


def test_retry_after_is_respected_but_capped(monkeypatch):
    waits = []
    monkeypatch.setattr(market_data, "_wait", waits.append)

    run(
        [
            http_error_response(429, {"Retry-After": "25"}),
            http_error_response(429, {"Retry-After": "9999"}),
            ok_response(),
        ]
    )

    assert waits == [25, market_data.GECKOTERMINAL_MAX_RETRY_AFTER_SECONDS]


def test_invalid_retry_after_uses_default(monkeypatch):
    waits = []
    monkeypatch.setattr(market_data, "_wait", waits.append)

    run([http_error_response(429, {"Retry-After": "soon"}), ok_response()])

    assert waits == [market_data.GECKOTERMINAL_RETRY_DELAYS_SECONDS[0]]


# --------------------------------------------------
# Keine alten Werte, Pflicht zu zwei Preisen bleibt
# --------------------------------------------------

def test_failure_returns_no_prices_at_all():
    # Auch ein früher erfolgreicher Abruf derselben Sitzung liefert
    # keine Ersatzwerte: jeder Aufruf beginnt ohne gespeicherte Daten.
    first, error, _, _ = run([ok_response()])
    assert error is None

    second, error, diag, _ = run([http_error_response(403)] * 2)

    assert second is None
    assert isinstance(error, MarketDataError)
    assert diag["tokens_with_price"] == 0
    assert diag["requested_tokens_returned"] == 0


def test_prepare_pools_failure_leaves_no_geckoterminal_price(desk_config):
    pool = {
        "id": "pool-1",
        "dex_name": "PumpSwap",
        "created_at": "2026-11-07T11:02:42Z",
        "tokens": [
            {"id": MINT, "symbol": "TTP", "name": "Test"},
            {
                "id": "So11111111111111111111111111111111111111112",
                "symbol": "SOL",
                "name": "Wrapped SOL",
            },
        ],
    }

    with patch(
        "desk.market_data.requests.get",
        side_effect=[http_error_response(403)] * 5,
    ) as mocked_get:
        prepared = prepare_pools([pool], desk_config)

    assert mocked_get.call_count == 2
    result = prepared[0]
    diag = result["market_cap_diagnostics"]

    assert result.get("price_usd_geckoterminal") is None
    assert result.get("market_cap_usd_reported") is None
    assert result.get("market_data_error")
    assert diag["error_reason"] == "http_403_forbidden"
    assert diag["last_http_status"] == 403
    assert diag["request_attempts"] == 2


def test_without_geckoterminal_price_reported_value_cannot_confirm(desk_config):
    # Fällt GeckoTerminal aus, gibt es nur den RugCheck-Preis:
    # ein gemeldeter Wert kann dann nie bestätigt werden.
    from desk.market_cap import evaluate_market_cap

    result = evaluate_market_cap(
        config=desk_config,
        reported_market_cap_usd=1_000_000,
        geckoterminal_price_usd=None,
        rugcheck_price_usd=0.001,
        total_supply_raw=1_000_000_000 * 10**6,
        supply_decimals=6,
    )

    assert result["status"] != "confirmed"
    assert result["market_cap_usd"] is None
