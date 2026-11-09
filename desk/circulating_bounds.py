"""Konservative On-Chain-Untergrenze der Umlaufmenge (Wallet-Untergrenze).

Interne Definition (vom Nutzer freigegeben): Frei übertragbare Bestände
in technisch bestätigten normalen Wallets bilden eine konservative
UNTERGRENZE der Umlaufmenge. Sie ist ausdrücklich KEINE offizielle oder
tatsächliche zirkulierende Menge und wird nie so ausgegeben.

Datengrundlage: die vollständige Holder-Liste (desk/solana_rpc.py,
fetch_full_holder_list). Jede Zeile enthält
- "address": das Token-Konto,
- "owner": die Token-AUTHORITY aus den Kontodaten (Byte 32-63), NICHT
  den technischen Account-Owner (der ist immer das Token-Programm).

Ablauf:
1. Token-Konten je Authority zusammenfassen (vor jeder Klassifizierung).
2. Authorities herausnehmen (zählen nie zur Untergrenze):
   - Pool: Konto ist ein Pool-Vault (RugCheck) oder die Authority ist
     eine Pool-Adresse (RugCheck markets/AMM, DexPaprika-Pool),
   - Burn-Adressen (desk.holders.BURN_OWNERS),
   - Creator, Börsen, Locker (RugCheck-Kennzeichnungen),
   - die 10 größten Authorities (wie desk.holders: ohne Pool und Burn),
   - eingefrorene Token-Konten (Status-Byte 2) werden abgezogen.
3. Off-Curve-Authorities (PDA, nur ein Programm kann signieren) ohne
   RPC-Abfrage ausschließen.
4. Restliche Authorities absteigend nach Bestand in Blöcken zu höchstens
   100 per getMultipleAccounts abfragen (höchstens max_calls Aufrufe).
   Gezählt wird nur ein Konto mit
     owner = System Program, nicht ausführbar, 0 Byte Daten, lamports > 0.
   Alles andere (Programm, Token-Konto, anderes Programm als Owner,
   System-Konto mit Daten, Konto ohne SOL = "null") zählt NICHT und
   bleibt unbekannt.
5. Abbruch, sobald die Zielmenge erreicht ist (Kosten sparen).

Jede fehlende, widersprüchliche oder zu alte Antwort -> Fehler mit
festem Grund; es gibt nie ein Teilergebnis. Die Untergrenze kann die
Marktkapitalisierungs-Prüfung nur zusammen mit der Obergrenze und nur
über desk.market_cap.evaluate_onchain_bounds bestätigen.

Grenzen: Wallets können Smart-Wallets oder Börsen ohne Kennzeichnung
sein; ein Keypair-Wallet kann einem Projekt gehören. Das macht die
Untergrenze nicht falsch, solange die Token frei übertragbar sind,
aber "Umlauf" ist hier eine technische, keine wirtschaftliche Aussage.
"""

from desk.holders import BURN_OWNERS, FULL_LIST_SCOPE, TOP_N
from desk.solana_rpc import (
    MAX_ACCOUNTS_PER_CALL,
    SYSTEM_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    OnchainBoundsError,
    is_on_curve,
)


COMPLETED = "completed"
ERROR = "error"

# Klassen einer Authority
WALLET = "wallet"
OFF_CURVE = "off_curve_pda"
EXECUTABLE = "executable_program"
TOKEN_ACCOUNT = "token_program_account"
PROGRAM_OWNED = "program_owned_account"
SYSTEM_WITH_DATA = "system_account_with_data"
UNFUNDED = "no_sol_account_unknown"

# Gründe, eine Authority vor der RPC-Abfrage herauszunehmen
EXCLUDED_POOL = "pool"
EXCLUDED_BURN = "burn"
EXCLUDED_CREATOR = "creator"
EXCLUDED_EXCHANGE = "exchange"
EXCLUDED_LOCKER = "locker"
EXCLUDED_TOP10 = "top10"

TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID})

NOTE = (
    "internal_conservative_lower_bound_not_official_circulating_supply"
)


def classify_authority_account(info):
    """Kontodaten einer Authority (fetch_authority_accounts) -> Klasse.

    Nur WALLET zählt zur Untergrenze.
    """
    if info is None:
        return UNFUNDED

    if info["executable"]:
        return EXECUTABLE

    if info["owner"] in TOKEN_PROGRAMS:
        return TOKEN_ACCOUNT

    if info["owner"] != SYSTEM_PROGRAM_ID:
        return PROGRAM_OWNED

    if info["data_length"] != 0:
        return SYSTEM_WITH_DATA

    if info["lamports"] <= 0:
        return UNFUNDED

    return WALLET


def _valid_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _input_error(snapshot, security, mint):
    """Grund, warum keine Untergrenze berechnet werden darf; sonst None."""
    if not isinstance(snapshot, dict):
        return "holder_list_missing"

    if snapshot.get("mint") != mint:
        return "holder_list_mismatch"

    if snapshot.get("data_scope") != FULL_LIST_SCOPE:
        return "full_holder_list_required"

    if snapshot.get("sample_owner_mapping_complete") is not True:
        return "owner_mapping_incomplete"

    if snapshot.get("token_program") not in TOKEN_PROGRAMS:
        return "unsupported_token_program"

    for key in ("slot_supply_before", "slot_supply_after"):
        if not _valid_int(snapshot.get(key)) or snapshot[key] < 0:
            return "holder_list_slot_missing"

    supply = snapshot.get("supply_amount_raw")
    accounts = snapshot.get("accounts")

    if not _valid_int(supply) or supply <= 0 or not isinstance(accounts, list):
        return "holder_list_incomplete"

    total = 0

    for row in accounts:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("address"), str)
            or not isinstance(row.get("owner"), str)
            or not _valid_int(row.get("amount"))
            or row["amount"] < 0
        ):
            return "invalid_account_rows"

        total += row["amount"]

    # Vollständige Liste: Summe = Gesamtmenge (nicht erfasste Menge 0)
    if total != supply:
        return "full_list_incomplete"

    labels = security.get("holder_labels") if isinstance(security, dict) else None

    if not isinstance(labels, dict) or labels.get("labels_complete") is not True:
        return "pool_labels_incomplete"

    return None


def _aggregate(snapshot, security, pool_addresses, frozen_accounts):
    """Token-Konten je Authority, mit Ausschlussgrund.

    Rückgabe: dict authority -> {amount, frozen_amount, accounts, excluded}
    """
    labels = security["holder_labels"]
    vaults = set(labels.get("pool_vault_accounts") or [])
    pool_owners = set(labels.get("pool_owner_addresses") or [])
    pool_owners.update(a for a in pool_addresses if isinstance(a, str) and a)
    exchanges = set(labels.get("exchange_addresses") or [])
    lockers = set(labels.get("locker_addresses") or [])
    creator = security.get("creator_address")

    authorities = {}

    for row in snapshot["accounts"]:
        authority = row["owner"]
        entry = authorities.setdefault(
            authority,
            {
                "amount": 0,
                "frozen_amount": 0,
                "accounts": 0,
                "excluded": None,
            },
        )
        entry["amount"] += row["amount"]
        entry["accounts"] += 1

        if row["address"] in frozen_accounts:
            entry["frozen_amount"] += row["amount"]

        # Ein Pool-Vault macht die ganze Authority zum Pool (konservativ).
        if row["address"] in vaults:
            entry["excluded"] = EXCLUDED_POOL

    for authority, entry in authorities.items():
        if entry["excluded"]:
            continue

        if authority in pool_owners:
            entry["excluded"] = EXCLUDED_POOL
        elif authority in BURN_OWNERS:
            entry["excluded"] = EXCLUDED_BURN
        elif creator and authority == creator:
            entry["excluded"] = EXCLUDED_CREATOR
        elif authority in exchanges:
            entry["excluded"] = EXCLUDED_EXCHANGE
        elif authority in lockers:
            entry["excluded"] = EXCLUDED_LOCKER

    # Top-10 wie desk.holders: alle außer Pool und Burn, nach Bestand.
    ranked = sorted(
        (
            (authority, entry)
            for authority, entry in authorities.items()
            if entry["excluded"] not in (EXCLUDED_POOL, EXCLUDED_BURN)
        ),
        key=lambda pair: (-pair[1]["amount"], pair[0]),
    )

    for authority, entry in ranked[:TOP_N]:
        if entry["excluded"] is None:
            entry["excluded"] = EXCLUDED_TOP10

    return authorities


def compute_wallet_lower_bound(
    *,
    mint,
    snapshot,
    security,
    pool_addresses,
    target_amount_raw,
    max_calls,
    max_slot_span,
    fetch_accounts,
    fetch_frozen,
):
    """Wallet-Untergrenze (Rohmenge) mit Protokoll.

    target_amount_raw: Menge, ab der die Entscheidung feststeht
    (früher Abbruch). fetch_accounts/fetch_frozen: Abruffunktionen aus
    desk.solana_rpc (in Tests ersetzbar).

    status "completed": wallet_lower_bound_raw ist belegt (kann 0 sein).
    status "error": kein Wert; reason nennt den festen Grund.
    """
    result = {
        "status": ERROR,
        "reason": None,
        "note": NOTE,
        "wallet_lower_bound_raw": None,
        "target_amount_raw": target_amount_raw,
        "target_reached": False,
        "account_calls": 0,
        "max_account_calls": max_calls,
        "frozen_check_calls": 0,
        "authorities_total": 0,
        "authorities_queried": 0,
        "classified": {},
        "excluded_authorities": {},
        "excluded_amount_raw": {},
        "frozen_accounts": 0,
        "frozen_amount_raw": 0,
        "candidate_amount_raw": 0,
        "unqueried_candidates": 0,
        "slots": [],
        "slot_span": None,
    }

    error = _input_error(snapshot, security, mint)

    if error is None and (not _valid_int(target_amount_raw) or target_amount_raw <= 0):
        error = "target_invalid"

    if error is None and (not _valid_int(max_calls) or max_calls <= 0):
        error = "call_limit_invalid"

    if error is not None:
        result["reason"] = error
        return result

    min_slot = snapshot["slot_supply_after"]
    last_slot = min_slot

    def check_slot(slot):
        nonlocal last_slot

        if not _valid_int(slot) or slot < last_slot:
            raise OnchainBoundsError("slot_inconsistent")

        last_slot = slot
        result["slots"].append(slot)

    try:
        result["frozen_check_calls"] = 1
        frozen = fetch_frozen(
            mint, snapshot["token_program"], min_slot=min_slot
        )

        if not isinstance(frozen, dict) or not isinstance(
            frozen.get("addresses"), (set, frozenset, list)
        ):
            raise OnchainBoundsError("frozen_response_invalid")

        check_slot(frozen.get("slot"))
        frozen_accounts = set(frozen["addresses"])

        authorities = _aggregate(
            snapshot, security, pool_addresses or (), frozen_accounts
        )
        result["authorities_total"] = len(authorities)
        result["frozen_accounts"] = sum(
            1 for row in snapshot["accounts"] if row["address"] in frozen_accounts
        )
        result["frozen_amount_raw"] = sum(
            entry["frozen_amount"] for entry in authorities.values()
        )

        candidates = []

        for authority, entry in authorities.items():
            reason = entry["excluded"]

            if reason is None:
                try:
                    on_curve = is_on_curve(authority)
                except ValueError:
                    raise OnchainBoundsError("invalid_authority_address") from None

                if not on_curve:
                    reason = OFF_CURVE

            if reason is not None:
                result["excluded_authorities"][reason] = (
                    result["excluded_authorities"].get(reason, 0) + 1
                )
                result["excluded_amount_raw"][reason] = (
                    result["excluded_amount_raw"].get(reason, 0) + entry["amount"]
                )
                continue

            free_amount = entry["amount"] - entry["frozen_amount"]

            if free_amount > 0:
                candidates.append((authority, free_amount))

        # Größte zuerst; bei Gleichstand stabil nach Adresse.
        candidates.sort(key=lambda pair: (-pair[1], pair[0]))
        result["candidate_amount_raw"] = sum(amount for _, amount in candidates)

        lower = 0
        position = 0

        # Zielmenge auch mit allen Kandidaten unerreichbar: keine Abfrage.
        if result["candidate_amount_raw"] >= target_amount_raw:
            while position < len(candidates) and lower < target_amount_raw:
                if result["account_calls"] >= max_calls:
                    break

                chunk = candidates[position:position + MAX_ACCOUNTS_PER_CALL]
                addresses = [authority for authority, _ in chunk]

                result["account_calls"] += 1
                response = fetch_accounts(addresses, min_slot=min_slot)

                if not isinstance(response, dict):
                    raise OnchainBoundsError("authority_response_invalid")

                check_slot(response.get("slot"))
                infos = response.get("accounts")

                if not isinstance(infos, list) or len(infos) != len(chunk):
                    raise OnchainBoundsError("authority_response_invalid")

                for (authority, amount), info in zip(chunk, infos):
                    if info is not None and not isinstance(info, dict):
                        raise OnchainBoundsError("authority_account_invalid")

                    try:
                        category = classify_authority_account(info)
                    except (KeyError, TypeError):
                        raise OnchainBoundsError("authority_account_invalid") from None

                    result["classified"][category] = (
                        result["classified"].get(category, 0) + 1
                    )

                    if category == WALLET:
                        lower += amount

                position += len(chunk)
                result["authorities_queried"] = position

        result["unqueried_candidates"] = len(candidates) - position

        span = last_slot - snapshot["slot_supply_before"]
        result["slot_span"] = span

        if span > max_slot_span:
            raise OnchainBoundsError("slot_span_exceeded")

    except OnchainBoundsError as failure:
        result["reason"] = failure.reason
        return result

    result.update(
        status=COMPLETED,
        wallet_lower_bound_raw=lower,
        target_reached=lower >= target_amount_raw,
        reason=(
            "target_reached"
            if lower >= target_amount_raw
            else "target_unreachable"
            if result["candidate_amount_raw"] < target_amount_raw
            else "call_limit_reached"
            if result["unqueried_candidates"] > 0
            else "target_not_reached"
        ),
    )
    return result
