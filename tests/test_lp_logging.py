"""Zusätzliche LP-Protokollierung (nur Protokoll, keine Bewertung).

Fall MYWAY (Scan #57): Der untersuchte Pool ist zu 100 % gesperrt, der
tokenweite lpLockedPct (99,9356 %) liegt wegen zweier kleiner, nicht
gesperrter Pools unter 99,99 % -> Ablehnung bleibt unverändert.
"""

import pytest

from desk import __main__ as app
from desk.security import (
    MAX_LOGGED_MARKETS,
    analyze_rugcheck_report,
    extract_market_lp,
)


MINT = "tx41eAXzN6jHH22LSgF7eSxpTNvJXwcbrcGsbDrpump"
POOL = "CV3MT2sZ4kfzc6GpBhw9esoz2dCfKkz5ZGS8uZP85p32"
ORCA = "Atfgj7sBvoUH5xuqNBuNhvEgCUxmBAqEbXv4VQ9nR2so"
METEORA = "4PWfgFPzSexok1aL2d7M9BbPh8B1RuCNPqNvHB54RVaJ"


def market(pubkey, market_type, pct, usd, base=1.0, quote=1.0):
    return {
        "pubkey": pubkey,
        "marketType": market_type,
        "mintA": MINT,
        "liquidityA": f"vault-{pubkey[:4]}",
        "lp": {
            "lpLockedPct": pct,
            "lpLockedUSD": usd,
            "baseUSD": base,
            "quoteUSD": quote,
        },
    }


def myway_report():
    # Werte wie am 2026-11-10 06:09 UTC von RugCheck gelesen
    return {
        "mint": MINT,
        "token": {"mintAuthority": None, "freezeAuthority": None, "supply": 1},
        "topHolders": [],
        "markets": [
            market(POOL, "pump_fun_amm", 100, 81262.26854830189, 41555.11, 39707.16),
            market(ORCA, "orca", 0, 0, 49.28, 0.015),
            market(METEORA, "meteora_damm_v2", 0, 0, 2.09, 0.94),
        ],
        "knownAccounts": {},
    }


def analyze(report, lp_pct=99.93564262340658):
    return analyze_rugcheck_report(
        report, {"lpLockedPct": lp_pct}, lp_locked_min_pct=99.99
    )


# --------------------------------------------------
# extract_market_lp
# --------------------------------------------------

def test_market_lp_is_read_per_pool():
    assert extract_market_lp(myway_report()) == [
        {"pool_address": POOL, "market_type": "pump_fun_amm",
         "lp_locked_pct": 100.0, "lp_locked_usd": 81262.26854830189},
        {"pool_address": ORCA, "market_type": "orca",
         "lp_locked_pct": 0.0, "lp_locked_usd": 0.0},
        {"pool_address": METEORA, "market_type": "meteora_damm_v2",
         "lp_locked_pct": 0.0, "lp_locked_usd": 0.0},
    ]


@pytest.mark.parametrize("markets", [None, "x", {"a": 1}])
def test_missing_markets_are_logged_as_none(markets):
    report = {"mint": MINT}
    if markets is not None:
        report["markets"] = markets

    assert extract_market_lp(report) is None


def test_invalid_values_become_none_and_are_not_guessed():
    report = {
        "markets": [
            "kein dict",
            {"pubkey": "", "marketType": 5, "lp": "kaputt"},
            {"pubkey": POOL, "lp": {"lpLockedPct": "abc", "lpLockedUSD": -1}},
            {"pubkey": ORCA, "lp": {"lpLockedPct": True, "lpLockedUSD": None}},
        ]
    }

    assert extract_market_lp(report) == [
        {"pool_address": None, "market_type": None,
         "lp_locked_pct": None, "lp_locked_usd": None},
        {"pool_address": POOL, "market_type": None,
         "lp_locked_pct": None, "lp_locked_usd": None},
        {"pool_address": ORCA, "market_type": None,
         "lp_locked_pct": None, "lp_locked_usd": None},
    ]


def test_extract_reads_all_markets_limit_applies_only_to_log():
    report = {"markets": [market(f"P{i:03d}", "x", 1, 1) for i in range(50)]}

    assert len(extract_market_lp(report)) == 50


# --------------------------------------------------
# Bewertung unverändert
# --------------------------------------------------

def test_lp_decision_still_uses_token_wide_value():
    # Pool 100 % gesperrt, tokenweit 99,9356 % -> weiterhin nicht bestanden
    result = analyze(myway_report())

    assert result["lp_locked_pct"] == pytest.approx(99.93564262340658)
    assert result["lp_burned_or_locked"] is False
    assert result["market_lp"][0]["lp_locked_pct"] == 100.0


@pytest.mark.parametrize("pct, expected", [(99.99, True), (100, True), (99.98, False), (None, None)])
def test_token_wide_threshold_unchanged(pct, expected):
    # Pool-Werte beeinflussen die Entscheidung nie
    report = myway_report()
    report["markets"][0]["lp"]["lpLockedPct"] = 0

    assert analyze(report, lp_pct=pct)["lp_burned_or_locked"] is expected


def test_pool_lp_values_do_not_change_other_fields():
    with_values = analyze(myway_report())
    report = myway_report()
    for entry in report["markets"]:
        del entry["lp"]["lpLockedPct"]
        del entry["lp"]["lpLockedUSD"]
    without = analyze(report)

    assert [m["lp_locked_pct"] for m in without["market_lp"]] == [None, None, None]
    for key in ("lp_locked_pct", "lp_burned_or_locked", "market_liquidity_usd_by_pool",
                "holder_labels", "mint_authority_revoked", "freeze_authority_revoked"):
        assert with_values[key] == without[key]


# --------------------------------------------------
# Protokoll (_check_details)
# --------------------------------------------------

def pool_record(pool_address=POOL, security=None):
    record = {"pool_address": pool_address}
    if security is not None:
        record["security"] = security
    return record


def test_check_details_log_lp_with_evaluated_pool():
    details = app._check_details(pool_record(security=analyze(myway_report())))["lp"]

    assert details["basis"] == "token_wide_lp_locked_pct"
    assert details["token_lp_locked_pct"] == pytest.approx(99.93564262340658)
    assert details["lp_burned_or_locked"] is False
    assert details["evaluated_pool_address"] == POOL
    assert details["evaluated_pool_found_in_markets"] is True
    assert details["evaluated_pool_lp_locked_pct"] == 100.0
    assert details["evaluated_pool_lp_locked_usd"] == pytest.approx(81262.26854830189)
    assert [m["pool_address"] for m in details["markets"]] == [POOL, ORCA, METEORA]
    assert [m["is_evaluated_pool"] for m in details["markets"]] == [True, False, False]
    assert details["markets"][1]["lp_locked_pct"] == 0.0
    assert details["evaluated_pool_market_position"] == 1
    assert details["markets_total_count"] == 3
    assert details["markets_truncated"] is False


def test_evaluated_pool_missing_in_markets_is_explicit():
    details = app._check_details(
        pool_record(pool_address="ANDERER", security=analyze(myway_report()))
    )["lp"]

    assert details["evaluated_pool_found_in_markets"] is False
    assert details["evaluated_pool_lp_locked_pct"] is None
    assert details["evaluated_pool_lp_locked_usd"] is None
    assert not any(m["is_evaluated_pool"] for m in details["markets"])


def test_missing_pool_address_never_matches():
    report = myway_report()
    report["markets"][0]["pubkey"] = None
    details = app._check_details(pool_record(pool_address=None, security=analyze(report)))["lp"]

    assert details["evaluated_pool_found_in_markets"] is False
    assert not any(m["is_evaluated_pool"] for m in details["markets"])


def test_no_security_check_means_no_lp_details():
    assert app._check_details(pool_record())["lp"] is None


def test_markets_missing_in_report_logged_as_none():
    report = myway_report()
    del report["markets"]
    details = app._check_details(pool_record(security=analyze(report)))["lp"]

    assert details["markets"] is None
    assert details["markets_total_count"] is None
    assert details["markets_truncated"] is None
    assert details["evaluated_pool_market_position"] is None
    assert details["evaluated_pool_found_in_markets"] is False
    assert details["token_lp_locked_pct"] == pytest.approx(99.93564262340658)


# --------------------------------------------------
# Viele Märkte: Begrenzung nur im Protokoll, untersuchter Pool immer dabei
# --------------------------------------------------

def many_markets_report(count, evaluated_index):
    """count Märkte; der untersuchte Pool POOL steht an Position
    evaluated_index (1-basiert), gesperrt mit 100 %."""
    markets = []
    for position in range(1, count + 1):
        if position == evaluated_index:
            markets.append(market(POOL, "pump_fun_amm", 100, 5000.0))
        else:
            markets.append(market(f"OTHER{position:03d}", "orca", 0, 0))
    return {**myway_report(), "markets": markets}


def lp_for(report):
    return app._check_details(pool_record(security=analyze(report)))["lp"]


@pytest.mark.parametrize("position", [21, 22, 35, 60])
def test_evaluated_pool_beyond_limit_is_found_and_logged(position):
    details = lp_for(many_markets_report(60, position))

    assert details["evaluated_pool_found_in_markets"] is True
    assert details["evaluated_pool_market_position"] == position
    assert details["evaluated_pool_lp_locked_pct"] == 100.0
    assert details["evaluated_pool_lp_locked_usd"] == 5000.0
    assert details["markets_total_count"] == 60
    assert details["markets_truncated"] is True
    # erste 20 Märkte plus der untersuchte Pool
    assert len(details["markets"]) == MAX_LOGGED_MARKETS + 1
    assert [m["pool_address"] for m in details["markets"][:MAX_LOGGED_MARKETS]] == [
        f"OTHER{i:03d}" for i in range(1, MAX_LOGGED_MARKETS + 1)
    ]
    assert details["markets"][-1]["pool_address"] == POOL
    assert details["markets"][-1]["is_evaluated_pool"] is True
    assert sum(m["is_evaluated_pool"] for m in details["markets"]) == 1


@pytest.mark.parametrize("position", [1, 20])
def test_evaluated_pool_within_limit_is_not_duplicated(position):
    details = lp_for(many_markets_report(60, position))

    assert details["evaluated_pool_market_position"] == position
    assert details["markets_truncated"] is True
    assert len(details["markets"]) == MAX_LOGGED_MARKETS
    assert sum(m["is_evaluated_pool"] for m in details["markets"]) == 1


def test_exactly_limit_markets_is_not_truncated():
    details = lp_for(many_markets_report(MAX_LOGGED_MARKETS, 5))

    assert details["markets_total_count"] == MAX_LOGGED_MARKETS
    assert details["markets_truncated"] is False
    assert len(details["markets"]) == MAX_LOGGED_MARKETS


def test_evaluated_pool_absent_from_many_markets():
    details = lp_for(many_markets_report(60, 0))

    assert details["evaluated_pool_found_in_markets"] is False
    assert details["evaluated_pool_market_position"] is None
    assert details["markets_truncated"] is True
    assert len(details["markets"]) == MAX_LOGGED_MARKETS


def test_many_markets_do_not_change_lp_decision():
    # Untersuchter Pool 100 % an Position 40; entscheidend bleibt tokenweit
    report = many_markets_report(60, 40)

    assert analyze(report, lp_pct=99.93)["lp_burned_or_locked"] is False
    assert analyze(report, lp_pct=99.99)["lp_burned_or_locked"] is True
