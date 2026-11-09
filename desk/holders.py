"""Holder-Verteilung: Zuordnung, Aggregation und Bestätigung.

Datengrundlage: die 20 größten Token-Konten von der Blockchain
(desk/solana_rpc.py), ergänzt um Kennzeichnungen aus RugCheck
(desk/security.py).

Regeln:
- Token-Konten werden ihrem Eigentümer (owner) zugeordnet und je
  Eigentümer zusammengezählt.
- Herausgerechnet werden nur
  * Pool-Konten: Konto ist ein Pool-Vault laut RugCheck "markets",
    oder der Eigentümer ist eine Pool-Adresse (RugCheck "markets",
    DexPaprika-Pool) bzw. von RugCheck als AMM gekennzeichnet;
  * Burn-Konten: Eigentümer ist eine Adresse, von der Token
    nachweislich nicht mehr bewegt werden können.
- Börsenkonten werden nur bei ausdrücklicher Kennzeichnung erkannt,
  gesondert ausgewiesen, aber weiterhin mitgezählt (vorsichtig).
- Unbekannte Konten werden immer mitgezählt.
- Anteile beziehen sich auf die Gesamtmenge abzüglich nachweislich
  verbrannter Token. Pool-Bestände bleiben im Nenner (übliche
  Konvention); der Anteil am Bestand außerhalb der Pools wird
  zusätzlich zur Information ausgewiesen.

Bestätigt ist der Top-10-Anteil nur, wenn
- alle Konten der Stichprobe gültig einem Eigentümer zugeordnet sind,
- die RugCheck-Kennzeichnungen vollständig gelesen wurden und
- mindestens 10 mitgezählte Eigentümer in der Stichprobe stehen oder
  die Stichprobe alle Konten umfasst (weniger als 20 Konten).

Bekannte Grenze: Hält ein Eigentümer außerhalb der 20 größten Konten
weitere Konten, sind diese nicht enthalten. Verschiedene Wallets
derselben Person können nicht erkannt werden.
"""


# Eigentümer, deren Token nicht mehr bewegt werden können:
# - Solana Incinerator
# - System Program (kann nicht für den Token-Eigentümer signieren)
BURN_OWNERS = {
    "1nc1nerator11111111111111111111111111111111": "incinerator",
    "11111111111111111111111111111111": "system_program",
}

FULL_SAMPLE_SIZE = 20
TOP_N = 10

POOL = "pool"
BURN = "burn"
CREATOR = "creator"
EXCHANGE = "exchange"
LOCKER = "locker"
UNKNOWN = "unknown"


def _pct(amount, base):
    return round(amount / base * 100, 6) if base > 0 else None


def _labels(security):
    if not isinstance(security, dict):
        return None

    labels = security.get("holder_labels")

    if not isinstance(labels, dict):
        return None

    return labels


def classify_account(row, labels, pool_address, creator_address):
    address = row["address"]
    owner = row["owner"]

    if address in labels["pool_vault_accounts"]:
        return POOL

    if owner in labels["pool_owner_addresses"]:
        return POOL

    if pool_address and owner == pool_address:
        return POOL

    if owner in BURN_OWNERS:
        return BURN

    if creator_address and owner == creator_address:
        return CREATOR

    if owner in labels["exchange_addresses"]:
        return EXCHANGE

    if owner in labels["locker_addresses"]:
        return LOCKER

    return UNKNOWN


def evaluate_holders(*, snapshot, security, pool_address, risk_filters):
    top10_limit = risk_filters["reject_top10_share_above_pct"]
    dev_limit = risk_filters["reject_dev_share_above_pct"]

    labels = _labels(security)
    creator = (
        security.get("creator_address")
        if isinstance(security, dict)
        else None
    )

    accounts = snapshot.get("accounts")
    supply = snapshot.get("supply_amount_raw")
    returned = snapshot.get("largest_accounts_returned")

    result = {
        "top10_status": "unclear",
        "dev_status": "unclear",
        "rejection_reasons": [],
        "unconfirmed_reasons": [],
    }

    if (
        not isinstance(accounts, list)
        or not isinstance(supply, int)
        or isinstance(supply, bool)
        or supply <= 0
        or not isinstance(returned, int)
    ):
        result["unconfirmed_reasons"].append("holder_snapshot_incomplete")
        return result

    sample_complete = snapshot.get("sample_owner_mapping_complete") is True

    if not sample_complete:
        result["unconfirmed_reasons"].append("owner_mapping_incomplete")

    if labels is None:
        result["unconfirmed_reasons"].append("pool_labels_missing")
        labels = {
            "pool_vault_accounts": [],
            "pool_owner_addresses": [],
            "exchange_addresses": [],
            "locker_addresses": [],
            "labels_complete": False,
        }
    elif labels.get("labels_complete") is not True:
        result["unconfirmed_reasons"].append("pool_labels_incomplete")

    labels = {
        **labels,
        "pool_vault_accounts": set(labels.get("pool_vault_accounts") or []),
        "pool_owner_addresses": set(labels.get("pool_owner_addresses") or []),
        "exchange_addresses": set(labels.get("exchange_addresses") or []),
        "locker_addresses": set(labels.get("locker_addresses") or []),
    }

    pool_amount = 0
    burn_amount = 0
    owners = {}

    for row in accounts:
        category = classify_account(row, labels, pool_address, creator)

        if category == POOL:
            pool_amount += row["amount"]
            continue

        if category == BURN:
            burn_amount += row["amount"]
            continue

        entry = owners.setdefault(
            row["owner"],
            {"owner": row["owner"], "amount": 0, "category": category},
        )
        entry["amount"] += row["amount"]

    effective_supply = supply - burn_amount

    if effective_supply <= 0:
        result["unconfirmed_reasons"].append("no_supply_after_burns")
        return result

    ranked = sorted(
        owners.values(),
        key=lambda entry: entry["amount"],
        reverse=True,
    )

    top = ranked[:TOP_N]
    top_amount = sum(entry["amount"] for entry in top)
    non_pool_supply = effective_supply - pool_amount

    full_holder_set = returned < FULL_SAMPLE_SIZE
    enough_owners = len(ranked) >= TOP_N or full_holder_set

    if not enough_owners:
        result["unconfirmed_reasons"].append("fewer_than_10_counted_owners_in_sample")

    top10_confirmed = (
        sample_complete
        and labels["labels_complete"] is True
        and enough_owners
    )

    top10_pct = _pct(top_amount, effective_supply)

    result.update(
        {
            "total_supply_raw": supply,
            "burned_amount_raw": burn_amount,
            "pool_amount_raw": pool_amount,
            "pool_share_pct": _pct(pool_amount, effective_supply),
            "counted_owners_in_sample": len(ranked),
            "accounts_in_sample": returned,
            "top10_share_pct": top10_pct,
            "top10_share_of_non_pool_supply_pct": (
                _pct(top_amount, non_pool_supply)
                if non_pool_supply > 0
                else None
            ),
            "top10_confirmed": top10_confirmed,
            "top_holders": [
                {
                    "owner": entry["owner"],
                    "category": entry["category"],
                    "share_pct": _pct(entry["amount"], effective_supply),
                }
                for entry in top
            ],
            "exchange_owners_counted": sum(
                1 for entry in ranked if entry["category"] == EXCHANGE
            ),
        }
    )

    # Top-10: Ein Überschreiten ist auch mit unvollständiger Stichprobe
    # bewiesen (die mitgezählten Bestände existieren). Ein Einhalten nur
    # mit bestätigter Stichprobe.
    if top10_pct > top10_limit:
        result["top10_status"] = "rejected"
        result["rejection_reasons"].append(
            "top10_holder_share_above_limit"
            if top10_confirmed
            else "observed_top10_holder_share_above_limit"
        )
    elif top10_confirmed:
        result["top10_status"] = "confirmed"

    # Entwickler-Anteil
    if not creator:
        result["unconfirmed_reasons"].append("creator_unknown")
        result["dev_share_pct"] = None
        return result

    creator_entry = owners.get(creator)

    if creator_entry is not None:
        dev_pct = _pct(creator_entry["amount"], effective_supply)
        result["dev_share_pct"] = dev_pct
        result["dev_share_basis"] = "creator_in_sample"

        if dev_pct > dev_limit:
            result["dev_status"] = "rejected"
            result["rejection_reasons"].append("creator_share_above_limit")
        elif sample_complete:
            result["dev_status"] = "confirmed"

        return result

    if not sample_complete:
        result["dev_share_pct"] = None
        return result

    if full_holder_set:
        result["dev_share_pct"] = 0.0
        result["dev_share_basis"] = "creator_not_in_complete_holder_set"
        result["dev_status"] = "confirmed"
        return result

    # Creator nicht unter den 20 größten Konten: Ein einzelnes Konto
    # außerhalb der Stichprobe ist höchstens so groß wie das kleinste
    # Konto in der Stichprobe.
    min_sampled = snapshot.get("min_sampled_amount_raw")

    if not isinstance(min_sampled, int) or isinstance(min_sampled, bool):
        result["dev_share_pct"] = None
        result["unconfirmed_reasons"].append("sample_minimum_unknown")
        return result

    upper_bound = _pct(min_sampled, effective_supply)
    result["dev_share_pct"] = None
    result["dev_share_upper_bound_pct"] = upper_bound
    result["dev_share_basis"] = "creator_below_smallest_sampled_account"

    if upper_bound <= dev_limit:
        result["dev_status"] = "confirmed"
    else:
        result["unconfirmed_reasons"].append("creator_share_upper_bound_above_limit")

    return result
