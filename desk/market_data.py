import os
import time
from datetime import datetime, timezone

import requests

from desk.data_quality import positive_number
from desk.token_classification import (
    STABLECOIN,
    STAKING_TOKEN,
    UNCLEAR,
    classify_token,
    is_known_non_memecoin,
)

BASE_URL = "https://api.geckoterminal.com/api/v2"
DEXPAPRIKA_URL = "https://api.dexpaprika.com"
DEXPAPRIKA_MAX_ATTEMPTS = 3
DEXPAPRIKA_RETRY_DELAY_SECONDS = 5

QUOTE_TOKEN_IDS = {
    "So11111111111111111111111111111111111111112".lower(),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".lower(),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),
}

GECKOTERMINAL_BATCH_SIZE = 30
GECKOTERMINAL_BATCH_DELAY_SECONDS = 6

# Begrenzte Wiederholungen beim Token-Abruf (technische Parameter, keine
# Prüf-Grenzwerte). Nie unbegrenzt; nach dem letzten Versuch gilt der
# Abruf als fehlgeschlagen und es werden KEINE alten Werte verwendet.
# - Netzwerkfehler, HTTP 429 und HTTP 5xx: höchstens 3 Versuche
# - HTTP 403: höchstens 2 Versuche (ein kontrollierter erneuter Versuch)
# - andere HTTP-Fehler (z. B. 400, 404) und ungültige Antworten: keine
#   Wiederholung
GECKOTERMINAL_MAX_ATTEMPTS = 3
GECKOTERMINAL_MAX_ATTEMPTS_403 = 2
# Wartezeit vor dem 2. bzw. 3. Versuch
GECKOTERMINAL_RETRY_DELAYS_SECONDS = (10, 30)
# Obergrenze für eine vom Server verlangte Wartezeit (Retry-After)
GECKOTERMINAL_MAX_RETRY_AFTER_SECONDS = 60


class MarketDataError(Exception):
    pass


def fetch_pools_by_age(
    *,
    min_age_hours,
    max_age_hours,
    min_liquidity_usd,
    timeout=20,
    max_pages=10,
):
    """Lädt Solana-Pools im Altersfenster von DexPaprika.

    Die Grenzwerte kommen aus config.yaml (scanner/risk_filters) und
    werden vom Aufrufer übergeben.
    """
    api_key = os.getenv("DEXPAPRIKA_API_KEY")
    cursor = None
    all_pools = []
    seen_ids = set()
    seen_cursors = set()
    pages_fetched = 0
    duplicates = 0
    has_next_page = False

    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")

    if min_age_hours > max_age_hours:
        raise ValueError("min_age_hours must not exceed max_age_hours")

    # Zeitfenster einmal festlegen, damit alle Seiten dasselbe Fenster
    # abfragen.
    now_ts = int(time.time())
    created_after = now_ts - int(max_age_hours * 3600)
    created_before = now_ts - int(min_age_hours * 3600)

    for page in range(max_pages):
        params = {
            "order_by": "created_at",
            "sort": "desc",
            "created_after": created_after,
            "created_before": created_before,
            "liquidity_usd_min": min_liquidity_usd,
            "limit": 100,
            "detailed": "true",
        }

        if cursor:
            if cursor in seen_cursors:
                raise MarketDataError(
                    "DexPaprika pagination cursor repeated"
                )

            seen_cursors.add(cursor)
            params["cursor"] = cursor

        headers = {}

        if api_key:
            headers["Authorization"] = api_key

        if page > 0:
            time.sleep(1)

        # Begrenzte Wiederholungen: Netzwerkfehler, 429 und 5xx höchstens
        # DEXPAPRIKA_MAX_ATTEMPTS Versuche, sonst sofort Fehler.
        attempt = 0
        while True:
            attempt += 1
            try:
                response = requests.get(
                    f"{DEXPAPRIKA_URL}/networks/solana/pools/search",
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                break

            except requests.RequestException as error:
                status = _http_status(error)
                retryable = status is None or status == 429 or status >= 500

                if retryable and attempt < DEXPAPRIKA_MAX_ATTEMPTS:
                    _wait(DEXPAPRIKA_RETRY_DELAY_SECONDS * attempt)
                    continue

                raise MarketDataError(
                    f"DexPaprika request failed: {error}"
                ) from error

            except ValueError as error:
                raise MarketDataError(
                    "Invalid DexPaprika JSON response"
                ) from error

        if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list
        ):
            raise MarketDataError(
                "DexPaprika returned no valid results list"
            )

        pages_fetched += 1
        has_next_page = bool(payload.get("has_next_page"))

        for pool in payload["results"]:
            if not isinstance(pool, dict):
                continue

            pool_id = pool.get("id")

            if pool_id and pool_id in seen_ids:
                duplicates += 1
                continue

            if pool_id:
                seen_ids.add(pool_id)

            all_pools.append(pool)

        if not has_next_page:
            cursor = None
            break

        next_cursor = payload.get("next_cursor")

        if not next_cursor:
            raise MarketDataError(
                "DexPaprika reports more pages but provides no cursor"
            )

        if next_cursor in seen_cursors:
            raise MarketDataError(
                "DexPaprika returned a repeated pagination cursor"
            )

        cursor = next_cursor

    return {
        "results": all_pools,
        "has_next_page": has_next_page,
        "next_cursor": cursor,
        "pages_fetched": pages_fetched,
        "duplicates_removed": duplicates,
        "api_key_used": bool(api_key),
        "pagination_complete": not has_next_page,
        "max_pages_reached": has_next_page and pages_fetched >= max_pages,
        "liquidity_filter_usd": min_liquidity_usd,
    }


def _as_number(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if number >= 0 else None


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _resolve_token(tokens):
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None

    valid = [
        token for token in tokens
        if isinstance(token, dict)
    ]

    if len(valid) != 2:
        return None

    quote_tokens = [
        token for token in valid
        if str(token.get("id", "")).lower() in QUOTE_TOKEN_IDS
    ]

    if len(quote_tokens) != 1:
        return None

    return next(
        token for token in valid
        if token is not quote_tokens[0]
    )


def _wait(seconds):
    """Wartezeit zwischen Wiederholungen (in Tests ersetzt)."""
    time.sleep(seconds)


def _status_code(response):
    status = getattr(response, "status_code", None)

    if isinstance(status, int) and not isinstance(status, bool):
        return status

    return None


def _http_status(error):
    return _status_code(getattr(error, "response", None))


def _failure_reason(status):
    if status is None:
        return "network_error"

    if status == 429:
        return "http_429_rate_limited"

    if status == 403:
        return "http_403_forbidden"

    if 500 <= status <= 599:
        return "http_5xx_server_error"

    return f"http_{status}"


def _max_attempts(status):
    if status is None or status == 429 or 500 <= status <= 599:
        return GECKOTERMINAL_MAX_ATTEMPTS

    if status == 403:
        return GECKOTERMINAL_MAX_ATTEMPTS_403

    return 1


def _retry_delay(error, status, attempt):
    """Wartezeit vor dem nächsten Versuch (attempt = bisherige Versuche)."""
    delay = GECKOTERMINAL_RETRY_DELAYS_SECONDS[
        min(attempt - 1, len(GECKOTERMINAL_RETRY_DELAYS_SECONDS) - 1)
    ]

    if status == 429:
        headers = getattr(getattr(error, "response", None), "headers", None)
        retry_after = (
            headers.get("Retry-After") if isinstance(headers, dict) else None
        )

        try:
            requested = int(retry_after)
        except (TypeError, ValueError):
            requested = None

        if requested is not None and requested > 0:
            delay = min(
                max(delay, requested),
                GECKOTERMINAL_MAX_RETRY_AFTER_SECONDS,
            )

    return delay


def _get_token_batch(url, timeout, stats):
    """Ein Token-Abruf mit begrenzten Wiederholungen.

    Protokolliert jeden Versuch in stats["request_log"] (Statuscode,
    Versuchsnummer, Fehlergrund). Gibt bei Erfolg die Antwort zurück,
    sonst wird der letzte Fehler weitergereicht.
    """
    attempt = 0

    while True:
        attempt += 1
        stats["request_attempts"] += 1

        try:
            response = requests.get(
                url,
                headers={"Accept": "application/json;version=20230203"},
                timeout=timeout,
            )
            response.raise_for_status()

        except requests.RequestException as error:
            status = _http_status(error)
            reason = _failure_reason(status)
            stats["last_http_status"] = status
            stats["error_reason"] = reason
            stats["request_log"].append(
                {
                    "batch": stats["batches_requested"],
                    "attempt": attempt,
                    "http_status": status,
                    "reason": reason,
                }
            )

            if attempt >= _max_attempts(status):
                raise

            stats["retries"] += 1
            _wait(_retry_delay(error, status, attempt))
            continue

        status = _status_code(response)
        stats["last_http_status"] = status
        stats["error_reason"] = None
        stats["request_log"].append(
            {
                "batch": stats["batches_requested"],
                "attempt": attempt,
                "http_status": status,
                "reason": "ok",
            }
        )
        return response


def fetch_token_market_data(token_addresses, timeout=15, diagnostics=None):
    """
    Lädt Marktdaten je Token von GeckoTerminal.

    Rückgabe: {adresse_klein: {"market_cap_usd", "price_usd", "fdv_usd"}}
    Marktkapitalisierung (gemeldete Umlaufmenge) und FDV bleiben getrennt.
    Ungültige Werte werden None. diagnostics ist optional.
    """
    addresses = list(dict.fromkeys(
        address.strip()
        for address in token_addresses
        if isinstance(address, str) and address.strip()
    ))

    stats = {
        "requested_unique_tokens": len(addresses),
        "batches_requested": 0,
        "token_objects_returned": 0,
        "requested_tokens_returned": 0,
        "tokens_with_market_cap": 0,
        "tokens_with_null_market_cap": 0,
        "tokens_with_missing_market_cap_field": 0,
        "tokens_with_invalid_market_cap": 0,
        "tokens_with_price": 0,
        "requested_tokens_not_returned": 0,
        "unrequested_token_objects_ignored": 0,
        "api_error": None,
        "request_attempts": 0,
        "retries": 0,
        "last_http_status": None,
        "error_reason": None,
        "request_log": [],
    }

    market_caps = {}

    for start in range(0, len(addresses), GECKOTERMINAL_BATCH_SIZE):
        batch = addresses[start:start + GECKOTERMINAL_BATCH_SIZE]
        requested_addresses = {
            address.lower() for address in batch
        }

        stats["batches_requested"] += 1

        try:
            response = _get_token_batch(
                f"{BASE_URL}/networks/solana/tokens/multi/{','.join(batch)}",
                timeout,
                stats,
            )
            payload = response.json()

        except requests.RequestException as error:
            stats["api_error"] = str(error)

            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(stats)

            raise MarketDataError(
                f"GeckoTerminal token request failed: {error}"
            ) from error

        except ValueError as error:
            stats["api_error"] = (
                "Invalid GeckoTerminal token JSON response"
            )
            stats["error_reason"] = "invalid_json"

            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(stats)

            raise MarketDataError(
                "Invalid GeckoTerminal token JSON response"
            ) from error

        if not isinstance(payload, dict):
            stats["api_error"] = (
                "Invalid GeckoTerminal token response"
            )

            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(stats)

            raise MarketDataError(
                "Invalid GeckoTerminal token response"
            )

        data = payload.get("data")

        if not isinstance(data, list):
            stats["api_error"] = (
                "GeckoTerminal returned no valid token list"
            )

            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(stats)

            raise MarketDataError(
                "GeckoTerminal returned no valid token list"
            )

        stats["token_objects_returned"] += len(data)
        returned_in_batch = set()

        for token_data in data:
            if not isinstance(token_data, dict):
                continue

            attributes = token_data.get("attributes")

            if not isinstance(attributes, dict):
                continue

            address = attributes.get("address")

            if not isinstance(address, str) or not address.strip():
                continue

            normalized_address = address.strip().lower()

            if normalized_address not in requested_addresses:
                stats["unrequested_token_objects_ignored"] += 1
                continue

            returned_in_batch.add(normalized_address)
            stats["requested_tokens_returned"] += 1

            price = positive_number(attributes.get("price_usd"))

            if price is not None:
                stats["tokens_with_price"] += 1

            entry = {
                "market_cap_usd": None,
                "price_usd": price,
                "fdv_usd": positive_number(attributes.get("fdv_usd")),
            }
            market_caps[normalized_address] = entry

            if "market_cap_usd" not in attributes:
                stats["tokens_with_missing_market_cap_field"] += 1
                continue

            raw_market_cap = attributes.get("market_cap_usd")

            if raw_market_cap is None:
                stats["tokens_with_null_market_cap"] += 1
                continue

            parsed_market_cap = positive_number(raw_market_cap)

            if parsed_market_cap is None:
                stats["tokens_with_invalid_market_cap"] += 1
                continue

            stats["tokens_with_market_cap"] += 1
            entry["market_cap_usd"] = parsed_market_cap

        stats["requested_tokens_not_returned"] += len(
            requested_addresses - returned_in_batch
        )

        if start + GECKOTERMINAL_BATCH_SIZE < len(addresses):
            time.sleep(GECKOTERMINAL_BATCH_DELAY_SECONDS)

    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(stats)

    return market_caps


# Diese Prüfungen erledigt ausschließlich desk/security_scan.py.
# Hier werden sie nur als "noch offen" markiert.
SECURITY_CHECKS = (
    "mint_authority_revoked",
    "freeze_authority_revoked",
    "lp_burned_or_locked",
    "liquidity_cross_check",
    "top10_share_pct",
    "dev_share_pct",
    "permanent_delegate_absent",
    "token_extensions_safe",
)


def _pair_has_no_memecoin(tokens):
    """True, wenn alle Token des Paares bekannte Nicht-Memecoins sind."""
    if not isinstance(tokens, list) or not tokens:
        return False

    ids = [
        token.get("id")
        for token in tokens
        if isinstance(token, dict)
    ]

    return (
        len(ids) == len(tokens)
        and all(isinstance(i, str) and is_known_non_memecoin(i) for i in ids)
    )


def prepare_pool(
    pool,
    config,
    now=None,
    market_caps=None,
    market_data_error=None,
):
    """Marktdaten-Vorprüfung eines Pools (ohne Sicherheitsprüfung).

    market_caps: Ergebnis von fetch_token_market_data
    ({adresse_klein: {"market_cap_usd", "price_usd", "fdv_usd"}}).
    """
    if not isinstance(pool, dict):
        raise ValueError("Pool must be a dictionary")

    scanner = config["scanner"]
    min_age_hours = scanner["pair_age_hours_min"]
    max_age_hours = scanner["pair_age_hours_max"]
    min_liquidity_usd = config["risk_filters"]["reject_liquidity_below_usd"]

    now = now or datetime.now(timezone.utc)

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now = now.astimezone(timezone.utc)

    created_at = _parse_timestamp(pool.get("created_at"))
    reasons = []
    missing = []
    age_hours = None

    if created_at is None:
        reasons.append("created_at_missing_or_invalid")
    else:
        age_hours = (
            now - created_at
        ).total_seconds() / 3600

        if age_hours < min_age_hours or age_hours > max_age_hours:
            reasons.append("pair_age_out_of_range")

    liquidity = _as_number(pool.get("liquidity_usd"))

    if liquidity is None:
        reasons.append("liquidity_missing_or_invalid")
    elif liquidity < min_liquidity_usd:
        reasons.append("liquidity_below_minimum")

    volume = _as_number(pool.get("volume_usd_24h"))
    transactions = _as_number(pool.get("transactions_24h"))

    if volume == 0 and transactions == 0:
        reasons.append("no_24h_trading_activity")
    elif volume is None or transactions is None:
        missing.append("24h_activity_data")

    token = _resolve_token(pool.get("tokens"))
    classification = None
    classification_detail = None

    if token is None:
        if _pair_has_no_memecoin(pool.get("tokens")):
            reasons.append("excluded_no_memecoin_in_pair")
        else:
            missing.append("unambiguous_base_token")
    else:
        classification, classification_detail = classify_token(
            token.get("id"),
            token.get("symbol"),
            token.get("name"),
        )

        if classification == STABLECOIN:
            reasons.append("excluded_stablecoin")
        elif classification == STAKING_TOKEN:
            reasons.append("excluded_staking_token")
        elif classification == UNCLEAR:
            missing.append("token_classification")

    token_address = token.get("id") if token else None
    dexpaprika_fdv = _as_number(token.get("fdv")) if token else None

    token_market = None

    if isinstance(token_address, str) and isinstance(market_caps, dict):
        token_market = market_caps.get(token_address.strip().lower())

    if not isinstance(token_market, dict):
        token_market = {}

    reported_market_cap = token_market.get("market_cap_usd")

    # Frühe Ablehnung nur, wenn eine gemeldete Marktkapitalisierung
    # außerhalb des Bereichs liegt. Bestätigt wird erst in
    # desk/security_scan.py mit Daten von der Blockchain.
    if (
        reported_market_cap is not None
        and not (
            scanner["market_cap_usd_min"]
            <= reported_market_cap
            <= scanner["market_cap_usd_max"]
        )
    ):
        reasons.append("market_cap_out_of_range")
    else:
        missing.append("market_cap_usd")

    missing.extend(SECURITY_CHECKS)

    status = (
        "rejected" if reasons
        else "incomplete" if missing
        else "ready_for_full_risk_screen"
    )

    result = {
        "pool_address": pool.get("id"),
        "dex_name": pool.get("dex_name"),
        "created_at": created_at.isoformat() if created_at else None,
        "pair_age_hours": (
            round(age_hours, 2)
            if age_hours is not None
            else None
        ),
        "token_address": token_address,
        "token_name": token.get("name") if token else None,
        "token_symbol": token.get("symbol") if token else None,
        "token_classification": classification,
        "token_classification_detail": classification_detail,
        "liquidity_usd": liquidity,
        "volume_usd_24h": volume,
        "transactions_24h": transactions,
        # FDV und Marktkapitalisierung bleiben getrennt.
        "fdv_usd_dexpaprika": dexpaprika_fdv,
        "fdv_usd_geckoterminal": token_market.get("fdv_usd"),
        "price_usd_geckoterminal": token_market.get("price_usd"),
        "market_cap_usd_reported": reported_market_cap,
        "market_cap_reported_source": (
            "geckoterminal"
            if reported_market_cap is not None
            else None
        ),
        "status": status,
        "rejection_reasons": reasons,
        "missing_checks": missing,
    }

    if market_data_error:
        result["market_data_error"] = market_data_error

    return result


def empty_market_cap_diagnostics(requested_unique_tokens=0):
    return {
        "requested_unique_tokens": requested_unique_tokens,
        "batches_requested": 0,
        "token_objects_returned": 0,
        "requested_tokens_returned": 0,
        "tokens_with_market_cap": 0,
        "tokens_with_null_market_cap": 0,
        "tokens_with_missing_market_cap_field": 0,
        "tokens_with_invalid_market_cap": 0,
        "tokens_with_price": 0,
        "requested_tokens_not_returned": requested_unique_tokens,
        "unrequested_token_objects_ignored": 0,
        "api_error": None,
        "request_attempts": 0,
        "retries": 0,
        "last_http_status": None,
        "error_reason": None,
        "request_log": [],
    }


def prepare_pools(pools, config, now=None):
    """Marktdaten-Vorprüfung für alle Pools.

    Lädt einmal die Token-Marktdaten (GeckoTerminal) und prüft jeden
    Pool mit prepare_pool. Sicherheitsprüfungen finden hier nicht statt.
    """
    pools = list(pools)

    unique_tokens = {}

    for pool in pools:
        if not isinstance(pool, dict):
            continue

        token = _resolve_token(pool.get("tokens"))

        if token is None:
            continue

        address = token.get("id")

        if not isinstance(address, str) or not address.strip():
            continue

        # Bekannte Nicht-Memecoins brauchen keine Marktdaten.
        if is_known_non_memecoin(address.strip()):
            continue

        normalized = address.strip().lower()

        if normalized not in unique_tokens:
            unique_tokens[normalized] = address.strip()

    token_addresses = list(unique_tokens.values())

    market_caps = {}
    market_data_error = None

    market_cap_diagnostics = empty_market_cap_diagnostics(
        len(token_addresses)
    )

    if token_addresses:
        try:
            market_caps = fetch_token_market_data(
                token_addresses,
                diagnostics=market_cap_diagnostics,
            )
        except MarketDataError as error:
            market_data_error = str(error)
            market_cap_diagnostics["api_error"] = str(error)

    prepared = []

    for pool in pools:
        result = prepare_pool(
            pool,
            config,
            now=now,
            market_caps=market_caps,
            market_data_error=market_data_error,
        )

        result["market_cap_diagnostics"] = market_cap_diagnostics

        prepared.append(result)

    return prepared

