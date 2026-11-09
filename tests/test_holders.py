import json
from pathlib import Path

import pytest

from desk.config import load_config
from desk.holders import evaluate_holders
from desk.security import analyze_rugcheck_report
from factories import (
    POOL_ADDRESS,
    SUPPLY_RAW,
    amount,
    make_labels,
    make_security,
    make_snapshot,
)


RISK = load_config()["risk_filters"]
FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "real_samples_2026-11-09.json")
    .read_text(encoding="utf-8")
)


def evaluate(snapshot=None, security=None, pool_address=POOL_ADDRESS):
    return evaluate_holders(
        snapshot=snapshot if snapshot is not None else make_snapshot(),
        security=security if security is not None else make_security(),
        pool_address=pool_address,
        risk_filters=RISK,
    )


# --------------------------------------------------
# Vollständig erfasste Verteilung
# --------------------------------------------------

def test_complete_distribution_is_confirmed():
    # Pool + 10 Holder erfassen 100 % der Menge
    result = evaluate()

    assert result["unseen_amount_raw"] == 0
    assert result["is_complete_distribution"] is True
    assert result["top10_share_lower_bound_pct"] == pytest.approx(10)
    assert result["top10_share_upper_bound_pct"] == pytest.approx(10)
    assert result["top10_status"] == "confirmed"


def test_upper_bound_exactly_at_limit_is_confirmed():
    snapshot = make_snapshot(holders=[(f"H{i}", 2.5) for i in range(10)])

    result = evaluate(snapshot)

    assert result["top10_share_upper_bound_pct"] == pytest.approx(25)
    assert result["top10_status"] == "confirmed"


def test_lower_bound_just_above_limit_rejects():
    snapshot = make_snapshot(holders=[(f"H{i}", 2.51) for i in range(10)])

    result = evaluate(snapshot)

    assert result["top10_status"] == "rejected"
    assert "top10_holder_share_above_limit" in result["rejection_reasons"]


# --------------------------------------------------
# Stichprobe mit nicht erfasster Menge
# --------------------------------------------------

def test_unseen_supply_prevents_confirmation():
    # Stichprobe erfasst 40 % Pool + 10 % Holder, 50 % nicht erfasst.
    # Im schlimmsten Fall gehören die 50 % den Top-10-Eigentümern.
    snapshot = make_snapshot(pool_pct=40.0, returned=20)

    result = evaluate(snapshot)

    assert result["is_complete_distribution"] is False
    assert result["unseen_share_pct"] == pytest.approx(50)
    assert result["top10_share_lower_bound_pct"] == pytest.approx(10)
    assert result["top10_share_upper_bound_pct"] == pytest.approx(60)
    assert result["top10_status"] == "unclear"
    assert "top10_upper_bound_above_limit" in result["unconfirmed_reasons"]


def test_small_unseen_supply_still_allows_proof():
    # Pool 88 %, 10 x 1 %, 2 % nicht erfasst -> Obergrenze 12 %
    snapshot = make_snapshot(pool_pct=88.0, returned=20)

    result = evaluate(snapshot)

    assert result["top10_share_upper_bound_pct"] == pytest.approx(12)
    assert result["top10_status"] == "confirmed"


def test_twenty_accounts_alone_are_not_treated_as_complete():
    # Genau 20 Konten geliefert, Summe < Gesamtmenge: keine Vollständigkeit
    holders = [(f"H{i}", 0.5) for i in range(19)]
    snapshot = make_snapshot(holders=holders, pool_pct=30.0, returned=20)

    result = evaluate(snapshot)

    assert result["accounts_in_sample"] == 20
    assert result["is_complete_distribution"] is False
    assert result["data_scope"] == "largest_token_accounts_sample"
    assert result["top10_status"] == "unclear"


def test_hidden_accounts_of_same_owner_are_covered_by_upper_bound():
    # Ein Eigentümer mit 1 % in der Stichprobe könnte über viele kleine
    # Konten außerhalb der Stichprobe weitere 30 % halten.
    snapshot = make_snapshot(pool_pct=59.0, returned=20)

    result = evaluate(snapshot)

    assert result["unseen_share_pct"] == pytest.approx(31)
    assert result["top10_share_upper_bound_pct"] > 25
    assert result["top10_status"] != "confirmed"


# --------------------------------------------------
# Aggregation je Eigentümer
# --------------------------------------------------

def test_accounts_of_same_owner_are_aggregated():
    snapshot = make_snapshot(
        holders=[("SAME", 8.0), ("SAME", 8.0), ("SAME", 8.0), ("SAME", 2.0)]
        + [(f"H{i}", 0.1) for i in range(9)],
    )

    result = evaluate(snapshot)
    top = result["top_holders_in_sample"][0]

    assert top["owner"] == "SAME"
    assert top["accounts_in_sample"] == 4
    assert top["share_lower_bound_pct"] == pytest.approx(26)
    assert "top10_holder_share_above_limit" in result["rejection_reasons"]


def test_aggregation_can_reveal_concentration_hidden_in_many_accounts():
    # Kein einzelnes Konto über 3 %, ein Eigentümer zusammen 30 %
    holders = [("SPLIT", 2.5) for _ in range(12)] + [(f"H{i}", 0.1) for i in range(5)]

    result = evaluate(make_snapshot(holders=holders))

    assert result["top_holders_in_sample"][0]["share_lower_bound_pct"] == pytest.approx(30)
    assert result["top10_status"] == "rejected"


# --------------------------------------------------
# Pool, Burn, Börse, unbekannt
# --------------------------------------------------

def test_burned_tokens_are_excluded_from_supply():
    snapshot = make_snapshot(burn_pct=50.0, holders=[(f"H{i}", 1.5) for i in range(10)])

    result = evaluate(snapshot)

    # 15 % der Gesamtmenge = 30 % der nicht verbrannten Menge
    assert result["top10_share_lower_bound_pct"] == pytest.approx(30)
    assert result["top10_status"] == "rejected"


def test_system_program_owner_counts_as_burn():
    rows = [{
        "address": "SYSBURN",
        "owner": "11111111111111111111111111111111",
        "amount": amount(10),
    }]
    result = evaluate(make_snapshot(extra_rows=rows))

    assert result["burned_amount_raw"] == amount(10)


def test_pool_detected_by_dexpaprika_pool_address_without_labels():
    security = make_security(labels=make_labels(pool_vaults=(), pool_owners=()))

    result = evaluate(security=security)

    assert result["pool_share_pct"] == pytest.approx(90)


def test_unknown_large_account_is_never_excluded():
    security = make_security(labels=make_labels(pool_vaults=(), pool_owners=()))

    result = evaluate(security=security, pool_address="SOMETHING_ELSE")

    assert result["pool_amount_raw"] == 0
    assert "top10_holder_share_above_limit" in result["rejection_reasons"]


def test_exchange_and_locker_are_labelled_but_counted():
    labels = make_labels(exchanges=("HOLDER0",), lockers=("HOLDER1",))
    result = evaluate(security=make_security(labels=labels))

    categories = {h["owner"]: h["category"] for h in result["top_holders_in_sample"]}

    assert categories["HOLDER0"] == "exchange"
    assert categories["HOLDER1"] == "locker"
    assert result["exchange_owners_counted"] == 1
    assert result["top10_share_lower_bound_pct"] == pytest.approx(10)


# --------------------------------------------------
# Unvollständige oder fehlerhafte Daten
# --------------------------------------------------

def test_incomplete_owner_mapping_is_not_confirmed():
    result = evaluate(make_snapshot(mapping_complete=False))

    assert result["top10_status"] == "unclear"
    assert result["dev_status"] == "unclear"
    assert "owner_mapping_incomplete" in result["unconfirmed_reasons"]


def test_missing_labels_are_not_confirmed():
    security = make_security()
    security.pop("holder_labels")

    result = evaluate(security=security)

    assert result["top10_status"] == "unclear"
    assert "pool_labels_missing" in result["unconfirmed_reasons"]


def test_incomplete_labels_are_not_confirmed():
    result = evaluate(security=make_security(labels=make_labels(complete=False)))

    assert result["top10_status"] == "unclear"
    assert "pool_labels_incomplete" in result["unconfirmed_reasons"]


def test_invalid_rows_count_as_unseen_and_block_confirmation():
    snapshot = make_snapshot()
    snapshot["accounts"].append({"address": "BAD", "owner": None, "amount": 5})

    result = evaluate(snapshot)

    assert "invalid_account_rows" in result["unconfirmed_reasons"]
    assert result["top10_status"] == "unclear"


def test_exceeding_is_proven_even_when_unconfirmed():
    snapshot = make_snapshot(holders=[("WHALE", 30.0)], pool_pct=10.0, mapping_complete=False)

    result = evaluate(snapshot)

    assert result["top10_status"] == "rejected"


def test_sample_larger_than_supply_is_data_error():
    rows = [{"address": "X", "owner": "Y", "amount": SUPPLY_RAW}]

    result = evaluate(make_snapshot(extra_rows=rows, pool_pct=10.0))

    assert result["top10_status"] == "unclear"
    assert "sample_exceeds_supply" in result["unconfirmed_reasons"]


def test_burn_equal_to_supply_is_data_error():
    result = evaluate(make_snapshot(holders=[], burn_pct=100.0, pool_pct=0))

    assert result["top10_status"] == "unclear"
    assert "no_supply_after_burns" in result["unconfirmed_reasons"]


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"accounts": "x", "supply_amount_raw": 10},
        {"accounts": [], "supply_amount_raw": 0},
        {"accounts": [], "supply_amount_raw": True},
        {"accounts": [], "supply_amount_raw": "1000"},
    ],
)
def test_broken_snapshot_is_unclear(snapshot):
    result = evaluate(snapshot)

    assert result["top10_status"] == "unclear"
    assert result["dev_status"] == "unclear"
    assert result["rejection_reasons"] == []


# --------------------------------------------------
# Entwickler-Anteil
# --------------------------------------------------

def test_creator_in_complete_distribution_is_confirmed():
    snapshot = make_snapshot(holders=[(f"H{i}", 1.0) for i in range(10)] + [("CREATOR", 2.0)])

    result = evaluate(snapshot)

    assert result["dev_share_lower_bound_pct"] == pytest.approx(2)
    assert result["dev_share_upper_bound_pct"] == pytest.approx(2)
    assert result["dev_status"] == "confirmed"


def test_creator_above_limit_rejects():
    snapshot = make_snapshot(holders=[(f"H{i}", 1.0) for i in range(10)] + [("CREATOR", 3.5)])

    result = evaluate(snapshot)

    assert "creator_share_above_limit" in result["rejection_reasons"]


def test_creator_absent_from_complete_distribution_is_zero():
    result = evaluate()

    assert result["creator_found_in_sample"] is False
    assert result["dev_share_upper_bound_pct"] == 0
    assert result["dev_status"] == "confirmed"


def test_creator_absent_from_sample_with_unseen_supply_is_unclear():
    # Nicht erfasste 5 % könnten (über mehrere Konten) dem Creator gehören
    result = evaluate(make_snapshot(pool_pct=85.0, returned=20))

    assert result["dev_share_upper_bound_pct"] == pytest.approx(5)
    assert result["dev_status"] == "unclear"
    assert "dev_upper_bound_above_limit" in result["unconfirmed_reasons"]


def test_creator_with_small_unseen_supply_is_confirmed():
    result = evaluate(make_snapshot(pool_pct=88.0, returned=20))

    assert result["dev_share_upper_bound_pct"] == pytest.approx(2)
    assert result["dev_status"] == "confirmed"


def test_unknown_creator_is_unclear():
    result = evaluate(security=make_security(creator_address=None))

    assert result["dev_status"] == "unclear"
    assert "creator_unknown" in result["unconfirmed_reasons"]


# --------------------------------------------------
# Echte Daten vom 09.11.2026 (RugCheck-Stichprobe der größten Konten)
# --------------------------------------------------

def real_case(symbol):
    case = FIXTURES[symbol]
    report = case["rugcheck_report"]
    security = analyze_rugcheck_report(report, {"lpLockedPct": 100}, lp_locked_min_pct=99.99)

    accounts = [
        {"address": h["address"], "owner": h["owner"], "amount": int(h["amount"])}
        for h in report["topHolders"]
    ]

    snapshot = {
        "mint": report["mint"],
        "largest_accounts_returned": len(accounts),
        "sample_owner_mapping_complete": True,
        "accounts": accounts,
        "supply_amount_raw": report["token"]["supply"],
        "supply_decimals": report["token"]["decimals"],
    }

    return evaluate_holders(
        snapshot=snapshot,
        security=security,
        pool_address=case["dexpaprika_pool"]["id"],
        risk_filters=RISK,
    )


def test_real_xto_distribution_is_not_confirmable_from_sample():
    # XTO: Die 20 größten Konten erfassen nur einen kleinen Teil der
    # Menge. Ohne Annahmen ist keine Bestätigung möglich.
    result = real_case("XTO")

    assert result["unseen_share_pct"] > 50
    assert result["top10_share_lower_bound_pct"] < 25
    assert result["top10_status"] == "unclear"


def test_real_raydium_authority_is_recognised_as_pool():
    # TAOLF: 25,07 % liegen beim Raydium-Pool, nicht bei einem Holder
    result = real_case("TAOLF")

    assert result["pool_share_pct"] == pytest.approx(25.07, abs=0.1)
    assert all(
        h["owner"] != "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL"
        for h in result["top_holders_in_sample"]
    )


def test_real_single_whale_is_rejected():
    # Alice: ein unbekannter Holder mit 49,97 %
    result = real_case("Alice")

    assert result["top10_status"] == "rejected"


def test_real_dead_pool_is_excluded_from_holders():
    # HABIBI: 99,84 % liegen im Pool. Früher als Top-10 gezählt.
    result = real_case("HABIBI")

    assert result["pool_share_pct"] == pytest.approx(99.84, abs=0.01)
    assert result["top10_share_lower_bound_pct"] < 1


def test_real_bonding_curve_without_markets_is_not_confirmed():
    # KHELLY: noch auf der pump.fun-Bonding-Curve, keine markets
    result = real_case("KHELLY")

    assert result["top10_status"] == "unclear"
    assert "pool_labels_incomplete" in result["unconfirmed_reasons"]
