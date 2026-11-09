"""Ganze Prüfkette: Vorprüfung -> RugCheck -> Liquidität -> Holder ->
Marktkapitalisierung. Externe Abfragen sind durch Testdaten ersetzt."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from desk.config import load_config
from desk.security import SecurityDataError
from desk.security_scan import scan_pool_security
from desk.solana_rpc import SolanaRpcError
from factories import POOL_ADDRESS, make_labels, make_security, make_snapshot


CONFIG = load_config()
SOL = "So11111111111111111111111111111111111111112"
MINT = "TOKEN123"


def make_raw_pool(**overrides):
    pool = {
        "id": POOL_ADDRESS,
        "dex_name": "PumpSwap",
        "created_at": (
            datetime.now(timezone.utc) - timedelta(hours=48)
        ).isoformat(),
        "liquidity_usd": 50000,
        "volume_usd_24h": 12000,
        "transactions_24h": 80,
        "tokens": [
            {"id": SOL, "symbol": "SOL"},
            {"id": MINT, "name": "Example", "symbol": "EX", "fdv": 510000},
        ],
    }
    pool.update(overrides)
    return pool


def good_market_data(price=0.0005, market_cap=None):
    return {
        MINT.lower(): {
            "market_cap_usd": market_cap,
            "price_usd": price,
            "fdv_usd": 500000.0,
        }
    }


def good_security(**overrides):
    values = {
        "mint": MINT,
        "freeze_revoked": True,
        "price": 0.0005,
    }
    values.update(overrides)
    return make_security(**values)


def run(
    pool=None,
    market_data=None,
    security=None,
    snapshot=None,
    security_error=None,
    holder_error=None,
    config=CONFIG,
):
    security_kwargs = (
        {"side_effect": security_error}
        if security_error
        else {"return_value": security if security is not None else good_security()}
    )
    holder_kwargs = (
        {"side_effect": holder_error}
        if holder_error
        else {"return_value": snapshot if snapshot is not None else make_snapshot(mint=MINT)}
    )

    with patch(
        "desk.market_data.fetch_token_market_data",
        return_value=market_data if market_data is not None else good_market_data(),
    ), patch(
        "desk.security_scan.check_token_security",
        **security_kwargs,
    ), patch(
        "desk.security_scan.fetch_holder_snapshot",
        **holder_kwargs,
    ):
        result = scan_pool_security([pool or make_raw_pool()], config)

    return result["pools"][0]


# --------------------------------------------------
# Vollständig bestätigter Fall
# --------------------------------------------------

def test_fully_confirmed_token_is_ready():
    # Gemeldete Marktkapitalisierung, zwei übereinstimmende Preise,
    # vollständig erfasste Holder-Verteilung.
    item = run(market_data=good_market_data(market_cap=450000.0))

    assert item["rejection_reasons"] == []
    assert item["missing_checks"] == []
    assert item["status"] == "ready_for_full_risk_screen"

    assert item["liquidity_check"]["status"] == "confirmed"
    assert item["holder_evaluation"]["top10_status"] == "confirmed"
    assert item["holder_evaluation"]["dev_status"] == "confirmed"

    market_cap = item["market_cap_evaluation"]
    assert market_cap["status"] == "confirmed"
    assert market_cap["basis"] == "reported_circulating_supply"
    assert market_cap["market_cap_usd"] == 450000.0
    assert round(market_cap["market_cap_upper_bound_usd"]) == 500000


def test_upper_bound_alone_never_makes_token_ready():
    # Alles bestätigt, aber keine gemeldete Umlaufmenge
    item = run()

    market_cap = item["market_cap_evaluation"]

    assert market_cap["status"] == "unclear"
    assert market_cap["reason"] == "circulating_supply_not_confirmed"
    assert item["market_cap_usd"] is None
    assert round(item["market_cap_upper_bound_usd"]) == 500000
    assert item["missing_checks"] == ["market_cap_usd"]
    assert item["status"] == "incomplete"


def test_price_conflict_is_not_bypassed_by_reported_market_cap():
    item = run(
        market_data=good_market_data(market_cap=450000.0),
        security=good_security(price=0.0009),
    )

    assert item["market_cap_evaluation"]["reason"] == "price_sources_disagree"
    assert "market_cap_usd" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_sample_with_unseen_supply_keeps_holders_open():
    item = run(
        market_data=good_market_data(market_cap=450000.0),
        snapshot=make_snapshot(mint=MINT, pool_pct=40.0, returned=20),
    )

    assert "top10_share_pct" in item["missing_checks"]
    assert "dev_share_pct" in item["missing_checks"]
    assert item["holder_evaluation"]["is_complete_distribution"] is False
    assert item["status"] == "incomplete"


# --------------------------------------------------
# Ausschlüsse
# --------------------------------------------------

def test_stablecoin_pool_is_excluded_without_api_calls():
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    pool = make_raw_pool(
        tokens=[
            {"id": SOL, "symbol": "SOL"},
            {"id": "EURCmint", "symbol": "EURC", "name": "Fake Euro"},
        ]
    )
    # EURC-Symbol mit fremder Adresse: unklar, nicht ausgeschlossen
    item = run(pool=pool)
    assert "token_classification" in item["missing_checks"]
    assert item["status"] == "incomplete"

    pool = make_raw_pool(
        tokens=[
            {"id": usdc, "symbol": "USDC"},
            {"id": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn", "symbol": "JitoSOL"},
        ]
    )

    with patch(
        "desk.security_scan.check_token_security",
    ) as mocked_security:
        with patch("desk.market_data.fetch_token_market_data", return_value={}):
            result = scan_pool_security([pool], CONFIG)

    item = result["pools"][0]
    assert "excluded_staking_token" in item["rejection_reasons"]
    mocked_security.assert_not_called()


# --------------------------------------------------
# Liquidität
# --------------------------------------------------

def test_stale_dexpaprika_liquidity_is_unclear():
    # Echter Fall (HABIBI, 09.11.2026): DexPaprika 70.676 $, RugCheck 1.921 $
    item = run(
        pool=make_raw_pool(liquidity_usd=70676),
        security=good_security(market_liquidity={POOL_ADDRESS: 1921.0}),
    )

    assert item["liquidity_check"]["status"] == "unclear"
    assert item["liquidity_check"]["reason"] == "liquidity_sources_disagree"
    assert "liquidity_cross_check" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_pool_missing_in_rugcheck_markets_is_unclear():
    item = run(security=good_security(market_liquidity={"OTHERPOOL": 50000.0}))

    assert item["liquidity_check"]["reason"] == "rugcheck_market_liquidity_missing"
    assert "liquidity_cross_check" in item["missing_checks"]


def test_lower_cross_checked_liquidity_below_minimum_rejects():
    item = run(
        pool=make_raw_pool(liquidity_usd=42000),
        security=good_security(market_liquidity={POOL_ADDRESS: 39000.0}),
    )

    assert "liquidity_below_minimum_cross_checked" in item["rejection_reasons"]
    assert item["holder_check_status"] == "skipped_security_rejection"


# --------------------------------------------------
# Holder
# --------------------------------------------------

def test_large_unknown_holder_is_counted_and_rejects():
    # Echter Fall (Alice, 09.11.2026): ein unbekannter Holder mit 49,97 %
    snapshot = make_snapshot(
        mint=MINT,
        holders=[("WHALE", 49.97)] + [(f"H{i}", 0.2) for i in range(10)],
        pool_pct=47.97,
    )

    item = run(snapshot=snapshot)

    assert "top10_holder_share_above_limit" in item["rejection_reasons"]
    assert item["status"] == "rejected"


def test_unlabelled_pool_account_is_not_excluded():
    # Pool-Konto ohne Kennzeichnung: wird wie ein Holder gezählt
    snapshot = make_snapshot(mint=MINT)
    labels = make_labels(pool_vaults=(), pool_owners=())

    item = run(
        snapshot=snapshot,
        security=good_security(labels=labels),
        pool=make_raw_pool(id="DIFFERENTPOOL"),
    )

    assert "top10_holder_share_above_limit" in item["rejection_reasons"]


def test_incomplete_labels_keep_top10_unconfirmed():
    item = run(security=good_security(labels=make_labels(complete=False)))

    assert item["holder_evaluation"]["top10_status"] == "unclear"
    assert "top10_share_pct" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_incomplete_owner_mapping_keeps_holders_unconfirmed():
    item = run(snapshot=make_snapshot(mint=MINT, mapping_complete=False))

    assert "top10_share_pct" in item["missing_checks"]
    assert "dev_share_pct" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_holder_rpc_error_leaves_market_cap_and_holders_open():
    item = run(holder_error=SolanaRpcError("RPC unavailable"))

    assert item["holder_check_status"] == "error"
    assert "top10_share_pct" in item["missing_checks"]
    assert "market_cap_usd" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_security_error_leaves_everything_open():
    item = run(security_error=SecurityDataError("down"))

    assert item["security_check_status"] == "error"
    assert "liquidity_cross_check" in item["missing_checks"]
    assert "mint_authority_revoked" in item["missing_checks"]
    assert item["status"] == "incomplete"


# --------------------------------------------------
# Marktkapitalisierung und Preis
# --------------------------------------------------

def test_conflicting_prices_keep_market_cap_open():
    item = run(security=good_security(price=0.0009))

    assert item["market_cap_evaluation"]["reason"] == "price_sources_disagree"
    assert "market_cap_usd" in item["missing_checks"]


def test_missing_prices_keep_market_cap_open():
    item = run(
        market_data=good_market_data(price=None),
        security=good_security(price=None),
    )

    assert item["market_cap_evaluation"]["reason"] == "no_price_available"
    assert item["status"] == "incomplete"


def test_upper_bound_below_minimum_rejects():
    item = run(
        market_data=good_market_data(price=0.0001),
        security=good_security(price=0.0001),
    )

    assert "market_cap_below_minimum" in item["rejection_reasons"]


def test_reported_market_cap_out_of_range_rejects_early():
    item = run(market_data=good_market_data(market_cap=25_000_000.0))

    assert "market_cap_out_of_range" in item["rejection_reasons"]
    assert item["security_check_status"] == "skipped_pool_rejected"


def test_invalid_numbers_from_apis_are_treated_as_missing():
    item = run(
        pool=make_raw_pool(liquidity_usd="abc"),
        market_data={MINT.lower(): {"market_cap_usd": None, "price_usd": None, "fdv_usd": None}},
    )

    assert "liquidity_missing_or_invalid" in item["rejection_reasons"]
