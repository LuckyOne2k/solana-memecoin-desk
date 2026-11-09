
import os
import time

import requests


DEFAULT_RPC_URL = "https://api.mainnet.solana.com"


class SolanaRpcError(Exception):
    pass


def _safe_request_error(error, status_code):
    message = f"Solana RPC request failed: {type(error).__name__}"

    if status_code is not None:
        message += f" (HTTP {status_code})"

    return message


def _rpc_call(method, params, rpc_url=None, timeout=20):
    url = rpc_url or os.getenv(
        "SOLANA_RPC_URL",
        DEFAULT_RPC_URL,
    )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    max_attempts = 3
    retryable_status_codes = {429, 500, 502, 503, 504}

    for attempt in range(max_attempts):
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Accept": "application/json"},
                timeout=timeout,
            )

            if (
                response.status_code in retryable_status_codes
                and attempt < max_attempts - 1
            ):
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            data = response.json()

            # Retry when the RPC node has not reached the requested slot yet.
            rpc_error = data.get("error") if isinstance(data, dict) else None
            rpc_error_code = (
                rpc_error.get("code")
                if isinstance(rpc_error, dict)
                else None
            )

            if (
                rpc_error_code == -32016
                and attempt < max_attempts - 1
            ):
                time.sleep(2 ** attempt)
                continue

            break

        except requests.RequestException as error:
            status_code = (
                error.response.status_code
                if error.response is not None
                else None
            )

            retryable = (
                status_code in retryable_status_codes
                or isinstance(
                    error,
                    (
                        requests.Timeout,
                        requests.ConnectionError,
                    ),
                )
            )

            if retryable and attempt < max_attempts - 1:
                time.sleep(2 ** attempt)
                continue

            # Die Fehlermeldung von requests enthält die vollständige
            # URL und damit einen möglichen API-Key. Deshalb nur Typ
            # und HTTP-Status weitergeben, nie die Meldung selbst.
            raise SolanaRpcError(
                _safe_request_error(error, status_code)
            ) from None

        except ValueError as error:
            raise SolanaRpcError(
                "Solana RPC returned invalid JSON"
            ) from error

    if not isinstance(data, dict):
        raise SolanaRpcError(
            "Solana RPC returned an unexpected response"
        )

    if data.get("error"):
        raise SolanaRpcError(
            f"Solana RPC error: {data['error']}"
        )

    if "result" not in data:
        raise SolanaRpcError(
            "Solana RPC response has no result"
        )

    return data["result"]

def _valid_amount(value):
    if not isinstance(value, str):
        return None

    if not value.isdigit():
        return None

    return int(value)


def analyze_largest_accounts(
    mint,
    largest_accounts,
    parsed_accounts,
    total_supply,
    creator_address=None,
):
    if not isinstance(largest_accounts, list):
        raise ValueError("Largest accounts must be a list")

    if not isinstance(parsed_accounts, list):
        raise ValueError("Parsed accounts must be a list")

    supply = _valid_amount(total_supply)

    if supply is None or supply <= 0:
        raise ValueError("Total supply must be positive")

    owner_balances = {}
    creator_balance = 0
    creator_found = False
    invalid_rows = 0
    valid_accounts = []

    if len(largest_accounts) != len(parsed_accounts):
        invalid_rows += abs(
            len(largest_accounts) - len(parsed_accounts)
        )

    for index, account in enumerate(largest_accounts):
        if not isinstance(account, dict):
            invalid_rows += 1
            continue

        amount = _valid_amount(account.get("amount"))

        if amount is None:
            invalid_rows += 1
            continue

        if index >= len(parsed_accounts):
            invalid_rows += 1
            continue

        parsed_account = parsed_accounts[index]

        if not isinstance(parsed_account, dict):
            invalid_rows += 1
            continue

        data = parsed_account.get("data")

        if not isinstance(data, dict):
            invalid_rows += 1
            continue

        parsed = data.get("parsed")

        if not isinstance(parsed, dict):
            invalid_rows += 1
            continue

        info = parsed.get("info")

        if not isinstance(info, dict):
            invalid_rows += 1
            continue

        if info.get("mint") != mint:
            invalid_rows += 1
            continue

        owner = info.get("owner")

        if not isinstance(owner, str) or not owner.strip():
            invalid_rows += 1
            continue

        address = account.get("address")

        if not isinstance(address, str) or not address.strip():
            invalid_rows += 1
            continue

        valid_accounts.append(
            {
                "address": address,
                "owner": owner,
                "amount": amount,
            }
        )

        owner_balances[owner] = (
            owner_balances.get(owner, 0) + amount
        )

        if creator_address and owner == creator_address:
            creator_balance += amount
            creator_found = True

    top10_observed_amount = sum(
        sorted(
            owner_balances.values(),
            reverse=True,
        )[:10]
    )

    top10_lower_bound_pct = (
        top10_observed_amount / supply * 100
    )

    creator_lower_bound_pct = None

    if creator_found:
        creator_lower_bound_pct = (
            creator_balance / supply * 100
        )

    return {
        "mint": mint,
        "largest_accounts_returned": len(largest_accounts),
        "distinct_owners_observed": len(owner_balances),
        "invalid_account_rows": invalid_rows,
        "sample_owner_mapping_complete": (
            len(largest_accounts) > 0
            and invalid_rows == 0
            and len(parsed_accounts) == len(largest_accounts)
        ),
        "top10_observed_owner_share_lower_bound_pct": (
            round(top10_lower_bound_pct, 4)
        ),
        "creator_share_lower_bound_pct": (
            round(creator_lower_bound_pct, 4)
            if creator_lower_bound_pct is not None
            else None
        ),
        "creator_found_in_sample": creator_found,
        "top10_share_fully_verified": False,
        "data_scope": "largest_20_token_accounts_sample",
        # Rohdaten für desk/holders.py (Zuordnung zu Pool, Burn, Holder)
        "accounts": valid_accounts,
        "supply_amount_raw": supply,
        "min_sampled_amount_raw": (
            min(row["amount"] for row in valid_accounts)
            if valid_accounts
            else None
        ),
    }


def fetch_holder_snapshot(
    mint,
    creator_address=None,
    rpc_url=None,
):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    largest_result = _rpc_call(
        "getTokenLargestAccounts",
        [
            mint,
            {"commitment": "finalized"},
        ],
        rpc_url=rpc_url,
    )

    if not isinstance(largest_result, dict):
        raise SolanaRpcError(
            "Invalid largest accounts response"
        )

    largest_accounts = largest_result.get("value")

    if not isinstance(largest_accounts, list) or not largest_accounts:
        raise SolanaRpcError(
            "No largest token accounts returned"
        )

    if len(largest_accounts) > 20:
        raise SolanaRpcError(
            "Unexpected number of largest token accounts"
        )

    addresses = []

    for account in largest_accounts:
        if not isinstance(account, dict):
            raise SolanaRpcError(
                "Invalid largest account entry"
            )

        address = account.get("address")

        if not isinstance(address, str) or not address.strip():
            raise SolanaRpcError(
                "Largest account has no valid address"
            )

        addresses.append(address)

    context = largest_result.get("context")
    slot = context.get("slot") if isinstance(context, dict) else None

    config = {
        "encoding": "jsonParsed",
        "commitment": "finalized",
    }

    if isinstance(slot, int):
        config["minContextSlot"] = slot

    parsed_result = _rpc_call(
        "getMultipleAccounts",
        [addresses, config],
        rpc_url=rpc_url,
    )

    if not isinstance(parsed_result, dict):
        raise SolanaRpcError(
            "Invalid parsed accounts response"
        )

    parsed_accounts = parsed_result.get("value")

    if not isinstance(parsed_accounts, list):
        raise SolanaRpcError(
            "Parsed accounts response has no value list"
        )

    supply_result = _rpc_call(
        "getTokenSupply",
        [
            mint,
            {
                "commitment": "finalized",
                **(
                    {"minContextSlot": slot}
                    if isinstance(slot, int)
                    else {}
                ),
            },
        ],
        rpc_url=rpc_url,
    )

    if not isinstance(supply_result, dict):
        raise SolanaRpcError(
            "Invalid token supply response"
        )

    supply_data = supply_result.get("value")

    if not isinstance(supply_data, dict):
        raise SolanaRpcError(
            "Token supply is missing"
        )

    try:
        snapshot = analyze_largest_accounts(
            mint=mint,
            largest_accounts=largest_accounts,
            parsed_accounts=parsed_accounts,
            total_supply=supply_data.get("amount"),
            creator_address=creator_address,
        )

    except ValueError as error:
        raise SolanaRpcError(
            f"Unable to analyze holder snapshot: {error}"
        ) from error

    snapshot["rpc_slot"] = slot
    snapshot["supply_decimals"] = supply_data.get("decimals")

    return snapshot
