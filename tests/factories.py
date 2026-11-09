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
    pool_pct=40.0,
    burn_pct=0.0,
    returned=None,
    supply=SUPPLY_RAW,
    mapping_complete=True,
    extra_rows=(),
):
    """holders: Liste von (owner, Anteil in %). Jedes Konto ist eine Zeile.

    returned: Anzahl der von der Blockchain gelieferten Konten.
    Standard: Anzahl der Zeilen (unter 20 = vollständige Holder-Menge).
    """
    holders = holders if holders is not None else [
        (f"HOLDER{i}", 1.0) for i in range(10)
    ]

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
