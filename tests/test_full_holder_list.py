"""Vollständige Holder-Liste (PR A): nur gemockte RPC, kein Netzwerk."""

import base64
import struct
from unittest.mock import patch

import pytest

from desk import solana_rpc
from desk.solana_rpc import (
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    HolderListError,
    SolanaRpcError,
    _b58encode,
    fetch_full_holder_list,
    fetch_mint_info,
)

MINT = "MintTest1111111111111111111111111111111111"
SECRET_URL = "https://mainnet.helius-rpc.test/?api-key=GEHEIM-123456"


@pytest.fixture(autouse=True)
def rpc_url_env(monkeypatch):
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.test.invalid")


@pytest.fixture(autouse=True)
def no_network():
    """Jeder echte HTTP-Aufruf wäre ein Testfehler."""
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=AssertionError("network access in test"),
    ):
        yield


def _b58decode(text):
    alphabet = solana_rpc._B58_ALPHABET
    number = 0
    for char in text:
        number = number * 58 + alphabet.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\0" * pad + body


def owner_bytes(index):
    return bytes([7]) + index.to_bytes(31, "big")


def owner_name(index):
    return _b58encode(owner_bytes(index))


def account_item(address, owner_index, amount, program=None, raw=None):
    if raw is None:
        raw = owner_bytes(owner_index) + struct.pack("<Q", amount)
    account = {"data": [base64.b64encode(raw).decode(), "base64"]}
    if program is not None:
        account["owner"] = program
    return {"pubkey": address, "account": account}


def page(slot, items, key=None):
    value = {"accounts": items}
    if key is not None:
        value["paginationKey"] = key
    return {"context": {"slot": slot}, "value": value}


def supply(amount, slot, decimals=6):
    return {
        "context": {"slot": slot},
        "value": {"amount": str(amount), "decimals": decimals},
    }


def mint_info(program=TOKEN_2022_PROGRAM_ID, decimals=6):
    return {"mint": MINT, "token_program": program, "decimals": decimals}


class FakeRpc:
    """Liefert vorbereitete Antworten je Methode in Reihenfolge."""

    def __init__(self, supplies, pages):
        self.supplies = list(supplies)
        self.pages = list(pages)
        self.calls = []

    def __call__(self, method, params, rpc_url=None, timeout=20):
        self.calls.append((method, params))
        if method == "getTokenSupply":
            return self.supplies.pop(0)
        if method == "getProgramAccountsV2":
            return self.pages.pop(0)
        raise AssertionError(f"unexpected method {method}")


def run(fake, info=None, max_pages=30, max_slot_span=150):
    with patch("desk.solana_rpc._rpc_call", fake):
        return fetch_full_holder_list(
            MINT,
            info or mint_info(),
            max_pages=max_pages,
            max_slot_span=max_slot_span,
        )


def pages_for(count, per_page=2, start_slot=100, amount=5):
    pages = []
    for number in range(count):
        items = [
            account_item(f"acc-{number}-{i}", number * per_page + i, amount)
            for i in range(per_page)
        ]
        key = f"key-{number}" if number < count - 1 else None
        pages.append(page(start_slot + number, items, key))
    return pages


def reason(fake, **kwargs):
    with pytest.raises(HolderListError) as error:
        run(fake, **kwargs)
    return error.value.reason


# --- base58 -----------------------------------------------------------

def test_b58_known_vectors():
    assert _b58encode(b"\0" * 32) == "11111111111111111111111111111111"
    assert _b58encode(b"\0" * 31 + b"\1") == "11111111111111111111111111111112"


@pytest.mark.parametrize("address", [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID])
def test_b58_roundtrip_program_ids(address):
    raw = _b58decode(address)
    assert len(raw) == 32
    assert _b58encode(raw) == address


# --- Erfolgsfälle -----------------------------------------------------

def test_single_page_complete_list():
    items = [
        account_item("a1", 1, 600),
        account_item("a2", 2, 400),
        account_item("a3", 3, 0),
    ]
    fake = FakeRpc([supply(1000, 100), supply(1000, 102)], [page(101, items)])
    result = run(fake)

    assert result["data_scope"] == "full_token_account_list"
    assert result["accounts"] == [
        {"address": "a1", "owner": owner_name(1), "amount": 600},
        {"address": "a2", "owner": owner_name(2), "amount": 400},
    ]
    assert result["accounts_total"] == 3
    assert result["accounts_nonzero"] == 2
    assert result["supply_amount_raw"] == 1000
    assert result["supply_decimals"] == 6
    assert result["sample_owner_mapping_complete"] is True
    assert result["pages"] == 1
    assert result["page_slots"] == [101]
    assert result["slot_supply_before"] == 100
    assert result["slot_supply_after"] == 102
    assert result["slot_span"] == 2


def test_multiple_accounts_same_owner_kept_individually():
    items = [account_item("a1", 1, 300), account_item("a2", 1, 700)]
    fake = FakeRpc([supply(1000, 10), supply(1000, 11)], [page(10, items)])
    result = run(fake)
    assert [row["owner"] for row in result["accounts"]] == [owner_name(1)] * 2


def test_three_pages_follow_pagination_key():
    pages = pages_for(3)
    fake = FakeRpc([supply(30, 100), supply(30, 103)], pages)
    result = run(fake)

    assert result["pages"] == 3
    assert result["accounts_nonzero"] == 6
    v2_calls = [p for m, p in fake.calls if m == "getProgramAccountsV2"]
    assert "paginationKey" not in v2_calls[0][1]
    assert v2_calls[1][1]["paginationKey"] == "key-0"
    assert v2_calls[2][1]["paginationKey"] == "key-1"


def test_request_parameters_token2022_without_datasize():
    fake = FakeRpc([supply(5, 100), supply(5, 100)], [page(100, [account_item("a", 1, 5)])])
    run(fake)
    program, config = [p for m, p in fake.calls if m == "getProgramAccountsV2"][0]

    assert program == TOKEN_2022_PROGRAM_ID
    assert config["filters"] == [{"memcmp": {"offset": 0, "bytes": MINT}}]
    assert config["withContext"] is True
    assert config["commitment"] == "finalized"
    assert config["minContextSlot"] == 100
    assert config["limit"] == 1000
    assert config["dataSlice"] == {"offset": 32, "length": 40}
    assert config["encoding"] == "base64"


def test_request_parameters_classic_with_datasize():
    fake = FakeRpc([supply(5, 100), supply(5, 100)], [page(100, [account_item("a", 1, 5)])])
    run(fake, info=mint_info(program=TOKEN_PROGRAM_ID))
    program, config = [p for m, p in fake.calls if m == "getProgramAccountsV2"][0]

    assert program == TOKEN_PROGRAM_ID
    assert config["filters"] == [
        {"memcmp": {"offset": 0, "bytes": MINT}},
        {"dataSize": 165},
    ]


def test_supply_after_requested_at_last_page_slot():
    fake = FakeRpc([supply(20, 100), supply(20, 104)], pages_for(2, amount=5, start_slot=103))
    run(fake)
    supply_calls = [p for m, p in fake.calls if m == "getTokenSupply"]
    assert "minContextSlot" not in supply_calls[0][1]
    assert supply_calls[1][1]["minContextSlot"] == 104


# --- Grenzfälle Seiten 30/31 ------------------------------------------

def test_exactly_30_pages_ok():
    fake = FakeRpc([supply(300, 100), supply(300, 130)], pages_for(30, amount=5))
    result = run(fake)
    assert result["pages"] == 30


def test_31_pages_rejected_without_partial_result():
    pages = pages_for(31, amount=5)
    fake = FakeRpc([supply(310, 100), supply(310, 131)], pages)
    assert reason(fake) == "too_many_pages"
    # Seite 31 wird gar nicht mehr abgefragt.
    assert len([m for m, _ in fake.calls if m == "getProgramAccountsV2"]) == 30


def test_max_pages_parameter_respected():
    fake = FakeRpc([supply(20, 100)], pages_for(2, amount=5))
    assert reason(fake, max_pages=1) == "too_many_pages"


# --- Grenzfälle Slot-Spanne 150/151 -----------------------------------

def test_slot_span_150_ok():
    fake = FakeRpc([supply(5, 1000), supply(5, 1150)], [page(1000, [account_item("a", 1, 5)])])
    assert run(fake)["slot_span"] == 150


def test_slot_span_151_rejected():
    fake = FakeRpc([supply(5, 1000), supply(5, 1151)], [page(1000, [account_item("a", 1, 5)])])
    assert reason(fake) == "slot_span_exceeded"


# --- Slot-Prüfung je Seite --------------------------------------------

@pytest.mark.parametrize(
    "bad_page",
    [
        {"value": {"accounts": []}},
        {"context": {}, "value": {"accounts": []}},
        {"context": {"slot": None}, "value": {"accounts": []}},
        {"context": {"slot": "100"}, "value": {"accounts": []}},
        {"context": {"slot": True}, "value": {"accounts": []}},
        {"context": {"slot": -1}, "value": {"accounts": []}},
        {"accounts": [], "paginationKey": None},
        [],
        None,
    ],
)
def test_missing_or_invalid_page_slot(bad_page):
    fake = FakeRpc([supply(5, 100)], [bad_page])
    assert reason(fake) == "page_slot_missing"


def test_missing_slot_on_later_page():
    pages = pages_for(2, amount=5)
    del pages[1]["context"]
    fake = FakeRpc([supply(20, 100)], pages)
    assert reason(fake) == "page_slot_missing"


def test_page_slot_before_supply_slot():
    fake = FakeRpc([supply(5, 100)], [page(99, [account_item("a", 1, 5)])])
    assert reason(fake) == "page_slot_before_supply"


def test_page_slots_decreasing():
    pages = [
        page(105, [account_item("a", 1, 5)], key="k"),
        page(104, [account_item("b", 2, 5)]),
    ]
    fake = FakeRpc([supply(10, 100)], pages)
    assert reason(fake) == "page_slot_inconsistent"


def test_supply_after_slot_before_last_page():
    fake = FakeRpc([supply(5, 100), supply(5, 101)], [page(102, [account_item("a", 1, 5)])])
    assert reason(fake) == "supply_slot_inconsistent"


def test_supply_slot_missing():
    fake = FakeRpc([{"value": {"amount": "5", "decimals": 6}}], [])
    assert reason(fake) == "supply_slot_missing"


# --- Supply und Summe -------------------------------------------------

def test_supply_changed_between_queries():
    fake = FakeRpc([supply(5, 100), supply(6, 101)], [page(100, [account_item("a", 1, 5)])])
    assert reason(fake) == "supply_changed"


@pytest.mark.parametrize("amount", [4, 6])
def test_sum_mismatch(amount):
    fake = FakeRpc([supply(5, 100), supply(5, 101)], [page(100, [account_item("a", 1, amount)])])
    assert reason(fake) == "supply_sum_mismatch"


@pytest.mark.parametrize(
    "bad_supply",
    [
        {"context": {"slot": 1}, "value": {"amount": "0", "decimals": 6}},
        {"context": {"slot": 1}, "value": {"amount": "-5", "decimals": 6}},
        {"context": {"slot": 1}, "value": {"amount": 5, "decimals": 6}},
        {"context": {"slot": 1}, "value": {"amount": "5"}},
        {"context": {"slot": 1}, "value": None},
    ],
)
def test_invalid_supply(bad_supply):
    assert reason(FakeRpc([bad_supply], [])) == "supply_invalid"


def test_decimals_mismatch_with_mint_info():
    fake = FakeRpc([supply(5, 100, decimals=9)], [])
    assert reason(fake) == "decimals_mismatch"


# --- Kontodaten -------------------------------------------------------

@pytest.mark.parametrize(
    "item",
    [
        account_item("a", 1, 5, raw=b"\1" * 39),
        account_item("a", 1, 5, raw=b"\1" * 41),
        {"pubkey": "a", "account": {"data": ["!!!", "base64"]}},
        {"pubkey": "a", "account": {"data": ["AAAA", "base58"]}},
        {"pubkey": "a", "account": {"data": "AAAA"}},
        {"pubkey": "a", "account": {}},
        {"pubkey": "", "account": {"data": ["AAAA", "base64"]}},
        {"account": {"data": ["AAAA", "base64"]}},
        {"pubkey": "a"},
        account_item("a", 1, 5, program=TOKEN_PROGRAM_ID),
        "kein-dict",
    ],
)
def test_invalid_account_data(item):
    fake = FakeRpc([supply(5, 100)], [page(100, [item])])
    assert reason(fake) == "invalid_account_data"


def test_account_owner_matching_program_accepted():
    item = account_item("a", 1, 5, program=TOKEN_2022_PROGRAM_ID)
    fake = FakeRpc([supply(5, 100), supply(5, 100)], [page(100, [item])])
    assert run(fake)["accounts_nonzero"] == 1


def test_duplicate_account_across_pages():
    pages = [
        page(100, [account_item("a", 1, 5)], key="k"),
        page(100, [account_item("a", 1, 5)]),
    ]
    fake = FakeRpc([supply(10, 100)], pages)
    assert reason(fake) == "duplicate_account"


@pytest.mark.parametrize(
    "bad_page",
    [
        {"context": {"slot": 100}, "value": []},
        {"context": {"slot": 100}, "value": {}},
        {"context": {"slot": 100}, "value": {"accounts": None}},
        {"context": {"slot": 100}, "value": {"accounts": [], "paginationKey": ""}},
        {"context": {"slot": 100}, "value": {"accounts": [], "paginationKey": 5}},
    ],
)
def test_invalid_page_shape(bad_page):
    fake = FakeRpc([supply(5, 100)], [bad_page])
    assert reason(fake) == "page_response_invalid"


# --- Eingaben ---------------------------------------------------------

def test_mint_info_mismatch():
    fake = FakeRpc([], [])
    info = mint_info()
    info["mint"] = "Anderer"
    assert reason(fake, info=info) == "mint_info_mismatch"


def test_unsupported_program_in_mint_info():
    fake = FakeRpc([], [])
    assert reason(fake, info=mint_info(program="Fremd111")) == "unsupported_token_program"


@pytest.mark.parametrize("value", [0, -1, True, 1.5, None, "30"])
def test_limits_must_be_positive_int(value):
    with pytest.raises(ValueError):
        run(FakeRpc([], []), max_pages=value)
    with pytest.raises(ValueError):
        run(FakeRpc([], []), max_slot_span=value)


def test_holder_list_error_is_solana_rpc_error():
    assert issubclass(HolderListError, SolanaRpcError)


# --- RPC-Fehler ohne URL/Key ------------------------------------------

def test_rpc_failure_gives_fixed_reason_without_secret(monkeypatch):
    monkeypatch.setenv("SOLANA_RPC_URL", SECRET_URL)
    import requests

    def failing_post(url, **kwargs):
        raise requests.ConnectionError(f"failed for {url}")

    with patch("desk.solana_rpc.requests.post", side_effect=failing_post), patch(
        "desk.solana_rpc.time.sleep"
    ):
        with pytest.raises(HolderListError) as error:
            fetch_full_holder_list(MINT, mint_info(), max_pages=30, max_slot_span=150)

    assert error.value.reason == "rpc_error"
    text = str(error.value) + repr(error.value)
    assert "GEHEIM" not in text and "helius" not in text
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


# --- Mint-Info --------------------------------------------------------

def mint_response(program, info, space=82, slot=50, parsed_type="mint"):
    return {
        "context": {"slot": slot},
        "value": {
            "owner": program,
            "space": space,
            "data": {"parsed": {"type": parsed_type, "info": info}},
        },
    }


def get_mint_info(response):
    def fake(method, params, rpc_url=None, timeout=20):
        assert method == "getAccountInfo"
        assert params[1] == {"encoding": "jsonParsed", "commitment": "finalized"}
        return response

    with patch("desk.solana_rpc._rpc_call", fake):
        return fetch_mint_info(MINT)


def mint_reason(response):
    with pytest.raises(HolderListError) as error:
        get_mint_info(response)
    return error.value.reason


def test_mint_info_classic_token():
    info = get_mint_info(mint_response(TOKEN_PROGRAM_ID, {"decimals": 6}))
    assert info["token_program"] == TOKEN_PROGRAM_ID
    assert info["decimals"] == 6
    assert info["mint_info_slot"] == 50
    assert info["permanent_delegate_status"] == "absent"
    assert info["token2022_extensions"] == []
    assert info["extensions_readable"] is True


def test_mint_info_token2022_with_known_extensions():
    extensions = [
        {"extension": "metadataPointer", "state": {}},
        {"extension": "tokenMetadata", "state": {}},
    ]
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, "extensions": extensions}, space=500)
    )
    assert info["token_program"] == TOKEN_2022_PROGRAM_ID
    assert info["token2022_extensions"] == ["metadataPointer", "tokenMetadata"]
    assert info["unknown_extensions"] == []
    assert info["permanent_delegate_status"] == "absent"
    assert info["permanent_delegate"] is None


def test_mint_info_permanent_delegate_with_address():
    extensions = [
        {"extension": "permanentDelegate", "state": {"delegate": "Deleg111"}},
    ]
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, "extensions": extensions}, space=200)
    )
    assert info["permanent_delegate_status"] == "present"
    assert info["permanent_delegate"] == "Deleg111"
    assert "permanentDelegate" in info["token2022_extensions"]


@pytest.mark.parametrize("state", [{}, {"delegate": None}, None, {"delegate": ""}])
def test_mint_info_permanent_delegate_without_address_still_present(state):
    extensions = [{"extension": "permanentDelegate", "state": state}]
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, "extensions": extensions}, space=200)
    )
    assert info["permanent_delegate_status"] == "present"
    assert info["permanent_delegate"] is None


def test_mint_info_unknown_extension_recorded():
    extensions = [{"extension": "brandNewExtension", "state": {}}]
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, "extensions": extensions}, space=200)
    )
    assert info["unknown_extensions"] == ["brandNewExtension"]
    assert info["permanent_delegate_status"] == "absent"


def test_mint_info_unparseable_extension_makes_delegate_unknown():
    extensions = [{"extension": "unparseableExtension"}]
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, "extensions": extensions}, space=200)
    )
    assert info["permanent_delegate_status"] == "unknown"
    assert "unparseableExtension" in info["unknown_extensions"]


def test_mint_info_token2022_base_size_without_extensions():
    info = get_mint_info(mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6}, space=82))
    assert info["permanent_delegate_status"] == "absent"
    assert info["token2022_extensions"] == []


@pytest.mark.parametrize(
    "info_extra, space",
    [
        ({}, 300),
        ({}, None),
        ({"extensions": "kaputt"}, 300),
        ({"extensions": [{"state": {}}]}, 300),
        ({"extensions": ["permanentDelegate"]}, 300),
    ],
)
def test_mint_info_unreadable_extensions_unknown(info_extra, space):
    info = get_mint_info(
        mint_response(TOKEN_2022_PROGRAM_ID, {"decimals": 6, **info_extra}, space=space)
    )
    assert info["permanent_delegate_status"] == "unknown"
    assert info["extensions_readable"] is False
    assert info["token2022_extensions"] is None


def test_mint_info_unsupported_program():
    assert mint_reason(mint_response("Fremd111", {"decimals": 6})) == "unsupported_token_program"


def test_mint_info_not_found():
    assert mint_reason({"context": {"slot": 1}, "value": None}) == "mint_not_found"


def test_mint_info_slot_missing():
    assert mint_reason({"value": None}) == "mint_slot_missing"


@pytest.mark.parametrize(
    "response",
    [
        mint_response(TOKEN_PROGRAM_ID, {"decimals": 6}, parsed_type="account"),
        mint_response(TOKEN_PROGRAM_ID, {"decimals": "6"}),
        mint_response(TOKEN_PROGRAM_ID, {}),
        mint_response(TOKEN_PROGRAM_ID, None),
        {"context": {"slot": 1}, "value": {"owner": TOKEN_PROGRAM_ID, "data": ["AAAA", "base64"]}},
    ],
)
def test_mint_info_invalid_data(response):
    assert mint_reason(response) == "mint_data_invalid"


def test_mint_info_requires_mint():
    with pytest.raises(ValueError):
        fetch_mint_info("")


# --- Ende-zu-Ende: Mint-Info + Liste ----------------------------------

def test_mint_info_output_feeds_full_list():
    info = get_mint_info(mint_response(TOKEN_PROGRAM_ID, {"decimals": 6}))
    fake = FakeRpc([supply(5, 100), supply(5, 100)], [page(100, [account_item("a", 1, 5)])])
    result = run(fake, info=info)
    assert result["token_program"] == TOKEN_PROGRAM_ID
