"""Eigene On-Chain-Spanne der Marktkapitalisierung (PR 2).

Kein Netzwerk: RPC-Aufrufe laufen gegen gemockte Antworten.
"""

import base64
import copy
from unittest.mock import patch

import pytest

from desk import circulating_bounds as cb
from desk.config import load_config
from desk.market_cap import (
    evaluate_onchain_bounds,
    onchain_target_amount_raw,
)
from desk.solana_rpc import (
    SYSTEM_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    OnchainBoundsError,
    SolanaRpcError,
    _b58encode,
    fetch_authority_accounts,
    fetch_frozen_token_accounts,
    is_on_curve,
)


CONFIG = load_config()
MINT = "TOKEN123"
SUPPLY = 1_000_000_000_000_000  # 1 Mrd. Token, 6 Dezimalstellen
DECIMALS = 6
SECRET_URL = "https://mainnet.helius-rpc.test/?api-key=GEHEIM-123456"


@pytest.fixture(autouse=True)
def no_network():
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=AssertionError("network access in test"),
    ):
        yield


# --------------------------------------------------
# Testadressen
# --------------------------------------------------

def _addresses(on_curve, count, tag):
    found = []
    index = 0

    while len(found) < count:
        address = _b58encode(bytes([tag]) + index.to_bytes(31, "big"))
        index += 1

        if is_on_curve(address) == on_curve:
            found.append(address)

    return found


WALLETS = _addresses(True, 400, 7)
PDAS = _addresses(False, 20, 8)
CREATOR = _addresses(True, 1, 9)[0]
POOL_OWNER = PDAS[19]
DEX_POOL = _addresses(True, 2, 10)
EXCHANGE = _addresses(True, 1, 11)[0]
LOCKER = _addresses(True, 1, 12)[0]
INCINERATOR = "1nc1nerator11111111111111111111111111111111"


def pct(value):
    return int(round(SUPPLY * value / 100))


def wallet_info(lamports=1_000_000, owner=SYSTEM_PROGRAM_ID, executable=False, data=b""):
    return {
        "owner": owner,
        "executable": executable,
        "lamports": lamports,
        "data_length": len(data),
    }


def raw_info(lamports=1_000_000, owner=SYSTEM_PROGRAM_ID, executable=False, data=b""):
    """Rohform eines getMultipleAccounts-Eintrags (base64)."""
    return {
        "owner": owner,
        "executable": executable,
        "lamports": lamports,
        "data": [base64.b64encode(data).decode(), "base64"],
        "rentEpoch": 18446744073709551615,
        "space": len(data),
    }


def labels(complete=True, exchanges=(), lockers=(), pool_owners=(POOL_OWNER,)):
    return {
        "pool_vault_accounts": ["POOLVAULT"],
        "pool_owner_addresses": list(pool_owners),
        "exchange_addresses": list(exchanges),
        "locker_addresses": list(lockers),
        "invalid_label_rows": 0 if complete else 1,
        "labels_complete": complete,
    }


def security(**kwargs):
    return {
        "mint": MINT,
        "creator_address": CREATOR,
        "holder_labels": labels(**kwargs),
    }


def full_list(rows, program=TOKEN_PROGRAM_ID, slot_before=1000, slot_after=1010):
    """rows: (Konto, Authority, Menge). Rest geht an den Pool-Vault."""
    rows = list(rows)
    rest = SUPPLY - sum(amount for _, _, amount in rows)
    assert rest >= 0
    accounts = [{"address": a, "owner": o, "amount": m} for a, o, m in rows]

    if rest:
        accounts.append({"address": "POOLVAULT", "owner": POOL_OWNER, "amount": rest})

    return {
        "mint": MINT,
        "data_scope": "full_token_account_list",
        "token_program": program,
        "accounts": accounts,
        "supply_amount_raw": SUPPLY,
        "supply_decimals": DECIMALS,
        "sample_owner_mapping_complete": True,
        "slot_supply_before": slot_before,
        "slot_supply_after": slot_after,
    }


def standard_rows(top=10, top_pct=2.0, others=40, other_pct=1.0):
    """Top-10 je 2 %, danach 40 Wallets je 1 %, Creator 0,5 %."""
    rows = [(f"top{i}", WALLETS[i], pct(top_pct)) for i in range(top)]
    rows += [
        (f"acc{i}", WALLETS[100 + i], pct(other_pct)) for i in range(others)
    ]
    rows.append(("creator-acc", CREATOR, pct(0.5)))
    return rows


class FakeAccounts:
    """Ersatz für fetch_authority_accounts mit Protokoll der Aufrufe."""

    def __init__(self, infos=None, slot=1020, slots=None):
        self.infos = infos or {}
        self.slot = slot
        self.slots = list(slots) if slots else None
        self.calls = []

    def __call__(self, addresses, *, min_slot):
        self.calls.append((list(addresses), min_slot))
        slot = self.slots.pop(0) if self.slots else self.slot
        return {
            "slot": slot,
            "accounts": [self.infos.get(a, wallet_info()) for a in addresses],
        }


class FakeFrozen:
    def __init__(self, addresses=(), slot=1015):
        self.addresses = set(addresses)
        self.slot = slot
        self.calls = []

    def __call__(self, mint, program, *, min_slot):
        self.calls.append((mint, program, min_slot))
        return {"slot": self.slot, "addresses": set(self.addresses)}


def compute(snapshot, *, target=pct(1), max_calls=10, max_slot_span=150,
            accounts=None, frozen=None, sec=None, pools=()):
    accounts = accounts or FakeAccounts()
    frozen = frozen or FakeFrozen()
    result = cb.compute_wallet_lower_bound(
        mint=MINT,
        snapshot=snapshot,
        security=sec or security(),
        pool_addresses=pools,
        target_amount_raw=target,
        max_calls=max_calls,
        max_slot_span=max_slot_span,
        fetch_accounts=accounts,
        fetch_frozen=frozen,
    )
    return result, accounts, frozen


def queried(accounts):
    return [a for addresses, _ in accounts.calls for a in addresses]


# --------------------------------------------------
# On-Curve-Prüfung (Ed25519)
# --------------------------------------------------

@pytest.mark.parametrize(
    "address, expected",
    [
        # echte Wallets (System Program als Owner, TTP-Test vom 09.11.2026)
        ("6sHjiBFNz3AVFwf9JnUrFFkigTgrCXmFjNzNRaNVG8V8", True),
        ("J5yGabLykSZ4Fjg8coukvWJbVobnUduzC9WAHNzJLcN8", True),
        ("43Us3wNqRDTyBaQRqXgKCfoZBcNcgYerWKwu7KowE1D3", True),
        ("GJfcXVv8TVinVrTYvm2K8ahLNjxnEdPc4cbGu9pZrKFo", True),
        # Programm-IDs sind Keypair-Adressen
        (TOKEN_PROGRAM_ID, True),
        # System Program: 32 Null-Bytes liegen auf der Kurve (deshalb
        # steht es auf der Burn-Liste und wird nie als Wallet gezählt)
        (SYSTEM_PROGRAM_ID, True),
        # PDAs / Off-Curve
        ("9eTpuokzkShkz9GSFrQ1cPpow8beSx1ZXE7UtTQUrAFT", False),  # Pool (pAMMBay6)
        ("AAm881SnV1bL5jZwRvcd4Ct2xZvMgMJTt4YrxTBG8n7D", False),  # ATA
        (INCINERATOR, False),
    ],
)
def test_on_curve_known_addresses(address, expected):
    assert is_on_curve(address) is expected


@pytest.mark.parametrize("bad", ["", "0OIl", "abc", "1" * 50, None, 5])
def test_on_curve_rejects_invalid_address(bad):
    with pytest.raises(ValueError):
        is_on_curve(bad)


def test_on_curve_roughly_half_of_random_points():
    # Etwa die Hälfte aller 32-Byte-Werte liegt auf der Kurve.
    share = len(_addresses(True, 200, 3)) / (
        len(_addresses(True, 200, 3)) + len(_addresses(False, 200, 3))
    )
    assert share == 0.5


# --------------------------------------------------
# Klassifizierung einer Authority
# --------------------------------------------------

@pytest.mark.parametrize(
    "info, expected",
    [
        (wallet_info(), cb.WALLET),
        (None, cb.UNFUNDED),
        (wallet_info(lamports=0), cb.UNFUNDED),
        (wallet_info(executable=True, owner="BPFLoaderUpgradeab1e11111111111111111111111"), cb.EXECUTABLE),
        (wallet_info(executable=True), cb.EXECUTABLE),
        (wallet_info(owner=TOKEN_PROGRAM_ID, data=b"x" * 165), cb.TOKEN_ACCOUNT),
        (wallet_info(owner=TOKEN_2022_PROGRAM_ID, data=b"x" * 170), cb.TOKEN_ACCOUNT),
        (wallet_info(owner="pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", data=b"x" * 300), cb.PROGRAM_OWNED),
        (wallet_info(owner="SMPLecH534NA9acpos4G6x7uf3LWbCAwZQE9e8ZekMu"), cb.PROGRAM_OWNED),
        # System-Konto mit Daten (z. B. Nonce-Konto) ist kein normales Wallet
        (wallet_info(data=b"x" * 80), cb.SYSTEM_WITH_DATA),
    ],
)
def test_classify_authority_account(info, expected):
    assert cb.classify_authority_account(info) == expected


# --------------------------------------------------
# fetch_authority_accounts (getMultipleAccounts)
# --------------------------------------------------

def rpc_result(value, slot=1020):
    return {"context": {"apiVersion": "2.3.0", "slot": slot}, "value": value}


def call_accounts(result, addresses=None, min_slot=1010):
    addresses = addresses or WALLETS[:2]

    with patch("desk.solana_rpc._rpc_call", return_value=result) as mock:
        return fetch_authority_accounts(addresses, min_slot=min_slot), mock


def test_fetch_authority_accounts_parses_wallet_and_null():
    result, mock = call_accounts(
        rpc_result([raw_info(), None]),
    )

    assert result["slot"] == 1020
    assert result["accounts"] == [wallet_info(), None]
    method, params = mock.call_args.args[:2]
    assert method == "getMultipleAccounts"
    assert params[0] == WALLETS[:2]
    assert params[1] == {
        "encoding": "base64",
        "commitment": "finalized",
        "minContextSlot": 1010,
    }


def test_fetch_authority_accounts_reads_program_owner_and_data_length():
    result, _ = call_accounts(
        rpc_result(
            [
                raw_info(owner=TOKEN_PROGRAM_ID, data=b"\0" * 165),
                raw_info(owner="BPFLoaderUpgradeab1e11111111111111111111111", executable=True, data=b"\1" * 36),
            ]
        )
    )
    assert result["accounts"][0]["data_length"] == 165
    assert result["accounts"][0]["owner"] == TOKEN_PROGRAM_ID
    assert result["accounts"][1]["executable"] is True


@pytest.mark.parametrize(
    "result, reason",
    [
        ({"value": [None, None]}, "authority_slot_missing"),
        ({"context": {"slot": "1020"}, "value": [None, None]}, "authority_slot_missing"),
        (rpc_result([None, None], slot=1009), "authority_slot_before_holder_list"),
        (rpc_result([None]), "authority_response_invalid"),
        (rpc_result([None, None, None]), "authority_response_invalid"),
        (rpc_result({"a": 1}), "authority_response_invalid"),
        (rpc_result(None), "authority_response_invalid"),
        (rpc_result([raw_info(), "wallet"]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "owner": None}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "executable": "false"}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "lamports": -1}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "lamports": True}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "data": ["!!", "base64"]}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "data": ["", "jsonParsed"]}]), "authority_account_invalid"),
        (rpc_result([raw_info(), {**raw_info(), "data": {"parsed": {}}}]), "authority_account_invalid"),
        ("not a dict", "authority_slot_missing"),
    ],
)
def test_fetch_authority_accounts_rejects_bad_responses(result, reason):
    with pytest.raises(OnchainBoundsError) as error:
        call_accounts(result)

    assert error.value.reason == reason


def test_fetch_authority_accounts_rpc_error_hides_url(monkeypatch):
    monkeypatch.setenv("SOLANA_RPC_URL", SECRET_URL)

    with patch(
        "desk.solana_rpc._rpc_call",
        side_effect=SolanaRpcError(f"boom {SECRET_URL}"),
    ):
        with pytest.raises(OnchainBoundsError) as error:
            fetch_authority_accounts(WALLETS[:1], min_slot=1)

    assert error.value.reason == "rpc_error"
    assert "GEHEIM" not in str(error.value)


@pytest.mark.parametrize(
    "addresses",
    [[], WALLETS[:101], "abc", [None], [""]],
)
def test_fetch_authority_accounts_limits_addresses(addresses):
    with pytest.raises(ValueError):
        fetch_authority_accounts(addresses, min_slot=1)


@pytest.mark.parametrize("min_slot", [-1, None, "1", True])
def test_fetch_authority_accounts_requires_min_slot(min_slot):
    with pytest.raises(ValueError):
        fetch_authority_accounts(WALLETS[:1], min_slot=min_slot)


# --------------------------------------------------
# fetch_frozen_token_accounts
# --------------------------------------------------

def frozen_page(items, slot=1015, key=None, count=None):
    value = {"accounts": items, "paginationKey": key}
    value["count"] = len(items) if count is None else count
    return {"context": {"slot": slot}, "value": value}


def frozen_item(address, program=TOKEN_PROGRAM_ID):
    return {"pubkey": address, "account": {"owner": program, "data": ["", "base64"]}}


@pytest.mark.parametrize("program", [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID])
def test_fetch_frozen_accounts_filters(program):
    with patch(
        "desk.solana_rpc._rpc_call",
        return_value=frozen_page([frozen_item("acc1", program)]),
    ) as mock:
        result = fetch_frozen_token_accounts(MINT, program, min_slot=1010)

    assert result == {"slot": 1015, "addresses": {"acc1"}}
    method, params = mock.call_args.args[:2]
    assert method == "getProgramAccountsV2"
    assert params[0] == program
    config = params[1]
    assert config["minContextSlot"] == 1010
    assert {"memcmp": {"offset": 0, "bytes": MINT}} in config["filters"]
    assert {"memcmp": {"offset": 108, "bytes": "3"}} in config["filters"]
    assert ({"dataSize": 165} in config["filters"]) is (program == TOKEN_PROGRAM_ID)
    assert config["dataSlice"] == {"offset": 0, "length": 0}


def test_frozen_state_byte_is_base58_of_two():
    assert _b58encode(b"\x02") == "3"


@pytest.mark.parametrize(
    "page, reason",
    [
        (frozen_page([], key="next"), "too_many_frozen_accounts"),
        (frozen_page([], slot=1009), "frozen_slot_before_holder_list"),
        ({"value": {"accounts": []}}, "frozen_page_slot_missing"),
        (frozen_page([frozen_item("a")], count=2), "frozen_page_count_mismatch"),
        (frozen_page([{"pubkey": None, "account": {}}]), "frozen_account_invalid"),
        (frozen_page([frozen_item("a", TOKEN_2022_PROGRAM_ID)]), "frozen_account_invalid"),
    ],
)
def test_fetch_frozen_accounts_rejects_bad_responses(page, reason):
    with patch("desk.solana_rpc._rpc_call", return_value=page):
        with pytest.raises(OnchainBoundsError) as error:
            fetch_frozen_token_accounts(MINT, TOKEN_PROGRAM_ID, min_slot=1010)

    assert error.value.reason == reason


def test_fetch_frozen_accounts_unknown_program():
    with pytest.raises(OnchainBoundsError) as error:
        fetch_frozen_token_accounts(MINT, "OtherProgram", min_slot=1)

    assert error.value.reason == "unsupported_token_program"


# --------------------------------------------------
# Wallet-Untergrenze: Aggregation und Ausschlüsse
# --------------------------------------------------

def test_standard_case_counts_only_wallets_outside_top10():
    result, accounts, frozen = compute(full_list(standard_rows()), target=pct(40))

    assert result["status"] == cb.COMPLETED
    # 40 Wallets je 1 %; Top-10, Creator und Pool nicht
    assert result["wallet_lower_bound_raw"] == pct(40)
    assert result["excluded_authorities"] == {"top10": 10, "creator": 1, "pool": 1}
    assert set(queried(accounts)) == set(WALLETS[100:140])
    assert result["note"] == cb.NOTE
    assert frozen.calls == [(MINT, TOKEN_PROGRAM_ID, 1010)]
    assert all(min_slot == 1010 for _, min_slot in accounts.calls)


def test_multiple_token_accounts_per_authority_are_aggregated_first():
    # Wallet W hat 5 Konten zu je 0,6 % = 3 % -> gehört zu den Top-10,
    # obwohl jedes einzelne Konto kleiner als 2 % ist.
    big = WALLETS[300]
    rows = standard_rows(top=9)
    rows += [(f"multi{i}", big, pct(0.6)) for i in range(5)]
    result, accounts, _ = compute(full_list(rows), target=pct(40))

    assert big not in queried(accounts)
    assert result["excluded_amount_raw"]["top10"] == pct(18) + 5 * pct(0.6)
    assert result["wallet_lower_bound_raw"] == pct(40)


def test_aggregated_wallet_amount_is_counted_once():
    wallet = WALLETS[100]
    rows = standard_rows(others=0)
    rows += [("w-a", wallet, pct(0.5)), ("w-b", wallet, pct(0.25))]
    result, accounts, _ = compute(full_list(rows), target=pct(0.75))

    assert queried(accounts) == [wallet]
    assert result["wallet_lower_bound_raw"] == pct(0.5) + pct(0.25)


def test_all_label_lists_apply_to_authorities():
    rows = standard_rows(others=2)
    rows += [
        ("burn-acc", INCINERATOR, pct(1)),
        ("sys-acc", SYSTEM_PROGRAM_ID, pct(1)),
        ("ex-acc", EXCHANGE, pct(1)),
        ("lock-acc", LOCKER, pct(1)),
        ("dex-acc", DEX_POOL[0], pct(1)),
        ("dex2-acc", DEX_POOL[1], pct(1)),
    ]
    sec = security(exchanges=[EXCHANGE], lockers=[LOCKER])
    result, accounts, _ = compute(
        full_list(rows), target=pct(2), sec=sec, pools=DEX_POOL
    )

    assert set(queried(accounts)) == set(WALLETS[100:102])
    assert result["excluded_authorities"] == {
        "top10": 10,
        "creator": 1,
        "pool": 3,
        "burn": 2,
        "exchange": 1,
        "locker": 1,
    }
    assert result["wallet_lower_bound_raw"] == pct(2)


def test_pool_vault_account_excludes_its_authority():
    # Vault-Konto gehört einer sonst unbekannten Authority.
    authority = WALLETS[350]
    rows = standard_rows(others=1)
    rows += [("POOLVAULT", authority, pct(5)), ("other", authority, pct(0.1))]
    snap = full_list(rows)
    # Rest-Konto ohne doppelte Vault-Adresse
    snap["accounts"][-1]["address"] = "REST"
    result, accounts, _ = compute(snap, target=pct(100))

    assert authority not in queried(accounts)
    assert result["excluded_authorities"]["pool"] == 2


def test_creator_is_excluded_even_if_outside_top10():
    rows = standard_rows()
    result, accounts, _ = compute(full_list(rows), target=pct(100))
    assert CREATOR not in queried(accounts)


def test_off_curve_authorities_are_excluded_without_query():
    rows = standard_rows(others=1)
    rows += [(f"pda{i}", PDAS[i], pct(1)) for i in range(5)]
    result, accounts, _ = compute(full_list(rows), target=pct(1))

    assert not set(PDAS[:5]) & set(queried(accounts))
    assert result["excluded_authorities"]["off_curve_pda"] == 5
    assert result["wallet_lower_bound_raw"] == pct(1)


@pytest.mark.parametrize(
    "info, category",
    [
        (None, cb.UNFUNDED),
        (wallet_info(lamports=0), cb.UNFUNDED),
        (wallet_info(owner=TOKEN_PROGRAM_ID, data=b"x" * 165), cb.TOKEN_ACCOUNT),
        (wallet_info(owner="SMPLecH534NA9acpos4G6x7uf3LWbCAwZQE9e8ZekMu"), cb.PROGRAM_OWNED),
        (wallet_info(executable=True), cb.EXECUTABLE),
        (wallet_info(data=b"x" * 80), cb.SYSTEM_WITH_DATA),
    ],
)
def test_non_wallet_authorities_are_not_counted(info, category):
    rows = standard_rows(others=2)
    special = WALLETS[100]
    accounts = FakeAccounts({special: info})
    result, _, _ = compute(full_list(rows), target=pct(2), accounts=accounts)

    assert result["wallet_lower_bound_raw"] == pct(1)
    assert result["reason"] == "target_not_reached"
    assert result["classified"] == {cb.WALLET: 1, category: 1}


def test_frozen_token_accounts_are_subtracted():
    rows = standard_rows(others=2)
    rows.append(("acc0-b", WALLETS[100], pct(0.5)))
    frozen = FakeFrozen({"acc0-b", "acc1"})
    result, accounts, _ = compute(full_list(rows), target=pct(1), frozen=frozen)

    # acc1 vollständig eingefroren -> nicht abgefragt
    assert queried(accounts) == [WALLETS[100]]
    assert result["wallet_lower_bound_raw"] == pct(1)
    assert result["frozen_accounts"] == 2
    assert result["frozen_amount_raw"] == pct(1.5)


@pytest.mark.parametrize("program", [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID])
def test_classic_and_token2022_use_their_program(program):
    result, _, frozen = compute(
        full_list(standard_rows(), program=program), target=pct(100)
    )
    assert result["status"] == cb.COMPLETED
    assert frozen.calls[0][1] == program


def test_lower_bound_never_exceeds_upper_bound():
    rows = standard_rows(others=60, other_pct=1.0)
    result, _, _ = compute(full_list(rows), target=pct(60))
    upper = SUPPLY  # keine Burns
    assert result["wallet_lower_bound_raw"] <= upper
    assert result["wallet_lower_bound_raw"] == pct(60)


# --------------------------------------------------
# Reihenfolge, früher Abbruch, Abruflimit
# --------------------------------------------------

def total(rows):
    """Summe der Kandidaten aus many_rows (ohne Top-10 und Creator)."""
    return sum(amount for address, _, amount in rows if address.startswith("m"))


def many_rows(count, amount_each):
    rows = standard_rows(others=0)
    rows += [(f"m{i}", WALLETS[20 + i], amount_each - i) for i in range(count)]
    return rows


def test_largest_authorities_are_queried_first_in_blocks_of_100():
    rows = many_rows(250, pct(0.1))
    result, accounts, _ = compute(full_list(rows), target=total(rows))

    assert [len(a) for a, _ in accounts.calls] == [100, 100, 50]
    first_block = accounts.calls[0][0]
    assert first_block == WALLETS[20:120]
    assert result["authorities_queried"] == 250
    assert result["wallet_lower_bound_raw"] == total(rows)
    assert result["reason"] == "target_reached"


def test_stops_early_when_target_reached():
    rows = many_rows(250, pct(0.1))
    target = pct(0.1) * 50  # nach dem ersten Block erreicht
    result, accounts, _ = compute(full_list(rows), target=target)

    assert len(accounts.calls) == 1
    assert result["target_reached"] is True
    assert result["reason"] == "target_reached"
    assert result["unqueried_candidates"] == 150


def test_call_limit_is_never_exceeded():
    rows = many_rows(350, pct(0.1))
    result, accounts, _ = compute(full_list(rows), target=total(rows), max_calls=2)

    assert len(accounts.calls) == 2
    assert result["account_calls"] == 2
    assert result["status"] == cb.COMPLETED
    assert result["target_reached"] is False
    assert result["reason"] == "call_limit_reached"
    assert result["unqueried_candidates"] == 150


def test_default_call_limit_is_ten():
    assert CONFIG["market_cap_onchain_bounds"]["max_account_calls_per_token"] == 10


def test_unreachable_target_needs_no_query():
    result, accounts, frozen = compute(full_list(standard_rows()), target=pct(41))

    assert accounts.calls == []
    assert result["account_calls"] == 0
    assert result["reason"] == "target_unreachable"
    assert result["target_reached"] is False
    assert len(frozen.calls) == 1


# --------------------------------------------------
# Slots und fehlerhafte Antworten
# --------------------------------------------------

def test_slot_going_backwards_is_error():
    rows = many_rows(250, pct(0.1))
    accounts = FakeAccounts(slots=[1020, 1019, 1030])
    result, _, _ = compute(full_list(rows), target=total(rows), accounts=accounts)

    assert result["status"] == cb.ERROR
    assert result["reason"] == "slot_inconsistent"
    assert result["wallet_lower_bound_raw"] is None


def test_frozen_slot_before_holder_list_is_error():
    result, _, _ = compute(
        full_list(standard_rows()), target=pct(100), frozen=FakeFrozen(slot=1009)
    )
    assert result["reason"] == "slot_inconsistent"


def test_slot_span_exceeded_is_error():
    accounts = FakeAccounts(slot=1151)
    result, _, _ = compute(full_list(standard_rows()), target=pct(40), accounts=accounts)

    assert result["status"] == cb.ERROR
    assert result["reason"] == "slot_span_exceeded"


def test_slot_span_exactly_at_limit_is_allowed():
    accounts = FakeAccounts(slot=1150)
    result, _, _ = compute(full_list(standard_rows()), target=pct(40), accounts=accounts)

    assert result["status"] == cb.COMPLETED
    assert result["slot_span"] == 150


@pytest.mark.parametrize(
    "response, reason",
    [
        (None, "authority_response_invalid"),
        ({"slot": 1020}, "authority_response_invalid"),
        ({"slot": 1020, "accounts": []}, "authority_response_invalid"),
        ({"accounts": [None] * 40}, "slot_inconsistent"),
        ({"slot": 1020, "accounts": ["x"] * 40}, "authority_account_invalid"),
        ({"slot": 1020, "accounts": [{"owner": SYSTEM_PROGRAM_ID}] * 40}, "authority_account_invalid"),
    ],
)
def test_bad_account_responses_are_errors(response, reason):
    result, _, _ = compute(
        full_list(standard_rows()),
        target=pct(40),
        accounts=lambda addresses, *, min_slot: response,
    )
    assert result["status"] == cb.ERROR
    assert result["reason"] == reason


@pytest.mark.parametrize(
    "fetch",
    ["accounts", "frozen"],
)
def test_rpc_errors_are_errors_without_partial_result(fetch):
    def failing(*args, **kwargs):
        raise OnchainBoundsError("rpc_error")

    kwargs = {fetch: failing}
    result, _, _ = compute(full_list(standard_rows()), target=pct(40), **kwargs)

    assert result["status"] == cb.ERROR
    assert result["reason"] == "rpc_error"
    assert result["wallet_lower_bound_raw"] is None


@pytest.mark.parametrize(
    "frozen_result",
    [None, {"slot": 1015}, {"slot": 1015, "addresses": "acc1"}],
)
def test_bad_frozen_response_is_error(frozen_result):
    result, _, _ = compute(
        full_list(standard_rows()),
        target=pct(100),
        frozen=lambda *args, **kwargs: frozen_result,
    )
    assert result["reason"] == "frozen_response_invalid"


def _mutate(change):
    snap = full_list(standard_rows())
    change(snap)
    return snap


@pytest.mark.parametrize(
    "snapshot, sec, reason",
    [
        (None, None, "holder_list_missing"),
        (_mutate(lambda s: s.update(mint="OTHER")), None, "holder_list_mismatch"),
        (_mutate(lambda s: s.update(data_scope="largest_20_token_accounts_sample")), None, "full_holder_list_required"),
        (_mutate(lambda s: s.update(sample_owner_mapping_complete=False)), None, "owner_mapping_incomplete"),
        (_mutate(lambda s: s.update(token_program="Other")), None, "unsupported_token_program"),
        (_mutate(lambda s: s.pop("slot_supply_after")), None, "holder_list_slot_missing"),
        (_mutate(lambda s: s.update(supply_amount_raw=0)), None, "holder_list_incomplete"),
        (_mutate(lambda s: s["accounts"].append({"address": "x"})), None, "invalid_account_rows"),
        (_mutate(lambda s: s["accounts"].pop()), None, "full_list_incomplete"),
        (full_list(standard_rows()), security(complete=False), "pool_labels_incomplete"),
        (full_list(standard_rows()), {"mint": MINT}, "pool_labels_incomplete"),
    ],
)
def test_invalid_input_is_error_without_queries(snapshot, sec, reason):
    result, accounts, frozen = compute(snapshot, target=pct(1), sec=sec)

    assert result["status"] == cb.ERROR
    assert result["reason"] == reason
    assert accounts.calls == []
    assert frozen.calls == []


def test_invalid_authority_address_is_error():
    rows = standard_rows(others=1)
    rows.append(("bad", "not-base58-0OIl", pct(1)))
    result, _, _ = compute(full_list(rows), target=pct(100))
    assert result["reason"] == "invalid_authority_address"


@pytest.mark.parametrize("target, calls", [(0, 10), (None, 10), (5, 0), (5, None)])
def test_invalid_target_or_call_limit(target, calls):
    result, accounts, _ = compute(
        full_list(standard_rows()), target=target, max_calls=calls
    )
    assert result["status"] == cb.ERROR
    assert accounts.calls == []


# --------------------------------------------------
# Marktkapitalisierung: Entscheidung aus der Spanne
# --------------------------------------------------

def bounds(lower_tokens, upper_tokens=1_000_000_000, low=0.001, high=0.001):
    return evaluate_onchain_bounds(
        config=CONFIG,
        price_low_usd=low,
        price_high_usd=high,
        lower_amount_raw=lower_tokens * 10 ** DECIMALS,
        upper_amount_raw=upper_tokens * 10 ** DECIMALS,
        decimals=DECIMALS,
    )


def test_bounds_confirm_inside_range():
    result = bounds(400_000_000)  # 400k $ .. 1 Mio. $
    assert result["status"] == "confirmed"
    assert result["reason"] == "onchain_bounds_in_range"
    assert result["basis"] == "onchain_bounds"
    assert result["market_cap_lower_bound_usd"] == pytest.approx(400_000)
    assert result["market_cap_upper_bound_usd"] == pytest.approx(1_000_000)
    assert "market_cap_usd" not in result


def test_bounds_lower_exactly_at_minimum_confirms():
    assert bounds(300_000_000)["status"] == "confirmed"


def test_bounds_lower_just_below_minimum_is_unclear():
    result = evaluate_onchain_bounds(
        config=CONFIG,
        price_low_usd=0.001,
        price_high_usd=0.001,
        lower_amount_raw=300_000_000 * 10 ** DECIMALS - 1,
        upper_amount_raw=10 ** 15,
        decimals=DECIMALS,
    )
    assert result["status"] == "unclear"
    assert result["reason"] == "onchain_lower_bound_below_minimum"


def test_bounds_upper_exactly_at_maximum_confirms():
    assert bounds(400_000_000, upper_tokens=20_000_000_000)["status"] == "confirmed"


def test_bounds_upper_just_above_maximum_is_unclear():
    result = evaluate_onchain_bounds(
        config=CONFIG,
        price_low_usd=0.001,
        price_high_usd=0.001,
        lower_amount_raw=400_000_000 * 10 ** DECIMALS,
        upper_amount_raw=20_000_000_000 * 10 ** DECIMALS + 1,
        decimals=DECIMALS,
    )
    assert result["status"] == "unclear"
    assert result["reason"] == "onchain_upper_bound_above_maximum"


def test_bounds_upper_below_minimum_rejects():
    result = bounds(0, upper_tokens=299_999_999)
    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_below_minimum"


def test_bounds_lower_above_maximum_rejects():
    result = bounds(20_000_000_001, upper_tokens=30_000_000_000)
    assert result["status"] == "rejected"
    assert result["reason"] == "market_cap_above_maximum_onchain_lower_bound"


def test_bounds_upper_reason_wins_when_both_bounds_miss():
    result = bounds(100_000_000, upper_tokens=30_000_000_000)
    assert result["status"] == "unclear"
    assert result["reason"] == "onchain_upper_bound_above_maximum"


def test_bounds_lower_exactly_at_maximum_does_not_reject():
    result = bounds(20_000_000_000, upper_tokens=30_000_000_000)
    assert result["status"] == "unclear"


def test_bounds_use_low_price_for_lower_and_high_price_for_upper():
    # Untergrenze nur mit niedrigem Preis über dem Minimum? 300 Mio. x
    # 0,00099 = 297k -> unklar, obwohl der hohe Preis 330k ergäbe.
    result = bounds(300_000_000, low=0.00099, high=0.0011)
    assert result["status"] == "unclear"
    assert result["market_cap_lower_bound_usd"] == pytest.approx(297_000)
    assert result["market_cap_upper_bound_usd"] == pytest.approx(1_100_000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_low_usd": None},
        {"price_low_usd": 0},
        {"price_low_usd": -1.0},
        {"price_low_usd": float("nan")},
        {"price_high_usd": float("inf")},
        {"price_low_usd": 0.002, "price_high_usd": 0.001},
        {"lower_amount_raw": -1},
        {"lower_amount_raw": 1.5},
        {"lower_amount_raw": True},
        {"upper_amount_raw": 0},
        {"decimals": 19},
        {"decimals": None},
    ],
)
def test_bounds_invalid_input_is_unclear(kwargs):
    params = {
        "config": CONFIG,
        "price_low_usd": 0.001,
        "price_high_usd": 0.001,
        "lower_amount_raw": 400_000_000 * 10 ** DECIMALS,
        "upper_amount_raw": 10 ** 15,
        "decimals": DECIMALS,
        **kwargs,
    }
    result = evaluate_onchain_bounds(**params)
    assert result["status"] == "unclear"
    assert result["reason"] == "onchain_bounds_input_invalid"


def test_bounds_lower_above_upper_is_unclear():
    result = bounds(2_000_000_000, upper_tokens=1_000_000_000)
    assert result["status"] == "unclear"
    assert result["reason"] == "onchain_lower_bound_exceeds_upper_bound"


def test_target_is_minimum_when_upper_bound_fits():
    target = onchain_target_amount_raw(
        config=CONFIG,
        price_low_usd=0.001,
        price_high_usd=0.001,
        upper_amount_raw=10 ** 15,
        decimals=DECIMALS,
    )
    assert target == 300_000_000 * 10 ** DECIMALS
    assert bounds(300_000_000)["status"] == "confirmed"


def test_target_proves_rejection_when_upper_bound_too_high():
    target = onchain_target_amount_raw(
        config=CONFIG,
        price_low_usd=0.001,
        price_high_usd=0.001,
        upper_amount_raw=30_000_000_000 * 10 ** DECIMALS,
        decimals=DECIMALS,
    )
    assert target == 20_000_000_000 * 10 ** DECIMALS + 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_low_usd": 0},
        {"price_low_usd": 0.002},
        {"upper_amount_raw": 0},
        {"decimals": -1},
    ],
)
def test_target_invalid_input(kwargs):
    params = {
        "config": CONFIG,
        "price_low_usd": 0.001,
        "price_high_usd": 0.001,
        "upper_amount_raw": 10 ** 15,
        "decimals": DECIMALS,
        **kwargs,
    }
    assert onchain_target_amount_raw(**params) is None


def test_config_switch_is_off_by_default():
    assert CONFIG["market_cap_onchain_bounds"]["allow_onchain_bounds"] is False


def test_config_copy_is_independent():
    config = copy.deepcopy(CONFIG)
    config["market_cap_onchain_bounds"]["allow_onchain_bounds"] = True
    assert CONFIG["market_cap_onchain_bounds"]["allow_onchain_bounds"] is False
