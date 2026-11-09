
import base64
import binascii
import os
import struct
import time

import requests


class SolanaRpcError(Exception):
    pass


def _resolve_rpc_url(rpc_url=None):
    """RPC-Adresse aus Argument oder SOLANA_RPC_URL.

    Kein stilles Zurückfallen auf die öffentliche RPC: Fehlt die Adresse
    oder ist sie ungültig, schlägt die Abfrage fehl und die Holder-Prüfung
    bleibt unvollständig. Der Wert selbst wird nie in Meldungen übernommen.
    """
    url = (rpc_url or os.getenv("SOLANA_RPC_URL") or "").strip()

    if not url:
        raise SolanaRpcError("SOLANA_RPC_URL is not set")

    if not url.startswith("https://"):
        raise SolanaRpcError("SOLANA_RPC_URL is not an https URL")

    return url


def _safe_request_error(error, status_code):
    message = f"Solana RPC request failed: {type(error).__name__}"

    if status_code is not None:
        message += f" (HTTP {status_code})"

    return message


def _rpc_call(method, params, rpc_url=None, timeout=20):
    url = _resolve_rpc_url(rpc_url)

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


# ---------------------------------------------------------------------
# Vollständige Holder-Liste (Helius getProgramAccountsV2)
# Plan: docs/helius_integration_plan.md, Abschnitt 1 und 2.
# Noch nicht in die Prüfkette eingebunden (PR B).
# ---------------------------------------------------------------------

TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

CLASSIC_ACCOUNT_SIZE = 165
# Grundgröße eines Mint-Kontos ohne Erweiterungen
BASE_MINT_SIZE = 82
PAGE_LIMIT = 1000
# dataSlice: Owner (32 Byte) + Menge (u64 little-endian, 8 Byte)
SLICE_OFFSET = 32
SLICE_LENGTH = 40

PERMANENT_DELEGATE_EXTENSION = "permanentDelegate"
UNPARSEABLE_EXTENSION = "unparseableExtension"

# Namen der Token-2022-Erweiterungen, wie sie jsonParsed ausgibt.
# Andere Namen werden als unbekannt erfasst.
KNOWN_TOKEN_2022_EXTENSIONS = frozenset(
    {
        "transferFeeConfig",
        "transferFeeAmount",
        "mintCloseAuthority",
        "confidentialTransferMint",
        "confidentialTransferAccount",
        "confidentialTransferFeeConfig",
        "confidentialTransferFeeAmount",
        "confidentialMintBurn",
        "defaultAccountState",
        "immutableOwner",
        "memoTransfer",
        "nonTransferable",
        "nonTransferableAccount",
        "interestBearingConfig",
        "cpiGuard",
        "permanentDelegate",
        "transferHook",
        "transferHookAccount",
        "metadataPointer",
        "tokenMetadata",
        "groupPointer",
        "tokenGroup",
        "groupMemberPointer",
        "tokenGroupMember",
        "scaledUiAmountConfig",
        "pausableConfig",
        "pausableAccount",
    }
)

ABSENT = "absent"
PRESENT = "present"
UNKNOWN = "unknown"

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class HolderListError(SolanaRpcError):
    """Vollständige Holder-Liste nicht belegbar.

    `reason` ist ein fester Grund ohne URL, Key oder Rohdaten. Es gibt
    nie ein Teilergebnis.
    """

    def __init__(self, reason):
        super().__init__(f"Full holder list rejected: {reason}")
        self.reason = reason


def _b58encode(data):
    number = int.from_bytes(data, "big")
    encoded = ""

    while number:
        number, rest = divmod(number, 58)
        encoded = _B58_ALPHABET[rest] + encoded

    leading_zeros = len(data) - len(data.lstrip(b"\0"))
    return "1" * leading_zeros + encoded


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _context_slot(result):
    """Slot aus einer RpcResponse {context: {slot}, value}; sonst None."""
    if not isinstance(result, dict):
        return None

    context = result.get("context")

    if not isinstance(context, dict):
        return None

    slot = context.get("slot")
    return slot if _is_int(slot) and slot >= 0 else None


def _call(method, params, rpc_url):
    """RPC-Aufruf; Fehler werden zu festem Grund ohne URL."""
    try:
        return _rpc_call(method, params, rpc_url=rpc_url)
    except SolanaRpcError:
        raise HolderListError("rpc_error") from None


def _read_extensions(info, space):
    """Token-2022-Erweiterungen -> (Namen, unbekannte, Delegate-Status, Delegate)."""
    extensions = info.get("extensions")

    if extensions is None:
        # Ohne Erweiterungen hat das Mint-Konto genau die Grundgröße.
        if _is_int(space) and space == BASE_MINT_SIZE:
            return [], [], ABSENT, None
        return None, None, UNKNOWN, None

    if not isinstance(extensions, list):
        return None, None, UNKNOWN, None

    names = []
    unknown = []
    delegate_status = ABSENT
    delegate = None

    for entry in extensions:
        name = entry.get("extension") if isinstance(entry, dict) else None

        if not isinstance(name, str) or not name:
            return None, None, UNKNOWN, None

        names.append(name)

        if name == PERMANENT_DELEGATE_EXTENSION:
            delegate_status = PRESENT
            state = entry.get("state")
            address = (
                state.get("delegate") if isinstance(state, dict) else None
            )
            delegate = address if isinstance(address, str) and address else None
        elif name not in KNOWN_TOKEN_2022_EXTENSIONS:
            unknown.append(name)

    # Eine nicht lesbare Erweiterung könnte ein Delegate sein.
    if delegate_status == ABSENT and UNPARSEABLE_EXTENSION in names:
        delegate_status = UNKNOWN

    return names, unknown, delegate_status, delegate


def fetch_mint_info(mint, rpc_url=None):
    """Token-Programm, Decimals und Token-2022-Erweiterungen des Mints.

    Permanent Delegate: "present" (Adresse kann None sein, zählt
    trotzdem), "absent" nur bei belegtem Fehlen, sonst "unknown".
    """
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    result = _call(
        "getAccountInfo",
        [mint, {"encoding": "jsonParsed", "commitment": "finalized"}],
        rpc_url,
    )

    slot = _context_slot(result)

    if slot is None:
        raise HolderListError("mint_slot_missing")

    value = result.get("value")

    if not isinstance(value, dict):
        raise HolderListError("mint_not_found")

    program = value.get("owner")

    if program not in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        raise HolderListError("unsupported_token_program")

    data = value.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    info = parsed.get("info") if isinstance(parsed, dict) else None

    if (
        not isinstance(parsed, dict)
        or parsed.get("type") != "mint"
        or not isinstance(info, dict)
    ):
        raise HolderListError("mint_data_invalid")

    decimals = info.get("decimals")

    if not _is_int(decimals) or decimals < 0:
        raise HolderListError("mint_data_invalid")

    if program == TOKEN_PROGRAM_ID:
        names, unknown, delegate_status, delegate = [], [], ABSENT, None
    else:
        names, unknown, delegate_status, delegate = _read_extensions(
            info, value.get("space")
        )

    return {
        "mint": mint,
        "token_program": program,
        "decimals": decimals,
        "mint_info_slot": slot,
        "token2022_extensions": names,
        "unknown_extensions": unknown,
        "extensions_readable": names is not None,
        "permanent_delegate_status": delegate_status,
        "permanent_delegate": delegate,
    }


def _fetch_supply(mint, rpc_url, min_slot=None):
    config = {"commitment": "finalized"}

    if min_slot is not None:
        config["minContextSlot"] = min_slot

    result = _call("getTokenSupply", [mint, config], rpc_url)
    slot = _context_slot(result)

    if slot is None:
        raise HolderListError("supply_slot_missing")

    value = result.get("value")
    amount = _valid_amount(value.get("amount")) if isinstance(value, dict) else None
    decimals = value.get("decimals") if isinstance(value, dict) else None

    if amount is None or amount <= 0 or not _is_int(decimals):
        raise HolderListError("supply_invalid")

    return amount, decimals, slot


def _decode_account(item, program):
    """Ein Konto der Seite -> (Adresse, Owner, Menge); sonst Fehler."""
    if not isinstance(item, dict):
        raise HolderListError("invalid_account_data")

    address = item.get("pubkey")
    account = item.get("account")

    if not isinstance(address, str) or not address or not isinstance(account, dict):
        raise HolderListError("invalid_account_data")

    # Gehört das Konto erkennbar zu einem anderen Programm -> Fehler.
    if "owner" in account and account.get("owner") != program:
        raise HolderListError("invalid_account_data")

    data = account.get("data")

    if (
        not isinstance(data, list)
        or len(data) != 2
        or data[1] != "base64"
        or not isinstance(data[0], str)
    ):
        raise HolderListError("invalid_account_data")

    try:
        raw = base64.b64decode(data[0], validate=True)
    except (binascii.Error, ValueError):
        raise HolderListError("invalid_account_data") from None

    if len(raw) != SLICE_LENGTH:
        raise HolderListError("invalid_account_data")

    owner = _b58encode(raw[:32])
    amount = struct.unpack("<Q", raw[32:40])[0]

    return address, owner, amount


def _page_parts(result):
    """Erwartete Form (withContext): {context: {slot}, value: {accounts,
    paginationKey}}. Jede andere Form -> Fehler, kein Ersatzweg."""
    slot = _context_slot(result)

    if slot is None:
        raise HolderListError("page_slot_missing")

    value = result.get("value")

    if not isinstance(value, dict):
        raise HolderListError("page_response_invalid")

    accounts = value.get("accounts")

    if not isinstance(accounts, list):
        raise HolderListError("page_response_invalid")

    key = value.get("paginationKey")

    if key is not None and (not isinstance(key, str) or not key):
        raise HolderListError("page_response_invalid")

    return slot, accounts, key


def fetch_full_holder_list(
    mint,
    mint_info,
    *,
    max_pages,
    max_slot_span,
    rpc_url=None,
):
    """Alle Token-Konten des Mints mit Owner und Menge.

    Gibt nur ein vollständig geprüftes Ergebnis zurück, sonst
    HolderListError mit festem Grund (siehe Plan, Abschnitt 1.3).

    Grenze: Die Seiten werden nacheinander gelesen; das Ergebnis ist
    keine atomare Momentaufnahme. Gegenläufige Überweisungen gleicher
    Höhe können sich in der Summe ausgleichen. Die Slot-Spanne begrenzt
    dieses Risiko, schließt es aber nicht aus.
    """
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("A token mint address is required")

    for name, value in (("max_pages", max_pages), ("max_slot_span", max_slot_span)):
        if not _is_int(value) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    if not isinstance(mint_info, dict) or mint_info.get("mint") != mint:
        raise HolderListError("mint_info_mismatch")

    program = mint_info.get("token_program")

    if program not in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        raise HolderListError("unsupported_token_program")

    supply_before, decimals, slot_before = _fetch_supply(mint, rpc_url)

    if decimals != mint_info.get("decimals"):
        raise HolderListError("decimals_mismatch")

    filters = [{"memcmp": {"offset": 0, "bytes": mint}}]

    # Token-2022-Konten mit Erweiterungen sind größer als 165 Byte.
    if program == TOKEN_PROGRAM_ID:
        filters.append({"dataSize": CLASSIC_ACCOUNT_SIZE})

    page_slots = []
    seen = set()
    accounts = []
    accounts_total = 0
    key = None

    while True:
        if len(page_slots) >= max_pages:
            raise HolderListError("too_many_pages")

        config = {
            "encoding": "base64",
            "commitment": "finalized",
            "withContext": True,
            "minContextSlot": slot_before,
            "limit": PAGE_LIMIT,
            "dataSlice": {"offset": SLICE_OFFSET, "length": SLICE_LENGTH},
            "filters": filters,
        }

        if key is not None:
            config["paginationKey"] = key

        result = _call("getProgramAccountsV2", [program, config], rpc_url)
        slot, items, key = _page_parts(result)

        if slot < slot_before:
            raise HolderListError("page_slot_before_supply")

        if page_slots and slot < page_slots[-1]:
            raise HolderListError("page_slot_inconsistent")

        page_slots.append(slot)

        for item in items:
            address, owner, amount = _decode_account(item, program)

            if address in seen:
                raise HolderListError("duplicate_account")

            seen.add(address)
            accounts_total += 1

            if amount > 0:
                accounts.append(
                    {"address": address, "owner": owner, "amount": amount}
                )

        if key is None:
            break

    supply_after, _, slot_after = _fetch_supply(
        mint, rpc_url, min_slot=page_slots[-1]
    )

    if slot_after < page_slots[-1]:
        raise HolderListError("supply_slot_inconsistent")

    slot_span = slot_after - slot_before

    if slot_span > max_slot_span:
        raise HolderListError("slot_span_exceeded")

    if supply_after != supply_before:
        raise HolderListError("supply_changed")

    if sum(row["amount"] for row in accounts) != supply_before:
        raise HolderListError("supply_sum_mismatch")

    return {
        "mint": mint,
        "data_scope": "full_token_account_list",
        "token_program": program,
        "accounts": accounts,
        "supply_amount_raw": supply_before,
        "supply_decimals": decimals,
        "sample_owner_mapping_complete": True,
        "pages": len(page_slots),
        "accounts_total": accounts_total,
        "accounts_nonzero": len(accounts),
        "slot_supply_before": slot_before,
        "page_slots": page_slots,
        "slot_supply_after": slot_after,
        "slot_span": slot_span,
    }
