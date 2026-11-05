import json
import os
import time
from datetime import datetime, timezone

import requests

from desk.security import SecurityDataError, check_token_security

BASE_URL = "https://api.geckoterminal.com/api/v2"
DEXPAPRIKA_URL = "https://api.dexpaprika.com"

QUOTE_TOKEN_IDS = {
    "So11111111111111111111111111111111111111112".lower(),
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v".lower(),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB".lower(),
}

GECKOTERMINAL_BATCH_SIZE = 30
GECKOTERMINAL_BATCH_DELAY_SECONDS = 6


class MarketDataError(Exception):
    pass


def fetch_new_pools(page=1, timeout=15):
    if page < 1:
        raise ValueError("Page must be at least 1")

    try:
        response = requests.get(
            f"{BASE_URL}/networks/solana/new_pools",
            params={"page": page, "include": "base_token,quote_token"},
            headers={"Accept": "application/json;version=20230203"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise MarketDataError(
            f"GeckoTerminal request failed: {error}"
        ) from error
    except ValueError as error:
        raise MarketDataError(
            "Invalid GeckoTerminal JSON response"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MarketDataError("Invalid GeckoTerminal response structure")

    return payload


def fetch_pools_by_age(timeout=20, max_pages=10):
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

    for page in range(max_pages):
        params = {
            "order_by": "created_at",
            "sort": "desc",
            "created_after": int(time.time()) - 72 * 3600,
            "created_before": int(time.time()) - 30 * 3600,
            "liquidity_usd_min": 40000,
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

        try:
            response = requests.get(
                f"{DEXPAPRIKA_URL}/networks/solana/pools/search",
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()

        except requests.RequestException as error:
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
        "liquidity_filter_usd": 40000,
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


def fetch_token_market_caps(token_addresses, timeout=15, diagnostics=None):
    """
    Lädt Marktkapitalisierungen von GeckoTerminal.

    diagnostics ist optional und wird nur zur Ursachenanalyse befüllt.
    Der Rückgabewert bleibt aus Kompatibilitätsgründen unverändert.
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
        "requested_tokens_not_returned": 0,
        "unrequested_token_objects_ignored": 0,
        "api_error": None,
    }

    market_caps = {}

    for start in range(0, len(addresses), GECKOTERMINAL_BATCH_SIZE):
        batch = addresses[start:start + GECKOTERMINAL_BATCH_SIZE]
        requested_addresses = {
            address.lower() for address in batch
        }

        stats["batches_requested"] += 1

        try:
            response = requests.get(
                f"{BASE_URL}/networks/solana/tokens/multi/{','.join(batch)}",
                headers={"Accept": "application/json;version=20230203"},
                timeout=timeout,
            )
            response.raise_for_status()
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

            if "market_cap_usd" not in attributes:
                stats["tokens_with_missing_market_cap_field"] += 1
                market_caps[normalized_address] = None
                continue

            raw_market_cap = attributes.get("market_cap_usd")

            if raw_market_cap is None:
                stats["tokens_with_null_market_cap"] += 1
                market_caps[normalized_address] = None
                continue

            parsed_market_cap = _as_number(raw_market_cap)

            if parsed_market_cap is None:
                stats["tokens_with_invalid_market_cap"] += 1
                market_caps[normalized_address] = None
                continue

            stats["tokens_with_market_cap"] += 1
            market_caps[normalized_address] = parsed_market_cap

        stats["requested_tokens_not_returned"] += len(
            requested_addresses - returned_in_batch
        )

        if start + GECKOTERMINAL_BATCH_SIZE < len(addresses):
            time.sleep(GECKOTERMINAL_BATCH_DELAY_SECONDS)

    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(stats)

    return market_caps


def prepare_pool(
    pool,
    now=None,
    min_age_hours=30,
    max_age_hours=72,
    min_liquidity_usd=40000,
    market_caps=None,
    market_data_error=None,
    security_data=None,
    security_error=None,
):
    if not isinstance(pool, dict):
        raise ValueError("Pool must be a dictionary")

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

    if token is None:
        missing.append("unambiguous_base_token")

    token_address = token.get("id") if token else None
    fdv = _as_number(token.get("fdv")) if token else None

    market_cap = None

    if isinstance(token_address, str) and isinstance(market_caps, dict):
        market_cap = market_caps.get(token_address.strip().lower())

    if market_cap is None:
        missing.append("market_cap_usd")

    security = security_data if isinstance(security_data, dict) else {}

    mint_revoked = security.get("mint_authority_revoked")
    freeze_revoked = security.get("freeze_authority_revoked")
    lp_secured = security.get("lp_burned_or_locked")

    top10_verified = (
        security.get("top10_share_fully_verified") is True
    )

    top10_pct = _as_number(
        security.get("top10_share_sampled_pct")
    )

    dev_pct = _as_number(
        security.get("creator_share_sampled_pct")
    )

    if mint_revoked is True:
        pass
    elif mint_revoked is False:
        reasons.append("mint_authority_active")
    else:
        missing.append("mint_authority_revoked")

    if freeze_revoked is True:
        pass
    elif freeze_revoked is False:
        reasons.append("freeze_authority_active")
    else:
        missing.append("freeze_authority_revoked")

    if lp_secured is True:
        pass
    elif lp_secured is False:
        reasons.append("lp_not_burned_or_locked")
    else:
        missing.append("lp_burned_or_locked")

    if not top10_verified or top10_pct is None:
        missing.append("top10_share_pct")

    if (
        dev_pct is None
        or security.get("owner_attribution_complete") is not True
    ):
        missing.append("dev_share_pct")

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
        "liquidity_usd": liquidity,
        "volume_usd_24h": volume,
        "transactions_24h": transactions,
        "fdv_usd": fdv,
        "market_cap_usd": market_cap,
        "market_cap_source": (
            "geckoterminal"
            if market_cap is not None
            else None
        ),
        "status": status,
        "rejection_reasons": reasons,
        "missing_checks": missing,
        "security_data": security if security else None,
    }

    if security_error:
        result["security_data_error"] = security_error

    if market_data_error:
        result["market_data_error"] = market_data_error

    return result


def prepare_pools(
    pools,
    security_checks_enabled=False,
    max_security_checks=10,
    **kwargs,
):
    pools = list(pools)

    if max_security_checks < 0:
        raise ValueError(
            "max_security_checks must be at least 0"
        )

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

        normalized = address.strip().lower()

        if normalized not in unique_tokens:
            unique_tokens[normalized] = address.strip()

    token_addresses = list(unique_tokens.values())

    market_caps = {}
    market_data_error = None

    market_cap_diagnostics = {
        "requested_unique_tokens": len(token_addresses),
        "batches_requested": 0,
        "token_objects_returned": 0,
        "requested_tokens_returned": 0,
        "tokens_with_market_cap": 0,
        "tokens_with_null_market_cap": 0,
        "tokens_with_missing_market_cap_field": 0,
        "tokens_with_invalid_market_cap": 0,
        "requested_tokens_not_returned": len(token_addresses),
        "unrequested_token_objects_ignored": 0,
        "api_error": None,
    }

    if token_addresses:
        try:
            market_caps = fetch_token_market_caps(
                token_addresses,
                diagnostics=market_cap_diagnostics,
            )
        except MarketDataError as error:
            market_data_error = str(error)
            market_cap_diagnostics["api_error"] = str(error)

    security_by_mint = {}
    security_errors = {}
    security_status_by_mint = {}

    if security_checks_enabled:
        normalized_addresses = list(unique_tokens.keys())

        addresses_to_check = normalized_addresses[
            :max_security_checks
        ]

        for normalized in addresses_to_check:
            address = unique_tokens[normalized]

            try:
                security_by_mint[normalized] = (
                    check_token_security(address)
                )

                security_status_by_mint[normalized] = "completed"

            except (SecurityDataError, ValueError) as error:
                security_errors[normalized] = str(error)
                security_status_by_mint[normalized] = "error"

        for normalized in normalized_addresses[max_security_checks:]:
            security_status_by_mint[normalized] = (
                "skipped_run_limit"
            )

    prepared = []

    for pool in pools:
        token = (
            _resolve_token(pool.get("tokens"))
            if isinstance(pool, dict)
            else None
        )

        address = token.get("id") if token else None

        normalized = (
            address.strip().lower()
            if isinstance(address, str) and address.strip()
            else None
        )

        if not security_checks_enabled:
            security_status = "not_enabled"

        elif not normalized:
            security_status = "skipped_missing_mint"

        else:
            security_status = security_status_by_mint.get(
                normalized,
                "skipped_run_limit",
            )

        result = prepare_pool(
            pool,
            market_caps=market_caps,
            market_data_error=market_data_error,
            security_data=security_by_mint.get(normalized),
            security_error=security_errors.get(normalized),
            **kwargs,
        )

        result["security_check_status"] = security_status
        result["market_cap_diagnostics"] = market_cap_diagnostics

        prepared.append(result)

    return prepared


if __name__ == "__main__":
    print("SOLANA MEMECOIN DESK")
    print("===================")

    print("\n1. GeckoTerminal")

    gecko = fetch_new_pools()

    print("Verbindung erfolgreich.")
    print("Neue Pools:", len(gecko["data"]))

    print("\n2. DexPaprika")

    paprika = fetch_pools_by_age()
    pools = paprika["results"]

    prepared = prepare_pools(
        pools,
        security_checks_enabled=True,
        max_security_checks=10,
    )

    rejected = sum(
        p["status"] == "rejected"
        for p in prepared
    )

    incomplete = sum(
        p["status"] == "incomplete"
        for p in prepared
    )

    ready = sum(
        p["status"] == "ready_for_full_risk_screen"
        for p in prepared
    )

    market_cap_found = sum(
        p["market_cap_usd"] is not None
        for p in prepared
    )

    print("Verbindung erfolgreich.")
    print("API-Key mitgesendet:", paprika["api_key_used"])
    print("Pagination vollständig:", paprika["pagination_complete"])
  paprika["pagination_complete"])
    print("Liquiditätsfilter:", paprika["liquidity_filter_usd"], "USD")

    if paprika["max_pages_reached"]:
        print(
            "WARNUNG: Seitenlimit erreicht. "
            "Weitere Pools wurden noch nicht geladen."
        )

    print("Abgerufene Seiten:", paprika["pages_fetched"])
    print("Pools insgesamt:", len(pools))
    print("Doppelte Pools entfernt:", paprika["duplicates_removed"])
    print("Weitere Seiten vorhanden:", paprika["has_next_page"])
    print("Marktkapitalisierungen gefunden:", market_cap_found)

    print("\nMARKTKAPITALITÄTS-DIAGNOSE:")

    if prepared:
        diagnostics = prepared[0]["market_cap_diagnostics"]
    else:
        diagnostics = {
            "requested_unique_tokens": 0,
            "batches_requested": 0,
            "token_objects_returned": 0,
            "requested_tokens_returned": 0,
            "tokens_with_market_cap": 0,
            "tokens_with_null_market_cap": 0,
            "tokens_with_missing_market_cap_field": 0,
            "tokens_with_invalid_market_cap": 0,
            "requested_tokens_not_returned": 0,
            "unrequested_token_objects_ignored": 0,
            "api_error": None,
        }

    print(
        "Eindeutige Token angefragt:",
        diagnostics["requested_unique_tokens"],
    )
    print(
        "API-Abfragen:",
        diagnostics["batches_requested"],
    )
    print(
        "Token-Objekte von API erhalten:",
        diagnostics["token_objects_returned"],
    )
    print(
        "Angefragte Token zurückgegeben:",
        diagnostics["requested_tokens_returned"],
    )
    print(
        "Mit Marktkapitalisierung:",
        diagnostics["tokens_with_market_cap"],
    )
    print(
        "Marktkapitalisierung ausdrücklich null:",
        diagnostics["tokens_with_null_market_cap"],
    )
    print(
        "Feld market_cap_usd fehlt:",
        diagnostics["tokens_with_missing_market_cap_field"],
    )
    print(
        "Ungültige Marktkapitalisierung:",
        diagnostics["tokens_with_invalid_market_cap"],
    )
    print(
        "Angefragte Token nicht zurückgegeben:",
        diagnostics["requested_tokens_not_returned"],
    )
    print(
        "Nicht angefragte Token ignoriert:",
        diagnostics["unrequested_token_objects_ignored"],
    )

    if diagnostics["api_error"]:
        print("API-Fehler:", diagnostics["api_error"])

    security_completed = sum(
        p.get("security_check_status") == "completed"
        for p in prepared
    )

    security_errors = sum(
        p.get("security_check_status") == "error"
        for p in prepared
    )

    security_skipped = sum(
        p.get("security_check_status") in (
            "skipped_run_limit",
            "skipped_missing_mint",
        )
        for p in prepared
    )

    security_disabled = sum(
        p.get("security_check_status") == "not_enabled"
        for p in prepared
    )

    print("\nSICHERHEITSPRÜFUNGEN:")
    print("RugCheck-Prüfungen abgeschlossen:", security_completed)
    print("RugCheck-Fehler:", security_errors)
    print("RugCheck übersprungen:", security_skipped)
    print("RugCheck nicht aktiviert:", security_disabled)

    print("\nERGEBNIS DER VORPRÜFUNG:")
    print("Vorprüfung abgelehnt:", rejected)
    print("Unvollständig / weitere Daten nötig:", incomplete)
    print("Bereit für vollständige Risikoprüfung:", ready)

    errors = [
        p.get("market_data_error")
        for p in prepared
        if p.get("market_data_error")
    ]

    if errors:
        print(
            "\nWARNUNG: GeckoTerminal-Marktdaten "
            "konnten nicht vollständig geladen werden."
        )
        print("Fehler:", errors[0])

    if prepared:
        print("\nBEISPIELE DER AUFBEREITETEN DATEN:")

        for item in prepared[:3]:
            print(
                json.dumps(
                    item,
                    indent=2,
                    ensure_ascii=False,
                )
            )

    print("\nHinweis: FDV ist nicht Marktkapitalisierung.")
    print("Live-Trading: Deaktiviert")
    print("Paper-Trading: Nicht aktiviert")
