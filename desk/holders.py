"""Holder-Verteilung: Zuordnung, Aggregation und belegbare Grenzen.

Datengrundlage: die bis zu 20 größten Token-Konten von der Blockchain
(desk/solana_rpc.py), ergänzt um Kennzeichnungen aus RugCheck
(desk/security.py). Das ist eine STICHPROBE, keine vollständige
Eigentümerverteilung, und wird nie als solche ausgegeben.

Zuordnung:
- Token-Konten werden ihrem Eigentümer (owner) zugeordnet und je
  Eigentümer zusammengezählt.
- Herausgerechnet werden nur
  * Pool-Konten: Konto ist ein Pool-Vault laut RugCheck "markets",
    oder der Eigentümer ist eine Pool-Adresse (RugCheck "markets",
    DexPaprika-Pool) bzw. von RugCheck als AMM gekennzeichnet;
  * Burn-Konten: Eigentümer ist eine Adresse, von der Token
    nachweislich nicht mehr bewegt werden können.
- Börsen- und Locker-Konten werden nur bei ausdrücklicher
  Kennzeichnung erkannt, gesondert ausgewiesen und weiterhin
  mitgezählt. Unbekannte Konten werden immer mitgezählt.

Grenzen statt Annahmen:
- Nicht erfasste Menge U = Gesamtmenge - Summe aller erfassten Konten
  (exakt, weil die Gesamtmenge von der Blockchain kommt). Konten mit
  ungültiger Zuordnung zählen zu U.
- Anteile beziehen sich auf N = Gesamtmenge - nachweislich verbrannt.
- Top-10-Untergrenze = größte 10 mitgezählte Eigentümer der
  Stichprobe / N. Die echten Bestände sind mindestens so groß.
- Top-10-Obergrenze = (größte 10 der Stichprobe + U) / N. Schlimmster
  Fall: alle nicht erfassten Token gehören den Top-10-Eigentümern
  (z. B. über weitere Konten desselben Eigentümers).
- Creator analog: Untergrenze = erfasster Bestand, Obergrenze =
  erfasster Bestand + U.

Entscheidung:
- Untergrenze über dem Limit -> abgelehnt (bewiesen).
- Obergrenze höchstens am Limit, vollständige Zuordnung und
  vollständige RugCheck-Kennzeichnung -> bestätigt (bewiesen, ohne
  Annahme über nicht erfasste Konten).
- Sonst -> unklar; die Prüfung bleibt unvollständig.

Verbleibende Vertrauensannahme: Konten, die als Pool oder Burn erkannt
werden, gehören tatsächlich keinem Holder. Verschiedene Wallets
derselben Person sind nicht erkennbar.
"""


# Eigentümer, deren Token nicht mehr bewegt werden können:
# - Solana Incinerator
# - System Program (kann nicht für den Token-Eigentümer signieren)
BURN_OWNERS = {
    "1nc1nerator11111111111111111111111111111111": "incinerator",
    "11111111111111111111111111111111": "system_program",
}

TOP_N = 10

POOL = "pool"
BURN = "burn"
CREATOR = "creator"
EXCHANGE = "exchange"
LOCKER = "locker"
UNKNOWN = "unknown"

CONFIRMED = "confirmed"
REJECTED = "rejected"
UNCLEAR = "unclear"


def _pct(amount, base):
    return round(amount / base * 100, 6) if base > 0 else None


def _valid_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


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


def _valid_rows(accounts):
    """Nur vollständige Zeilen; alles andere zählt als nicht erfasst."""
    rows = []

    for row in accounts:
        if (
            isinstance(row, dict)
            and isinstance(row.get("address"), str)
            and isinstance(row.get("owner"), str)
            and _valid_int(row.get("amount"))
            and row["amount"] >= 0
        ):
            rows.append(row)

    return rows


def evaluate_holders(*, snapshot, security, pool_address, risk_filters):
    top10_limit = risk_filters["reject_top10_share_above_pct"]
    dev_limit = risk_filters["reject_dev_share_above_pct"]

    labels = _labels(security)
    creator = (
        security.get("creator_address")
        if isinstance(security, dict)
        else None
    )

    accounts = snapshot.get("accounts") if isinstance(snapshot, dict) else None
    supply = snapshot.get("supply_amount_raw") if isinstance(snapshot, dict) else None

    result = {
        "data_scope": "largest_token_accounts_sample",
        "is_complete_distribution": False,
        "top10_status": UNCLEAR,
        "dev_status": UNCLEAR,
        "rejection_reasons": [],
        "unconfirmed_reasons": [],
    }

    if (
        not isinstance(accounts, list)
        or not _valid_int(supply)
        or supply <= 0
    ):
        result["unconfirmed_reasons"].append("holder_snapshot_incomplete")
        return result

    rows = _valid_rows(accounts)

    if len(rows) != len(accounts):
        result["unconfirmed_reasons"].append("invalid_account_rows")

    mapping_complete = (
        snapshot.get("sample_owner_mapping_complete") is True
        and len(rows) == len(accounts)
    )

    if not mapping_complete:
        result["unconfirmed_reasons"].append("owner_mapping_incomplete")

    if labels is None:
        result["unconfirmed_reasons"].append("pool_labels_missing")
        labels = {"labels_complete": False}
    elif labels.get("labels_complete") is not True:
        result["unconfirmed_reasons"].append("pool_labels_incomplete")

    labels_complete = labels.get("labels_complete") is True

    labels = {
        "pool_vault_accounts": set(labels.get("pool_vault_accounts") or []),
        "pool_owner_addresses": set(labels.get("pool_owner_addresses") or []),
        "exchange_addresses": set(labels.get("exchange_addresses") or []),
        "locker_addresses": set(labels.get("locker_addresses") or []),
    }

    pool_amount = 0
    burn_amount = 0
    owners = {}

    for row in rows:
        category = classify_account(row, labels, pool_address, creator)

        if category == POOL:
            pool_amount += row["amount"]
            continue

        if category == BURN:
            burn_amount += row["amount"]
            continue

        entry = owners.setdefault(
            row["owner"],
            {"owner": row["owner"], "amount": 0, "accounts": 0, "category": category},
        )
        entry["amount"] += row["amount"]
        entry["accounts"] += 1

    sampled_total = sum(row["amount"] for row in rows)

    if sampled_total > supply:
        result["unconfirmed_reasons"].append("sample_exceeds_supply")
        return result

    unseen = supply - sampled_total
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

    top10_lower = _pct(top_amount, effective_supply)
    top10_upper = _pct(top_amount + unseen, effective_supply)

    result.update(
        {
            "is_complete_distribution": unseen == 0 and mapping_complete,
            "total_supply_raw": supply,
            "sampled_amount_raw": sampled_total,
            "unseen_amount_raw": unseen,
            "unseen_share_pct": _pct(unseen, effective_supply),
            "burned_amount_raw": burn_amount,
            "pool_amount_raw": pool_amount,
            "pool_share_pct": _pct(pool_amount, effective_supply),
            "counted_owners_in_sample": len(ranked),
            "accounts_in_sample": len(rows),
            "top10_share_lower_bound_pct": top10_lower,
            "top10_share_upper_bound_pct": top10_upper,
            "top_holders_in_sample": [
                {
                    "owner": entry["owner"],
                    "category": entry["category"],
                    "accounts_in_sample": entry["accounts"],
                    "share_lower_bound_pct": _pct(entry["amount"], effective_supply),
                }
                for entry in top
            ],
            "exchange_owners_counted": sum(
                1 for entry in ranked if entry["category"] == EXCHANGE
            ),
        }
    )

    can_confirm = mapping_complete and labels_complete

    # Top-10
    if top10_lower > top10_limit:
        result["top10_status"] = REJECTED
        result["rejection_reasons"].append("top10_holder_share_above_limit")
    elif top10_upper <= top10_limit and can_confirm:
        result["top10_status"] = CONFIRMED
    elif top10_upper > top10_limit:
        result["unconfirmed_reasons"].append("top10_upper_bound_above_limit")

    # Entwickler-Anteil
    if not creator:
        result["unconfirmed_reasons"].append("creator_unknown")
        return result

    creator_entry = owners.get(creator)
    creator_amount = creator_entry["amount"] if creator_entry else 0

    dev_lower = _pct(creator_amount, effective_supply)
    dev_upper = _pct(creator_amount + unseen, effective_supply)

    result["dev_share_lower_bound_pct"] = dev_lower
    result["dev_share_upper_bound_pct"] = dev_upper
    result["creator_found_in_sample"] = creator_entry is not None

    if dev_lower > dev_limit:
        result["dev_status"] = REJECTED
        result["rejection_reasons"].append("creator_share_above_limit")
    elif dev_upper <= dev_limit and can_confirm:
        result["dev_status"] = CONFIRMED
    elif dev_upper > dev_limit:
        result["unconfirmed_reasons"].append("dev_upper_bound_above_limit")

    return result
