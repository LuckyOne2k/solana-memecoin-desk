"""Testdaten-Bausteine für Prüfkette, Holder und Marktkapitalisierung."""

SUPPLY_RAW = 1_000_000_000_000_000  # 1 Mrd. Token mit 6 Dezimalstellen
DECIMALS = 6
POOL_ADDRESS = "POOL1"
POOL_VAULT = "POOLVAULT"


def amount(pct, supply=SUPPLY_RAW):
    return int(round(supply * pct / 100))


def make_labels(
    pool_vaults=(POOL_VAULT,),
    pool_owners=(POOL_ADDRESS,),
    exchanges=(),
    lockers=(),
    complete=True,
):
    return {
        "pool_vault_accounts": list(pool_vaults),
        "pool_owner_addresses": list(pool_owners),
        "exchange_addresses": list(exchanges),
        "locker_addresses": list(lockers),
        "invalid_label_rows": 0 if complete else 1,
        "labels_complete": complete,
    }


def make_security(
    mint="TOKEN123",
    mint_revoked=True,
    freeze_revoked=None,
    lp_locked=True,
    creator_address="CREATOR",
    market_liquidity=None,
    price=None,
    labels=None,
):
    return {
        "mint": mint,
        "mint_authority_revoked": mint_revoked,
        "freeze_authority_revoked": freeze_revoked,
        "lp_burned_or_locked": lp_locked,
        "creator_address": creator_address,
        "holder_labels": labels if labels is not None else make_labels(),
        "market_liquidity_usd_by_pool": (
            market_liquidity
            if market_liquidity is not None
            else {POOL_ADDRESS: 50000.0}
        ),
        "rugcheck_price_usd": price,
    }


def make_snapshot(
    mint="TOKEN123",
    holders=None,
    pool_pct=None,
    burn_pct=0.0,
    returned=None,
    supply=SUPPLY_RAW,
    mapping_complete=True,
    extra_rows=(),
):
    """holders: Liste von (owner, Anteil in %). Jedes Konto ist eine Zeile.

    pool_pct=None: Das Pool-Konto erhält den Rest der Gesamtmenge, die
    Stichprobe erfasst also alles (vollständige Verteilung).
    Ein fester pool_pct lässt den Rest als nicht erfasste Menge.
    """
    holders = holders if holders is not None else [
        (f"HOLDER{i}", 1.0) for i in range(10)
    ]

    if pool_pct is None:
        extra_pct = sum(
            row["amount"] for row in extra_rows
        ) / supply * 100
        pool_pct = 100.0 - burn_pct - extra_pct - sum(p for _, p in holders)

    rows = []

    if pool_pct:
        rows.append(
            {"address": POOL_VAULT, "owner": POOL_ADDRESS, "amount": amount(pool_pct, supply)}
        )

    if burn_pct:
        rows.append(
            {
                "address": "BURNACCOUNT",
                "owner": "1nc1nerator11111111111111111111111111111111",
                "amount": amount(burn_pct, supply),
            }
        )

    for index, (owner, pct) in enumerate(holders):
        rows.append(
            {"address": f"ACC_{owner}_{index}", "owner": owner, "amount": amount(pct, supply)}
        )

    rows.extend(extra_rows)

    # Rundungsreste dem Pool-Konto zuschlagen, damit eine vollständige
    # Verteilung exakt der Gesamtmenge entspricht.
    if rows and rows[0]["address"] == POOL_VAULT:
        difference = supply - sum(row["amount"] for row in rows)
        if 0 < abs(difference) < 10 and pool_pct and pool_pct > 0:
            rows[0]["amount"] += difference
    rows.sort(key=lambda row: row["amount"], reverse=True)

    return {
        "mint": mint,
        "largest_accounts_returned": returned if returned is not None else len(rows),
        "sample_owner_mapping_complete": mapping_complete,
        "accounts": rows,
        "supply_amount_raw": supply,
        "supply_decimals": DECIMALS,
        "min_sampled_amount_raw": min(row["amount"] for row in rows) if rows else None,
        "top10_share_fully_verified": False,
        "data_scope": "largest_20_token_accounts_sample",
    }


TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


def make_full_list(mint="TOKEN123", token_program=TOKEN_PROGRAM, **kwargs):
    """Wie make_snapshot, aber als vollständige Holder-Liste
    (Rückgabeform von fetch_full_holder_list)."""
    snapshot = make_snapshot(mint=mint, **kwargs)
    snapshot.pop("largest_accounts_returned", None)
    snapshot.pop("min_sampled_amount_raw", None)
    snapshot.pop("top10_share_fully_verified", None)
    snapshot.update(
        {
            "data_scope": "full_token_account_list",
            "token_program": token_program,
            "sample_owner_mapping_complete": True,
            "pages": 1,
            "accounts_total": len(snapshot["accounts"]),
            "accounts_nonzero": len(snapshot["accounts"]),
            "slot_supply_before": 1000,
            "page_slots": [1000],
            "slot_supply_after": 1001,
            "slot_span": 1,
        }
    )
    return snapshot


def make_mint_info(
    mint="TOKEN123",
    token_program=TOKEN_PROGRAM,
    extensions=None,
    delegate_status="absent",
    delegate=None,
    readable=True,
):
    """Rückgabeform von fetch_mint_info."""
    names = list(extensions or []) if readable else None
    return {
        "mint": mint,
        "token_program": token_program,
        "decimals": DECIMALS,
        "mint_info_slot": 999,
        "token2022_extensions": names,
        "unknown_extensions": [] if readable else None,
        "extensions_readable": readable,
        "permanent_delegate_status": delegate_status,
        "permanent_delegate": delegate,
    }
