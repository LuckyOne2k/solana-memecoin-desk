"""Einbindung der On-Chain-Spanne in die Prüfkette (PR 2).

Kein Netzwerk. Mit ausgeschaltetem Schalter (eigene Config-Kopie) wird
nie abgefragt; die conftest-Sperre lässt jeden ungewollten Aufruf
scheitern.
"""

import base64
import copy
import struct
from unittest.mock import patch

import pytest

from desk import solana_rpc
from desk.circulating_bounds import NOTE
from desk.config import load_config
from desk.security_scan import scan_pool_security
from desk.solana_rpc import (
    SYSTEM_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    OnchainBoundsError,
    _b58decode,
    _b58encode,
    is_on_curve,
)
from factories import make_mint_info


CONFIG = load_config()
MINT = "TOKEN123"
SUPPLY = 1_000_000_000_000_000
DECIMALS = 6
POOL = "POOL1"


def disabled_config():
    """Unabhängige Kopie der Projekt-Config mit ausdrücklich
    ausgeschaltetem Schalter."""
    config = copy.deepcopy(CONFIG)
    config["market_cap_onchain_bounds"]["allow_onchain_bounds"] = False
    return config


def enabled_config(**overrides):
    config = copy.deepcopy(CONFIG)
    config["market_cap_onchain_bounds"]["allow_onchain_bounds"] = True
    config["market_cap_onchain_bounds"].update(overrides)
    return config


@pytest.fixture(autouse=True)
def no_network():
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=AssertionError("network access in test"),
    ):
        yield


def _on_curve(count, tag):
    found, index = [], 0
    while len(found) < count:
        address = _b58encode(bytes([tag]) + index.to_bytes(31, "big"))
        index += 1
        if is_on_curve(address):
            found.append(address)
    return found


WALLETS = _on_curve(120, 21)
CREATOR = _on_curve(1, 22)[0]
POOL_AUTHORITY = "9eTpuokzkShkz9GSFrQ1cPpow8beSx1ZXE7UtTQUrAFT"  # off-curve


def pct(value):
    return int(round(SUPPLY * value / 100))


def rows(top_pct=2.0, others=40, other_pct=1.0, creator_pct=0.5):
    result = [(f"top{i}", WALLETS[i], pct(top_pct)) for i in range(10)]
    result += [(f"acc{i}", WALLETS[20 + i], pct(other_pct)) for i in range(others)]
    result.append(("creator-acc", CREATOR, pct(creator_pct)))
    rest = SUPPLY - sum(amount for _, _, amount in result)
    result.append(("POOLVAULT", POOL_AUTHORITY, rest))
    return result


def full_list(holder_rows=None, program=TOKEN_PROGRAM_ID):
    holder_rows = holder_rows or rows()
    return {
        "mint": MINT,
        "data_scope": "full_token_account_list",
        "token_program": program,
        "accounts": [
            {"address": a, "owner": o, "amount": m} for a, o, m in holder_rows
        ],
        "supply_amount_raw": SUPPLY,
        "supply_decimals": DECIMALS,
        "sample_owner_mapping_complete": True,
        "pages": 1,
        "accounts_total": len(holder_rows),
        "accounts_nonzero": len(holder_rows),
        "slot_supply_before": 1000,
        "page_slots": [1005],
        "slot_supply_after": 1010,
        "slot_span": 10,
    }


def security(freeze=True, mint_revoked=True, price=0.001, labels_complete=True):
    return {
        "mint": MINT,
        "mint_authority_revoked": mint_revoked,
        "freeze_authority_revoked": freeze,
        "lp_burned_or_locked": True,
        "creator_address": CREATOR,
        "holder_labels": {
            "pool_vault_accounts": ["POOLVAULT"],
            "pool_owner_addresses": [POOL],
            "exchange_addresses": [],
            "locker_addresses": [],
            "invalid_label_rows": 0,
            "labels_complete": labels_complete,
        },
        "market_liquidity_usd_by_pool": {POOL: 50000.0},
        "rugcheck_price_usd": price,
    }


CHECKS = [
    "mint_authority_revoked",
    "freeze_authority_revoked",
    "lp_burned_or_locked",
    "liquidity_cross_check",
    "top10_share_pct",
    "dev_share_pct",
    "permanent_delegate_absent",
    "token_extensions_safe",
    "market_cap_usd",
]


def pool(price=0.001, reported=None, extra_missing=(), address=POOL):
    return {
        "token_address": MINT,
        "pool_address": address,
        "liquidity_usd": 50000.0,
        "status": "incomplete",
        "rejection_reasons": [],
        "missing_checks": CHECKS + list(extra_missing),
        "price_usd_geckoterminal": price,
        "market_cap_usd_reported": reported,
    }


def wallet():
    return {"owner": SYSTEM_PROGRAM_ID, "executable": False, "lamports": 5_000_000, "data_length": 0}


class Accounts:
    def __init__(self, infos=None, slot=1020, error=None):
        self.infos = infos or {}
        self.slot = slot
        self.error = error
        self.calls = []

    def __call__(self, addresses, *, min_slot):
        self.calls.append(list(addresses))
        if self.error:
            raise self.error
        return {"slot": self.slot, "accounts": [self.infos.get(a, wallet()) for a in addresses]}


class Frozen:
    def __init__(self, addresses=(), slot=1015):
        self.addresses = set(addresses)
        self.slot = slot
        self.calls = []

    def __call__(self, mint, program, *, min_slot):
        self.calls.append((mint, program, min_slot))
        return {"slot": self.slot, "addresses": set(self.addresses)}


def scan(
    pools=None,
    sec=None,
    snapshot=None,
    mint_info=None,
    config=None,
    accounts=None,
    frozen=None,
    sample_error=True,
):
    config = config or enabled_config()
    accounts = accounts if accounts is not None else Accounts()
    frozen = frozen if frozen is not None else Frozen()

    patches = [
        patch("desk.security_scan.prepare_pools", return_value=pools or [pool()]),
        patch("desk.security_scan.check_token_security", return_value=sec or security()),
        patch(
            "desk.security_scan.fetch_mint_info",
            return_value=mint_info or make_mint_info(mint=MINT),
        ),
        patch(
            "desk.security_scan.fetch_full_holder_list",
            **(
                {"side_effect": snapshot}
                if isinstance(snapshot, BaseException)
                else {"return_value": snapshot or full_list()}
            ),
        ),
        patch(
            "desk.security_scan.fetch_holder_snapshot",
            side_effect=solana_rpc.SolanaRpcError("no sample in test"),
        ),
        patch("desk.security_scan.fetch_authority_accounts", accounts),
        patch("desk.security_scan.fetch_frozen_token_accounts", frozen),
    ]

    for item in patches:
        item.start()

    try:
        result = scan_pool_security([{}], config)
    finally:
        for item in patches:
            item.stop()

    return result, accounts, frozen


def first(result):
    return result["pools"][0]


# --------------------------------------------------
# Schalter
# --------------------------------------------------

def test_switch_off_makes_no_queries():
    # Schalter aus: conftest-Sperre würde jeden Abruf scheitern lassen.
    result, accounts, frozen = scan(config=disabled_config())
    item = first(result)

    assert accounts.calls == [] and frozen.calls == []
    assert item["market_cap_onchain_bounds"] == {"status": "disabled"}
    assert item["missing_checks"] == ["market_cap_usd"]
    assert item["status"] == "incomplete"
    assert result["authority_account_calls"] == 0
    assert result["onchain_bounds_attempted"] == 0


def test_switch_off_with_real_default_functions_never_calls_rpc():
    patches = [
        patch("desk.security_scan.prepare_pools", return_value=[pool()]),
        patch("desk.security_scan.check_token_security", return_value=security()),
        patch("desk.security_scan.fetch_mint_info", return_value=make_mint_info(mint=MINT)),
        patch("desk.security_scan.fetch_full_holder_list", return_value=full_list()),
    ]
    for item in patches:
        item.start()
    try:
        result = scan_pool_security([{}], disabled_config())
    finally:
        for item in patches:
            item.stop()

    assert first(result)["status"] == "incomplete"


# --------------------------------------------------
# Bestätigung, Ablehnung, unklar
# --------------------------------------------------

def test_confirmed_bounds_make_pool_ready_without_point_value():
    result, accounts, frozen = scan()
    item = first(result)
    bounds = item["market_cap_onchain_bounds"]

    assert item["status"] == "ready_for_full_risk_screen"
    assert item["missing_checks"] == []
    assert item["market_cap_basis"] == "onchain_bounds"
    # Kein Punktwert, keine "offizielle" Umlaufmenge
    assert item["market_cap_usd"] is None
    assert item["market_cap_evaluation"]["market_cap_usd"] is None
    assert item["market_cap_evaluation"]["status"] == "confirmed"
    assert item["market_cap_evaluation"]["reason"] == "onchain_bounds_in_range"
    assert bounds["decision"]["market_cap_heuristic_lower_bound_usd"] == pytest.approx(400_000)
    assert bounds["decision"]["market_cap_upper_bound_usd"] == pytest.approx(1_000_000)
    assert bounds["wallet_like_lower_bound"]["note"] == NOTE
    assert len(accounts.calls) == 1
    assert set(accounts.calls[0]) == set(WALLETS[20:60])
    assert len(frozen.calls) == 1
    assert result["authority_account_calls"] == 1
    assert result["frozen_account_checks"] == 1
    assert result["onchain_bounds_attempted"] == 1
    assert result["onchain_bounds_failed"] == 0


@pytest.mark.parametrize("program", [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID])
def test_confirms_for_classic_and_token2022(program):
    result, _, frozen = scan(
        snapshot=full_list(program=program),
        mint_info=make_mint_info(mint=MINT, token_program=program),
    )
    assert first(result)["status"] == "ready_for_full_risk_screen"
    assert frozen.calls[0][1] == program


def test_lower_bound_below_minimum_stays_incomplete():
    # 20 Wallets je 1 % = 200k $ < 300k $
    result, _, _ = scan(snapshot=full_list(rows(others=20)))
    item = first(result)

    assert item["status"] == "incomplete"
    assert item["missing_checks"] == ["market_cap_usd"]
    assert item["market_cap_onchain_bounds"]["wallet_like_lower_bound"]["reason"] == "target_unreachable"
    assert item["market_cap_onchain_bounds"]["decision"]["reason"] == "onchain_lower_bound_below_minimum"


def test_upper_bound_above_maximum_stays_incomplete():
    # Preis 0,03 $: Obergrenze 30 Mio. $, Untergrenze 40 % = 12 Mio. $
    result, _, _ = scan(pools=[pool(price=0.03)], sec=security(price=0.03))
    item = first(result)

    assert item["status"] == "incomplete"
    assert item["market_cap_onchain_bounds"]["decision"]["reason"] == "onchain_upper_bound_above_maximum"
    # Ziel ist dann der Nachweis > 20 Mio. $ (unerreichbar) -> keine Abfrage
    assert item["market_cap_onchain_bounds"]["wallet_like_lower_bound"]["reason"] == "target_unreachable"


def test_lower_bound_above_maximum_rejects():
    # Preis 0,06 $: Untergrenze 40 % = 24 Mio. $ > 20 Mio. $
    result, _, _ = scan(pools=[pool(price=0.06)], sec=security(price=0.06))
    item = first(result)

    assert item["status"] == "rejected"
    assert item["rejection_reasons"] == ["market_cap_above_maximum_onchain_lower_bound"]
    assert item["market_cap_evaluation"]["basis"] == "onchain_bounds"


def test_non_wallets_reduce_lower_bound():
    # 15 der 40 Wallets sind Programme/ohne SOL -> 25 % = 250k $ -> unklar
    infos = {address: None for address in WALLETS[20:35]}
    result, _, _ = scan(accounts=Accounts(infos))
    item = first(result)

    assert item["status"] == "incomplete"
    assert item["market_cap_onchain_bounds"]["wallet_like_lower_bound"]["wallet_like_lower_bound_raw"] == pct(25)


@pytest.mark.parametrize(
    "others, status, lower_usd, calls",
    [
        # 34 % x 0,0009 = 306k -> bestätigt
        (34, "ready_for_full_risk_screen", 306_000, 1),
        # 33 % x 0,0009 = 297k (mit hohem Preis wären es 326,7k):
        # Ziel mit niedrigem Preis unerreichbar -> keine Abfrage
        (33, "incomplete", 0, 0),
    ],
)
def test_lower_bound_uses_lower_price(others, status, lower_usd, calls):
    result, accounts, _ = scan(
        pools=[pool(price=0.0009)],
        sec=security(price=0.00099),
        snapshot=full_list(rows(others=others)),
    )
    bounds = first(result)["market_cap_onchain_bounds"]["decision"]
    assert bounds["price_low_usd"] == 0.0009
    assert bounds["price_high_usd"] == 0.00099
    assert bounds["market_cap_heuristic_lower_bound_usd"] == pytest.approx(lower_usd)
    assert len(accounts.calls) == calls
    assert first(result)["status"] == status


# --------------------------------------------------
# Vorbedingungen
# --------------------------------------------------

@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"sec": security(freeze=None)}, "other_checks_open"),
        ({"pools": [pool(extra_missing=["unambiguous_base_token"])]}, "other_checks_open"),
        ({"sec": security(labels_complete=False)}, "other_checks_open"),
        ({"snapshot": full_list(rows(top_pct=3.0))}, "already_rejected"),
        ({"snapshot": full_list(rows(creator_pct=4.0))}, "already_rejected"),
        (
            {"mint_info": make_mint_info(mint=MINT, delegate_status="present", delegate="D")},
            "other_checks_open",
        ),
    ],
)
def test_preconditions_skip_without_queries(kwargs, reason):
    result, accounts, frozen = scan(**kwargs)
    item = first(result)

    assert accounts.calls == [] and frozen.calls == []
    assert item["market_cap_onchain_bounds"] == {"status": "skipped", "reason": reason}
    assert item["status"] != "ready_for_full_risk_screen"


def test_single_price_source_is_never_enough():
    # Nur GeckoTerminal-Preis: market_cap bleibt bei
    # circulating_supply_not_confirmed, aber On-Chain-Spanne verlangt
    # zwei übereinstimmende Preise.
    result, accounts, _ = scan(sec=security(price=None))
    item = first(result)

    assert accounts.calls == []
    assert item["market_cap_onchain_bounds"] == {
        "status": "skipped",
        "reason": "two_agreeing_prices_required",
    }
    assert item["status"] == "incomplete"


def test_conflicting_prices_never_reach_bounds():
    result, accounts, _ = scan(sec=security(price=0.002))
    item = first(result)

    assert accounts.calls == []
    assert "market_cap_onchain_bounds" not in item
    assert item["market_cap_evaluation"]["reason"] == "price_sources_disagree"
    assert item["status"] == "incomplete"


def test_sample_fallback_never_reaches_bounds():
    result, accounts, _ = scan(
        snapshot=solana_rpc.HolderListError("slot_span_exceeded"),
    )
    item = first(result)

    assert accounts.calls == []
    assert item["status"] == "incomplete"


def test_reported_market_cap_path_is_unchanged():
    # Gemeldete Marktkapitalisierung bestätigt wie bisher; keine Abfrage.
    result, accounts, frozen = scan(pools=[pool(reported=500_000)])
    item = first(result)

    assert accounts.calls == [] and frozen.calls == []
    assert item["market_cap_basis"] == "reported_circulating_supply"
    assert item["market_cap_usd"] == 500_000
    assert "market_cap_onchain_bounds" not in item


def test_reported_value_above_upper_bound_is_not_overridden():
    result, accounts, _ = scan(pools=[pool(reported=5_000_000)])
    item = first(result)

    assert accounts.calls == []
    assert item["market_cap_evaluation"]["reason"] == "reported_market_cap_exceeds_supply_value"
    assert item["status"] == "incomplete"


def test_upper_bound_rejection_unchanged_and_no_queries():
    result, accounts, _ = scan(pools=[pool(price=0.0001)], sec=security(price=0.0001))
    item = first(result)

    assert accounts.calls == []
    assert item["rejection_reasons"] == ["market_cap_below_minimum"]


# --------------------------------------------------
# Fehler, Slots, Abruflimit
# --------------------------------------------------

@pytest.mark.parametrize(
    "accounts, frozen, reason",
    [
        (Accounts(error=OnchainBoundsError("rpc_error")), None, "rpc_error"),
        (Accounts(slot=1009), None, "slot_inconsistent"),
        (Accounts(slot=1151), None, "slot_span_exceeded"),
        (None, Frozen(slot=900), "slot_inconsistent"),
        (Accounts(error=OnchainBoundsError("authority_response_invalid")), None, "authority_response_invalid"),
    ],
)
def test_errors_keep_pool_incomplete(accounts, frozen, reason):
    result, _, _ = scan(accounts=accounts, frozen=frozen)
    item = first(result)
    bounds = item["market_cap_onchain_bounds"]

    assert item["status"] == "incomplete"
    assert item["missing_checks"] == ["market_cap_usd"]
    assert bounds["status"] == "error"
    assert bounds["decision"] == {"status": "unclear", "reason": reason}
    assert result["onchain_bounds_failed"] == 1


class FailingFrozen:
    def __init__(self):
        self.calls = []

    def __call__(self, mint, program, *, min_slot):
        self.calls.append((mint, program, min_slot))
        raise OnchainBoundsError("frozen_rpc_error")


@pytest.mark.parametrize(
    "accounts, frozen, reason",
    [
        (Accounts(error=OnchainBoundsError("rpc_error")), None, "rpc_error"),
        (None, FailingFrozen(), "frozen_rpc_error"),
    ],
)
def test_project_config_enabled_failed_query_stays_incomplete(accounts, frozen, reason):
    # Echte Projekt-Config (ohne Kopie/Änderung): Schalter ist an, die
    # Abfrage wird tatsächlich versucht, scheitert und darf nie "bereit"
    # ergeben.
    assert CONFIG["market_cap_onchain_bounds"]["allow_onchain_bounds"] is True
    result, used_accounts, used_frozen = scan(
        config=CONFIG, accounts=accounts, frozen=frozen
    )
    item = first(result)
    bounds = item["market_cap_onchain_bounds"]

    # Frozen-Abfrage läuft zuerst und wird in beiden Fällen aufgerufen.
    assert used_frozen.calls
    if reason == "rpc_error":
        # Authority-Fehlerfall: Authority-Abfrage wurde tatsächlich aufgerufen.
        assert used_accounts.calls
    else:
        # Frozen-Fehlerfall: danach keine Authority-Abfrage mehr.
        assert used_accounts.calls == []
    assert bounds["status"] == "error"
    assert bounds["decision"] == {"status": "unclear", "reason": reason}
    assert item["status"] == "incomplete"
    assert item["status"] != "ready_for_full_risk_screen"
    assert item["missing_checks"] == ["market_cap_usd"]
    assert item["market_cap_basis"] != "onchain_bounds"
    assert result["onchain_bounds_attempted"] == 1
    assert result["onchain_bounds_failed"] == 1
    assert all(
        pool["status"] != "ready_for_full_risk_screen" for pool in result["pools"]
    )


def test_slot_span_limit_comes_from_config():
    result, _, _ = scan(config=enabled_config(max_slot_span=15), accounts=Accounts(slot=1016))
    assert first(result)["market_cap_onchain_bounds"]["decision"]["reason"] == "slot_span_exceeded"


def test_call_limit_comes_from_config():
    # 40 Wallets je 0,75 % in Blöcken: mit 1 Aufruf erreicht, Limit 1 genügt.
    # Mit 140 kleineren Wallets und Limit 1 bleibt es unvollständig.
    many = [(f"top{i}", WALLETS[i], pct(2.0)) for i in range(10)]
    extra = _on_curve(150, 23)
    many += [(f"m{i}", extra[i], pct(0.25)) for i in range(140)]
    many.append(("creator-acc", CREATOR, pct(0.5)))
    many.append(("POOLVAULT", POOL_AUTHORITY, SUPPLY - sum(a for _, _, a in many)))

    accounts = Accounts()
    result, _, _ = scan(
        config=enabled_config(max_account_calls_per_token=1),
        snapshot=full_list(many),
        accounts=accounts,
    )
    lower = first(result)["market_cap_onchain_bounds"]["wallet_like_lower_bound"]

    assert len(accounts.calls) == 1
    assert lower["reason"] == "call_limit_reached"
    assert lower["wallet_like_lower_bound_raw"] == 100 * pct(0.25)
    assert first(result)["status"] == "incomplete"


def test_two_pools_of_same_token_query_once_and_exclude_both_pools():
    second_pool = WALLETS[119]  # zweiter DexPaprika-Pool hält Token selbst
    holder_rows = rows()
    holder_rows.insert(0, ("pool2-acc", second_pool, pct(1)))
    holder_rows[-1] = ("POOLVAULT", POOL_AUTHORITY, holder_rows[-1][2] - pct(1))

    sec = security()
    sec["market_liquidity_usd_by_pool"][second_pool] = 50000.0
    accounts = Accounts()
    result, _, frozen = scan(
        pools=[pool(), pool(address=second_pool)],
        sec=sec,
        snapshot=full_list(holder_rows),
        accounts=accounts,
    )

    assert len(accounts.calls) == 1
    assert len(frozen.calls) == 1
    assert second_pool not in accounts.calls[0]
    assert [p["status"] for p in result["pools"]] == ["ready_for_full_risk_screen"] * 2


# --------------------------------------------------
# Ende-zu-Ende: echte Abruffunktionen gegen gemockte RPC
# --------------------------------------------------

def token_account(address, authority, amount, program):
    raw = _b58decode(authority) + struct.pack("<Q", amount)
    return {
        "pubkey": address,
        "account": {"data": [base64.b64encode(raw).decode(), "base64"], "owner": program},
    }


def raw_wallet(lamports=5_000_000):
    return {
        "owner": SYSTEM_PROGRAM_ID,
        "executable": False,
        "lamports": lamports,
        "data": ["", "base64"],
        "rentEpoch": 0,
        "space": 0,
    }


class FakeRpc:
    """Beantwortet alle RPC-Methoden der Prüfkette."""

    def __init__(self, program, holder_rows, extensions=None, null_wallets=()):
        self.program = program
        self.rows = holder_rows
        self.extensions = extensions
        self.null_wallets = set(null_wallets)
        self.methods = []
        self.supply_calls = 0

    def __call__(self, method, params, rpc_url=None, timeout=20):
        self.methods.append(method)

        if method == "getAccountInfo":
            info = {
                "decimals": DECIMALS,
                "supply": str(SUPPLY),
                "mintAuthority": None,
                "freezeAuthority": None,
            }
            if self.extensions is not None:
                info["extensions"] = self.extensions
            return {
                "context": {"slot": 990},
                "value": {
                    "owner": self.program,
                    "space": 82 if self.extensions is None else 300,
                    "data": {"parsed": {"type": "mint", "info": info}},
                },
            }

        if method == "getTokenSupply":
            self.supply_calls += 1
            slot = 1000 if self.supply_calls == 1 else 1010
            return {"context": {"slot": slot}, "value": {"amount": str(SUPPLY), "decimals": DECIMALS}}

        if method == "getProgramAccountsV2":
            filters = params[1]["filters"]
            if {"memcmp": {"offset": 108, "bytes": "3"}} in filters:
                assert params[1]["minContextSlot"] == 1010
                return {"context": {"slot": 1012}, "value": {"accounts": [], "count": 0, "paginationKey": None}}
            items = [token_account(a, o, m, self.program) for a, o, m in self.rows]
            return {"context": {"slot": 1005}, "value": {"accounts": items, "count": len(items), "paginationKey": None}}

        if method == "getMultipleAccounts":
            addresses, config = params
            assert config["minContextSlot"] == 1010
            assert len(addresses) <= 100
            return {
                "context": {"slot": 1015},
                "value": [None if a in self.null_wallets else raw_wallet() for a in addresses],
            }

        raise AssertionError(f"unexpected method {method}")


def real_scan(fake, monkeypatch, config=None):
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.test.invalid")
    patches = [
        patch("desk.security_scan.prepare_pools", return_value=[pool()]),
        patch("desk.security_scan.check_token_security", return_value=security()),
        patch("desk.security_scan.fetch_mint_info", solana_rpc.fetch_mint_info),
        patch("desk.security_scan.fetch_full_holder_list", solana_rpc.fetch_full_holder_list),
        patch("desk.security_scan.fetch_authority_accounts", solana_rpc.fetch_authority_accounts),
        patch("desk.security_scan.fetch_frozen_token_accounts", solana_rpc.fetch_frozen_token_accounts),
        patch("desk.solana_rpc._rpc_call", fake),
    ]
    for item in patches:
        item.start()
    try:
        return scan_pool_security([{}], config or enabled_config())
    finally:
        for item in patches:
            item.stop()


@pytest.mark.parametrize(
    "program, extensions",
    [
        (TOKEN_PROGRAM_ID, None),
        (TOKEN_2022_PROGRAM_ID, [{"extension": "metadataPointer", "state": {}}]),
    ],
)
def test_end_to_end_confirms(program, extensions, monkeypatch):
    fake = FakeRpc(program, rows(), extensions)
    result = real_scan(fake, monkeypatch)
    item = first(result)

    assert item["status"] == "ready_for_full_risk_screen"
    assert item["market_cap_basis"] == "onchain_bounds"
    assert fake.methods.count("getMultipleAccounts") == 1
    # 1x Holder-Liste, 1x Frozen-Abfrage
    assert fake.methods.count("getProgramAccountsV2") == 2


def test_end_to_end_null_authorities_not_counted(monkeypatch):
    fake = FakeRpc(TOKEN_PROGRAM_ID, rows(), null_wallets=WALLETS[20:35])
    result = real_scan(fake, monkeypatch)
    item = first(result)
    lower = item["market_cap_onchain_bounds"]["wallet_like_lower_bound"]

    assert lower["classified"] == {"wallet_like_system_account": 25, "no_sol_account_unknown": 15}
    assert item["status"] == "incomplete"


def test_end_to_end_switch_off_never_calls_bounds_methods(monkeypatch):
    fake = FakeRpc(TOKEN_PROGRAM_ID, rows())
    result = real_scan(fake, monkeypatch, config=disabled_config())

    assert "getMultipleAccounts" not in fake.methods
    assert fake.methods.count("getProgramAccountsV2") == 1
    assert first(result)["status"] == "incomplete"


def test_log_details_are_compact_and_marked_internal():
    from desk.__main__ import _check_details

    result, _, _ = scan()
    details = _check_details(first(result))["market_cap"]["onchain_bounds"]

    assert details["status"] == "completed"
    assert details["decision_status"] == "confirmed"
    assert details["note"] == NOTE
    assert details["account_calls"] == 1
    assert details["max_account_calls"] == 10
    assert details["market_cap_heuristic_lower_bound_usd"] == pytest.approx(400_000)
    assert "accounts" not in details


def test_log_details_without_bounds():
    from desk.__main__ import _check_details

    result, _, _ = scan(config=disabled_config())
    details = _check_details(first(result))["market_cap"]["onchain_bounds"]
    assert details["status"] == "disabled"
    assert _check_details({})["market_cap"]["onchain_bounds"] is None


def test_log_separates_heuristic_lower_bound_from_proven_supply():
    from desk.__main__ import _check_details

    result, _, _ = scan()
    item = first(result)
    details = _check_details(item)["market_cap"]["onchain_bounds"]

    # Auch bei Bestätigung: nichts davon gilt als nachgewiesen.
    assert details["decision_status"] == "confirmed"
    assert details["lower_bound_kind"] == "heuristic_wallet_like_lower_bound"
    assert details["private_key_proven"] is False
    assert details["free_transferability_proven"] is False
    assert details["economic_circulating_supply_proven"] is False
    assert details["residual_risk"] == "on_curve_address_without_known_private_key"
    assert details["note"] == "heuristic_wallet_like_lower_bound_not_proven_circulating_supply"
    assert set(details["classified"]) == {"wallet_like_system_account"}
    # keine Umlaufmenge, kein Punktwert
    assert item["market_cap_usd"] is None
    assert "circulating_supply" not in details
