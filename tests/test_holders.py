import json
from pathlib import Path

import pytest

from desk.config import load_config
from desk.holders import evaluate_holders
from desk.security import analyze_rugcheck_report
from factories import (
    POOL_ADDRESS,
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
# Zuordnung und Aggregation
# --------------------------------------------------

def test_pool_account_is_excluded_and_rest_confirmed():
    result = evaluate()

    assert result["pool_share_pct"] == pytest.approx(40)
    assert result["top10_share_pct"] == pytest.approx(10)
    assert result["top10_confirmed"] is True
    assert result["top10_status"] == "confirmed"


def test_accounts_of_same_owner_are_aggregated():
    snapshot = make_snapshot(
        holders=[("SAME", 8.0), ("SAME", 8.0), ("SAME", 8.0), ("SAME", 2.0)]
        + [(f"H{i}", 0.1) for i in range(9)],
    )

    result = evaluate(snapshot)

    assert result["top_holders"][0]["owner"] == "SAME"
    assert result["top_holders"][0]["share_pct"] == pytest.approx(26)
    assert "top10_holder_share_above_limit" in result["rejection_reasons"]


def test_burned_tokens_are_excluded_from_supply():
    snapshot = make_snapshot(burn_pct=50.0, pool_pct=10.0,
                             holders=[(f"H{i}", 1.5) for i in range(10)])

    result = evaluate(snapshot)

    # 15 % der Gesamtmenge = 30 % der nicht verbrannten Menge
    assert result["top10_share_pct"] == pytest.approx(30)
    assert result["top10_status"] == "rejected"


def test_system_program_owner_counts_as_burn():
    rows = [{
        "address": "SYSBURN",
        "owner": "11111111111111111111111111111111",
        "amount": 10**14,  # 10 % der Gesamtmenge
    }]
    result = evaluate(make_snapshot(extra_rows=rows))

    assert result["burned_amount_raw"] == 10**14


def test_burn_larger_than_supply_is_data_error():
    rows = [{
        "address": "SYSBURN",
        "owner": "11111111111111111111111111111111",
        "amount": 10**17,
    }]
    result = evaluate(make_snapshot(extra_rows=rows))

    assert result["top10_status"] == "unclear"
    assert "no_supply_after_burns" in result["unconfirmed_reasons"]


def test_pool_detected_by_dexpaprika_pool_address_without_labels():
    security = make_security(labels=make_labels(pool_vaults=(), pool_owners=()))

    result = evaluate(security=security)

    assert result["pool_share_pct"] == pytest.approx(40)


def test_unknown_large_account_is_never_excluded():
    security = make_security(labels=make_labels(pool_vaults=(), pool_owners=()))

    result = evaluate(security=security, pool_address="SOMETHING_ELSE")

    assert result["pool_amount_raw"] == 0
    assert "top10_holder_share_above_limit" in result["rejection_reasons"]


def test_exchange_and_locker_are_labelled_but_counted():
    labels = make_labels(exchanges=("HOLDER0",), lockers=("HOLDER1",))
    result = evaluate(security=make_security(labels=labels))

    categories = {h["owner"]: h["category"] for h in result["top_holders"]}

    assert categories["HOLDER0"] == "exchange"
    assert categories["HOLDER1"] == "locker"
    assert result["exchange_owners_counted"] == 1
    assert result["top10_share_pct"] == pytest.approx(10)


# --------------------------------------------------
# Bestätigung
# --------------------------------------------------

def test_incomplete_owner_mapping_is_not_confirmed():
    result = evaluate(make_snapshot(mapping_complete=False))

    assert result["top10_confirmed"] is False
    assert result["top10_status"] == "unclear"
    assert result["dev_status"] == "unclear"
    assert "owner_mapping_incomplete" in result["unconfirmed_reasons"]


def test_missing_labels_are_not_confirmed():
    security = make_security()
    security.pop("holder_labels")

    result = evaluate(security=security)

    assert result["top10_confirmed"] is False
    assert "pool_labels_missing" in result["unconfirmed_reasons"]


def test_full_sample_with_too_few_counted_owners_is_not_confirmed():
    # 20 Konten geliefert, aber nur 5 mitgezählte Eigentümer
    snapshot = make_snapshot(
        holders=[(f"H{i}", 1.0) for i in range(5)],
        returned=20,
    )

    result = evaluate(snapshot)

    assert result["top10_confirmed"] is False
    assert "fewer_than_10_counted_owners_in_sample" in result["unconfirmed_reasons"]


def test_exceeding_is_proven_even_when_unconfirmed():
    snapshot = make_snapshot(
        holders=[("WHALE", 30.0)],
        returned=20,
        mapping_complete=False,
    )

    result = evaluate(snapshot)

    assert result["top10_status"] == "rejected"
    assert "observed_top10_holder_share_above_limit" in result["rejection_reasons"]


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"accounts": "x", "supply_amount_raw": 10, "largest_accounts_returned": 1},
        {"accounts": [], "supply_amount_raw": 0, "largest_accounts_returned": 0},
        {"accounts": [], "supply_amount_raw": True, "largest_accounts_returned": 0},
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

def test_creator_in_sample_is_measured():
    snapshot = make_snapshot(holders=[(f"H{i}", 1.0) for i in range(10)] + [("CREATOR", 2.0)])

    result = evaluate(snapshot)

    assert result["dev_share_pct"] == pytest.approx(2)
    assert result["dev_status"] == "confirmed"


def test_creator_above_limit_rejects():
    snapshot = make_snapshot(holders=[(f"H{i}", 1.0) for i in range(10)] + [("CREATOR", 3.5)])

    result = evaluate(snapshot)

    assert "creator_share_above_limit" in result["rejection_reasons"]


def test_creator_absent_from_complete_holder_set_is_zero():
    result = evaluate()

    assert result["dev_share_pct"] == 0.0
    assert result["dev_status"] == "confirmed"


def test_creator_outside_full_sample_uses_upper_bound():
    holders = [(f"H{i}", 1.0) for i in range(19)]
    result = evaluate(make_snapshot(holders=holders, returned=20))

    assert result["dev_share_upper_bound_pct"] == pytest.approx(1.0)
    assert result["dev_status"] == "confirmed"


def test_creator_outside_sample_with_large_upper_bound_is_unclear():
    holders = [(f"H{i}", 4.0) for i in range(5)]
    result = evaluate(make_snapshot(holders=holders, pool_pct=0, returned=20))

    assert result["dev_status"] == "unclear"
    assert "creator_share_upper_bound_above_limit" in result["unconfirmed_reasons"]


def test_unknown_creator_is_unclear():
    result = evaluate(security=make_security(creator_address=None))

    assert result["dev_status"] == "unclear"
    assert "creator_unknown" in result["unconfirmed_reasons"]


# --------------------------------------------------
# Echte Daten vom 09.11.2026
# --------------------------------------------------

def real_case(symbol):
    case = FIXTURES[symbol]
    report = case["rugcheck_report"]
    security = analyze_rugcheck_report(report, {"lpLockedPct": 100}, lp_locked_min_pct=99.99)

    holders = report["topHolders"]
    accounts = [
        {"address": h["address"], "owner": h["owner"], "amount": int(h["amount"])}
        for h in holders
    ]

    snapshot = {
        "mint": report["mint"],
        "largest_accounts_returned": len(accounts),
        "sample_owner_mapping_complete": True,
        "accounts": accounts,
        "supply_amount_raw": report["token"]["supply"],
        "supply_decimals": report["token"]["decimals"],
        "min_sampled_amount_raw": min(a["amount"] for a in accounts),
    }

    return evaluate_holders(
        snapshot=snapshot,
        security=security,
        pool_address=case["dexpaprika_pool"]["id"],
        risk_filters=RISK,
    )


def test_real_pumpswap_pool_vault_is_excluded():
    # XTO: Pool-Konto ~1 %, Rest verteilt -> bestätigt, unter 25 %
    result = real_case("XTO")

    assert result["pool_share_pct"] > 0
    assert result["top10_confirmed"] is True
    assert result["top10_status"] == "confirmed"


def test_real_raydium_authority_is_recognised_as_pool():
    # TAOLF: 25,07 % liegen beim Raydium-Pool, nicht bei einem Holder
    result = real_case("TAOLF")

    assert result["pool_share_pct"] == pytest.approx(25.07, abs=0.1)
    assert all(h["owner"] != "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL" for h in result["top_holders"])


def test_real_single_whale_is_rejected():
    # Alice: ein unbekannter Holder mit 49,97 %
    result = real_case("Alice")

    assert result["top10_status"] == "rejected"


def test_real_dead_pool_old_logic_would_have_rejected():
    # HABIBI: 99,84 % liegen im Pool. Früher als Top-10 gezählt.
    result = real_case("HABIBI")

    assert result["pool_share_pct"] == pytest.approx(99.84, abs=0.01)
    assert result["top10_share_pct"] < 1


def test_real_bonding_curve_without_markets_is_not_confirmed():
    # KHELLY: noch auf der pump.fun-Bonding-Curve, RugCheck liefert keine markets
    result = real_case("KHELLY")

    assert result["top10_confirmed"] is False
    assert "pool_labels_incomplete" in result["unconfirmed_reasons"]
