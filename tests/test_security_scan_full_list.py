"""Einbindung der vollständigen Holder-Liste in die Prüfkette (PR B).

Kein Netzwerk: Entweder werden die Abruffunktionen ersetzt, oder die
echten Funktionen laufen gegen eine gemockte RPC (_rpc_call). Ein echter
HTTP-Aufruf wäre ein Testfehler.
"""

import base64
import copy
import json
import struct
from unittest.mock import patch

import pytest
import requests

from desk import solana_rpc
from desk.config import load_config
from desk.security_scan import SAFE_TOKEN_2022_EXTENSIONS, scan_pool_security
from desk.solana_rpc import (
    KNOWN_TOKEN_2022_EXTENSIONS,
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    HolderListError,
    SolanaRpcError,
    _b58encode,
)
from factories import (
    POOL_ADDRESS,
    POOL_VAULT,
    make_full_list,
    make_labels,
    make_mint_info,
    make_security,
    make_snapshot,
)


CONFIG = load_config()
MINT = "TOKEN123"
SECRET_URL = "https://mainnet.helius-rpc.test/?api-key=GEHEIM-123456"

# Alle Prüfungen, die die Holder-/Mint-Stufe klären kann. Andere
# Prüfungen sind hier bereits erledigt, damit "ready" erreichbar wäre.
CHECKS = [
    "top10_share_pct",
    "dev_share_pct",
    "permanent_delegate_absent",
    "token_extensions_safe",
]

FULL_LIST_ERRORS = [
    "rpc_error",
    "mint_info_mismatch",
    "unsupported_token_program",
    "supply_slot_missing",
    "supply_invalid",
    "decimals_mismatch",
    "too_many_pages",
    "page_slot_missing",
    "page_response_invalid",
    "page_count_mismatch",
    "page_slot_before_supply",
    "page_slot_inconsistent",
    "invalid_account_data",
    "duplicate_account",
    "supply_slot_inconsistent",
    "slot_span_exceeded",
    "supply_changed",
    "supply_sum_mismatch",
]


@pytest.fixture(autouse=True)
def no_network():
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=AssertionError("network access in test"),
    ):
        yield


def make_pool(mint=MINT, missing=None):
    return {
        "token_address": mint,
        "pool_address": POOL_ADDRESS,
        "liquidity_usd": 50000.0,
        "status": "incomplete",
        "rejection_reasons": [],
        "missing_checks": list(CHECKS if missing is None else missing),
    }


def scan(
    pools=None,
    security=None,
    mint_info=None,
    full_list=None,
    sample=None,
    config=CONFIG,
):
    """mint_info/full_list/sample: Rückgabewert oder Exception.
    None bei mint_info/full_list -> Fehler aus conftest (rpc_error)."""

    def kwargs(value):
        if isinstance(value, BaseException):
            return {"side_effect": value}
        return {"return_value": value}

    patches = [
        patch(
            "desk.security_scan.prepare_pools",
            return_value=pools if pools is not None else [make_pool()],
        ),
        patch(
            "desk.security_scan.check_token_security",
            return_value=security or make_security(mint=MINT),
        ),
        patch(
            "desk.security_scan.fetch_holder_snapshot",
            **kwargs(sample if sample is not None else make_snapshot(mint=MINT)),
        ),
    ]

    if mint_info is not None:
        patches.append(
            patch("desk.security_scan.fetch_mint_info", **kwargs(mint_info))
        )

    if full_list is not None:
        patches.append(
            patch("desk.security_scan.fetch_full_holder_list", **kwargs(full_list))
        )

    mocks = [item.start() for item in patches]

    try:
        result = scan_pool_security([{}], config)
    finally:
        for item in patches:
            item.stop()

    return result, mocks


def first(result):
    return result["pools"][0]


# --------------------------------------------------
# Vollständige Liste: klassischer SPL-Token und Token-2022
# --------------------------------------------------

def test_classic_token_full_list_confirms_and_is_ready():
    result, mocks = scan(
        mint_info=make_mint_info(mint=MINT),
        full_list=make_full_list(mint=MINT),
    )
    item = first(result)

    assert item["holder_source"] == "full_holder_list"
    assert item["holder_evaluation"]["data_scope"] == "full_token_account_list"
    assert item["holder_evaluation"]["top10_status"] == "confirmed"
    assert item["holder_evaluation"]["dev_status"] == "confirmed"
    assert item["token_program"] == TOKEN_PROGRAM_ID
    assert item["token_check_reasons"] == []
    assert item["missing_checks"] == []
    assert item["status"] == "ready_for_full_risk_screen"
    mocks[2].assert_not_called()  # keine Stichprobe

    assert result["full_holder_list_succeeded"] == 1
    assert result["holder_fallbacks"] == 0


def test_token2022_with_metadata_extensions_only_is_ready():
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["metadataPointer", "tokenMetadata"],
    )

    result, _ = scan(
        mint_info=info,
        full_list=make_full_list(mint=MINT, token_program=TOKEN_2022_PROGRAM_ID),
    )
    item = first(result)

    assert item["token2022_extensions"] == ["metadataPointer", "tokenMetadata"]
    assert item["missing_checks"] == []
    assert item["status"] == "ready_for_full_risk_screen"


def test_full_list_rejection_is_applied():
    full = make_full_list(mint=MINT, holders=[(f"H{i}", 2.6) for i in range(10)])

    result, _ = scan(mint_info=make_mint_info(mint=MINT), full_list=full)
    item = first(result)

    assert "top10_holder_share_above_limit" in item["rejection_reasons"]
    assert item["status"] == "rejected"


def test_holder_limits_come_from_config():
    config = copy.deepcopy(CONFIG)
    config["holders"]["max_pages"] = 7
    config["holders"]["max_slot_span"] = 42

    result, mocks = scan(
        mint_info=make_mint_info(mint=MINT),
        full_list=make_full_list(mint=MINT),
        config=config,
    )

    mocks[4].assert_called_once_with(
        MINT,
        make_mint_info(mint=MINT),
        max_pages=7,
        max_slot_span=42,
    )


# --------------------------------------------------
# Token-2022: Permanent Delegate und Erweiterungen
# --------------------------------------------------

def test_permanent_delegate_keeps_token_incomplete_without_rejection():
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["metadataPointer", "permanentDelegate"],
        delegate_status="present",
        delegate="DELEGATE111",
    )

    result, _ = scan(
        mint_info=info,
        full_list=make_full_list(mint=MINT, token_program=TOKEN_2022_PROGRAM_ID),
    )
    item = first(result)

    # Holder sind bestätigt, trotzdem nie bereit.
    assert item["holder_evaluation"]["top10_status"] == "confirmed"
    assert "permanent_delegate_absent" in item["missing_checks"]
    assert "token_extensions_safe" in item["missing_checks"]
    assert "permanent_delegate_present" in item["token_check_reasons"]
    assert item["permanent_delegate"] == "DELEGATE111"
    assert item["permanent_delegate_status"] == "present"
    assert item["unsafe_token_extensions"] == ["permanentDelegate"]
    assert item["rejection_reasons"] == []
    assert item["status"] == "incomplete"


def test_permanent_delegate_without_address_is_still_present():
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["permanentDelegate"],
        delegate_status="present",
        delegate=None,
    )

    result, _ = scan(mint_info=info, full_list=make_full_list(
        mint=MINT, token_program=TOKEN_2022_PROGRAM_ID))
    item = first(result)

    assert "permanent_delegate_present" in item["token_check_reasons"]
    assert item["status"] == "incomplete"


def test_unknown_delegate_status_keeps_token_incomplete():
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["unparseableExtension"],
        delegate_status="unknown",
    )

    result, _ = scan(mint_info=info, full_list=make_full_list(
        mint=MINT, token_program=TOKEN_2022_PROGRAM_ID))
    item = first(result)

    assert "permanent_delegate_unknown" in item["token_check_reasons"]
    assert "permanent_delegate_absent" in item["missing_checks"]
    assert item["status"] == "incomplete"


RISKY_KNOWN = sorted(KNOWN_TOKEN_2022_EXTENSIONS - SAFE_TOKEN_2022_EXTENSIONS)


def test_safe_list_is_small_and_known():
    assert SAFE_TOKEN_2022_EXTENSIONS <= KNOWN_TOKEN_2022_EXTENSIONS
    for name in (
        "permanentDelegate",
        "transferHook",
        "transferFeeConfig",
        "pausableConfig",
        "defaultAccountState",
        "nonTransferable",
        "confidentialTransferMint",
        "mintCloseAuthority",
    ):
        assert name not in SAFE_TOKEN_2022_EXTENSIONS


@pytest.mark.parametrize("extension", RISKY_KNOWN + ["brandNewExtension"])
def test_not_classified_extension_keeps_token_incomplete(extension):
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["metadataPointer", extension],
    )

    result, _ = scan(mint_info=info, full_list=make_full_list(
        mint=MINT, token_program=TOKEN_2022_PROGRAM_ID))
    item = first(result)

    assert "token_extensions_safe" in item["missing_checks"]
    assert extension in item["unsafe_token_extensions"]
    assert "token_extension_not_classified_safe" in item["token_check_reasons"]
    assert item["status"] != "ready_for_full_risk_screen"


def test_unreadable_extensions_keep_token_incomplete():
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        readable=False,
        delegate_status="unknown",
    )

    result, _ = scan(mint_info=info, full_list=make_full_list(
        mint=MINT, token_program=TOKEN_2022_PROGRAM_ID))
    item = first(result)

    assert "token_extensions_unreadable" in item["token_check_reasons"]
    assert "token_extensions_safe" in item["missing_checks"]
    assert "permanent_delegate_absent" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_token_checks_are_added_even_if_not_marked_open():
    # Auch ohne Vormerkung in missing_checks bleiben sie Pflicht.
    pool = make_pool(missing=["top10_share_pct", "dev_share_pct"])
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["transferHook"],
    )

    result, _ = scan(pools=[pool], mint_info=info, full_list=make_full_list(
        mint=MINT, token_program=TOKEN_2022_PROGRAM_ID))
    item = first(result)

    assert item["missing_checks"] == ["token_extensions_safe"]
    assert item["status"] == "incomplete"


# --------------------------------------------------
# Rückfall auf die Stichprobe
# --------------------------------------------------

def test_mint_info_failure_uses_sample_and_skips_full_list():
    result, mocks = scan(
        mint_info=HolderListError("mint_not_found"),
        full_list=make_full_list(mint=MINT),
    )
    item = first(result)

    mocks[4].assert_not_called()
    assert item["mint_info_status"] == "error"
    assert item["mint_info_error"] == "mint_not_found"
    assert item["holder_source"] == "sample_fallback"
    assert item["full_holder_list_error"] == "mint_info_unavailable"
    assert "permanent_delegate_absent" in item["missing_checks"]
    assert "token_extensions_safe" in item["missing_checks"]
    assert item["status"] == "incomplete"
    assert result["mint_info_checks_failed"] == 1
    assert result["full_holder_list_attempted"] == 0


@pytest.mark.parametrize("reason", FULL_LIST_ERRORS)
def test_every_full_list_error_falls_back_to_sample(reason):
    result, mocks = scan(
        mint_info=make_mint_info(mint=MINT),
        full_list=HolderListError(reason),
    )
    item = first(result)

    mocks[2].assert_called_once()
    assert item["holder_source"] == "sample_fallback"
    assert item["full_holder_list_error"] == reason
    assert item["holder_evaluation"]["data_scope"] == "largest_token_accounts_sample"
    assert item["holder_evaluation"]["top10_status"] == "unclear"
    assert item["holder_evaluation"]["dev_status"] == "unclear"
    assert "sample_cannot_confirm" in item["holder_evaluation"]["unconfirmed_reasons"]
    assert item["status"] == "incomplete"
    assert result["full_holder_list_failed"] == 1
    assert result["holder_fallbacks"] == 1


def test_plain_rpc_error_falls_back_with_fixed_reason():
    result, _ = scan(
        mint_info=make_mint_info(mint=MINT),
        full_list=SolanaRpcError("Solana RPC request failed"),
    )

    assert first(result)["full_holder_list_error"] == "rpc_error"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "text",
        {**make_full_list(mint=MINT), "mint": "OTHER"},
        {**make_full_list(mint=MINT), "data_scope": "largest_20_token_accounts_sample"},
        {**make_full_list(mint=MINT), "token_program": TOKEN_2022_PROGRAM_ID},
        {**make_full_list(mint=MINT), "accounts": None},
    ],
)
def test_invalid_full_list_result_falls_back(bad):
    with patch("desk.security_scan.fetch_full_holder_list", return_value=bad):
        result, _ = scan(mint_info=make_mint_info(mint=MINT))
    item = first(result)

    assert item["holder_source"] == "sample_fallback"
    assert item["full_holder_list_error"] == "full_list_response_invalid"
    assert item["holder_evaluation"]["top10_status"] != "confirmed"


def test_sample_claiming_full_scope_still_cannot_confirm():
    # Selbst wenn die Stichprobe "vollständig" behauptet: Rückfall bleibt
    # Stichprobe.
    sample = {**make_snapshot(mint=MINT), "data_scope": "full_token_account_list"}

    result, _ = scan(mint_info=make_mint_info(mint=MINT), sample=sample)
    item = first(result)

    assert item["holder_snapshot"]["data_scope"] == "largest_20_token_accounts_sample"
    assert item["holder_evaluation"]["top10_status"] == "unclear"
    assert item["holder_evaluation"]["dev_status"] == "unclear"
    assert item["status"] == "incomplete"


@pytest.mark.parametrize("top10_pct", [0.5, 1.0, 2.0, 2.5])
@pytest.mark.parametrize("creator_pct", [0.0, 1.0, 3.0])
@pytest.mark.parametrize("full_error", ["mint_info", "too_many_pages", "slot_span_exceeded"])
def test_fallback_never_makes_token_ready(top10_pct, creator_pct, full_error):
    sample = make_snapshot(
        mint=MINT,
        holders=[(f"H{i}", top10_pct) for i in range(10)] + [("CREATOR", creator_pct)],
    )
    kwargs = (
        {"mint_info": HolderListError("rpc_error")}
        if full_error == "mint_info"
        else {
            "mint_info": make_mint_info(mint=MINT),
            "full_list": HolderListError(full_error),
        }
    )

    result, _ = scan(sample=sample, **kwargs)
    item = first(result)

    assert item["holder_evaluation"]["top10_status"] != "confirmed"
    assert item["holder_evaluation"]["dev_status"] != "confirmed"
    # Offen oder abgelehnt, aber nie durch die Stichprobe geklärt-bestätigt.
    if item["holder_evaluation"]["top10_status"] == "unclear":
        assert "top10_share_pct" in item["missing_checks"]
    else:
        assert "top10_holder_share_above_limit" in item["rejection_reasons"]
    assert item["status"] != "ready_for_full_risk_screen"


def test_fallback_can_reject():
    sample = make_snapshot(
        mint=MINT,
        holders=[(f"H{i}", 3.0) for i in range(10)],
        pool_pct=50.0,
        returned=20,
    )

    result, _ = scan(mint_info=make_mint_info(mint=MINT),
                     full_list=HolderListError("too_many_pages"), sample=sample)
    item = first(result)

    assert "top10_holder_share_above_limit" in item["rejection_reasons"]
    assert item["status"] == "rejected"


def test_fallback_failure_records_both_errors():
    result, _ = scan(
        mint_info=make_mint_info(mint=MINT),
        full_list=HolderListError("supply_changed"),
        sample=SolanaRpcError("No largest token accounts returned"),
    )
    item = first(result)

    assert item["holder_check_status"] == "error"
    assert item["full_holder_list_error"] == "supply_changed"
    assert item["holder_error"] == "No largest token accounts returned"
    assert item["status"] == "incomplete"
    assert result["holder_checks_failed"] == 1


# --------------------------------------------------
# Abfragen je Token nur einmal; keine Abfrage nach Ablehnung
# --------------------------------------------------

def test_mint_info_and_full_list_fetched_once_per_token():
    pools = [make_pool(), make_pool()]
    pools[1]["pool_address"] = "POOL2"

    result, mocks = scan(
        pools=pools,
        mint_info=make_mint_info(mint=MINT),
        full_list=make_full_list(mint=MINT),
    )

    assert mocks[3].call_count == 1
    assert mocks[4].call_count == 1
    assert result["full_holder_list_attempted"] == 1


def test_security_rejection_skips_mint_and_holder_queries():
    result, mocks = scan(
        security=make_security(mint=MINT, mint_revoked=False),
        mint_info=make_mint_info(mint=MINT),
        full_list=make_full_list(mint=MINT),
    )
    item = first(result)

    mocks[3].assert_not_called()
    mocks[4].assert_not_called()
    mocks[2].assert_not_called()
    assert item["mint_info_status"] == "skipped_security_rejection"
    assert item["status"] == "rejected"


# --------------------------------------------------
# End-to-End mit echten Abruffunktionen gegen gemockte RPC
# --------------------------------------------------

def owner(index):
    return _b58encode(bytes([9]) + index.to_bytes(31, "big"))


CREATOR = owner(999)
SUPPLY = 1_000_000


def account(address, owner_index, amount, program):
    raw = (bytes([9]) + owner_index.to_bytes(31, "big")) + struct.pack("<Q", amount)
    return {
        "pubkey": address,
        "account": {
            "data": [base64.b64encode(raw).decode(), "base64"],
            "owner": program,
            "space": 165,
        },
    }


def mint_response(program, extensions=None, slot=900):
    info = {"decimals": 6, "supply": str(SUPPLY)}
    if extensions is not None:
        info["extensions"] = extensions
    return {
        "context": {"slot": slot},
        "value": {
            "owner": program,
            "space": 82 if extensions is None else 300,
            "data": {"parsed": {"type": "mint", "info": info}},
        },
    }


def supply_response(amount, slot):
    return {"context": {"slot": slot}, "value": {"amount": str(amount), "decimals": 6}}


def page_response(slot, items, key):
    return {
        "context": {"apiVersion": "2.3.0", "slot": slot},
        "value": {"accounts": items, "count": len(items), "paginationKey": key},
    }


class FakeRpc:
    def __init__(self, mint, supplies, pages):
        self.mint = mint
        self.supplies = list(supplies)
        self.pages = list(pages)
        self.methods = []

    def __call__(self, method, params, rpc_url=None, timeout=20):
        self.methods.append(method)
        if method == "getAccountInfo":
            return self.mint
        if method == "getTokenSupply":
            return self.supplies.pop(0)
        if method == "getProgramAccountsV2":
            return self.pages.pop(0)
        raise AssertionError(f"unexpected method {method}")


def holder_pages(program, pages=2, slot=1000):
    """Pool 89 %, 10 Holder je 1 %, Creator 1 %, plus leere Konten;
    verteilt auf mehrere Seiten."""
    items = [account(POOL_VAULT, 0, 890_000, program)]
    items += [account(f"acc{i}", i + 1, 10_000, program) for i in range(10)]
    items += [account("acc-creator", 999, 10_000, program)]
    items += [account(f"empty{i}", 500 + i, 0, program) for i in range(3)]

    size = -(-len(items) // pages)
    chunks = [items[i:i + size] for i in range(0, len(items), size)]
    return [
        page_response(slot + n, chunk, f"k{n}" if n < len(chunks) - 1 else None)
        for n, chunk in enumerate(chunks)
    ]


def real_scan(fake, monkeypatch, sample=None):
    # Echte Funktionen statt der conftest-Fehler.
    monkeypatch.setattr("desk.security_scan.fetch_mint_info", solana_rpc.fetch_mint_info)
    monkeypatch.setattr(
        "desk.security_scan.fetch_full_holder_list", solana_rpc.fetch_full_holder_list
    )
    monkeypatch.setenv("SOLANA_RPC_URL", "https://rpc.test.invalid")
    security = make_security(
        mint=MINT,
        creator_address=CREATOR,
        labels=make_labels(),
    )

    with patch("desk.solana_rpc._rpc_call", fake):
        result, mocks = scan(security=security, sample=sample)

    return result, mocks


@pytest.mark.parametrize(
    "program, extensions",
    [
        (TOKEN_PROGRAM_ID, None),
        (TOKEN_2022_PROGRAM_ID, None),
        (
            TOKEN_2022_PROGRAM_ID,
            [{"extension": "metadataPointer", "state": {}},
             {"extension": "tokenMetadata", "state": {}}],
        ),
    ],
)
def test_end_to_end_full_list_confirms(program, extensions, monkeypatch):
    fake = FakeRpc(
        mint_response(program, extensions),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        holder_pages(program, pages=3),
    )

    result, mocks = real_scan(fake, monkeypatch)
    item = first(result)

    assert fake.methods.count("getProgramAccountsV2") == 3
    assert item["holder_source"] == "full_holder_list"
    assert item["holder_snapshot"]["pages"] == 3
    assert item["holder_snapshot"]["accounts_total"] == 15
    assert item["holder_snapshot"]["accounts_nonzero"] == 12
    assert item["holder_evaluation"]["top10_status"] == "confirmed"
    assert item["holder_evaluation"]["dev_status"] == "confirmed"
    assert item["holder_evaluation"]["dev_share_upper_bound_pct"] == pytest.approx(1)
    assert item["missing_checks"] == []
    assert item["status"] == "ready_for_full_risk_screen"
    mocks[2].assert_not_called()


def test_end_to_end_token2022_permanent_delegate(monkeypatch):
    extensions = [
        {"extension": "permanentDelegate", "state": {"delegate": "DELEGATE999"}},
    ]
    fake = FakeRpc(
        mint_response(TOKEN_2022_PROGRAM_ID, extensions),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        holder_pages(TOKEN_2022_PROGRAM_ID, pages=1),
    )

    result, _ = real_scan(fake, monkeypatch)
    item = first(result)

    assert item["holder_evaluation"]["top10_status"] == "confirmed"
    assert item["permanent_delegate"] == "DELEGATE999"
    assert item["token2022_extensions"] == ["permanentDelegate"]
    assert set(item["missing_checks"]) == {
        "permanent_delegate_absent",
        "token_extensions_safe",
    }
    assert item["rejection_reasons"] == []
    assert item["status"] == "incomplete"


def _expect_fallback(fake, monkeypatch, reason):
    result, mocks = real_scan(fake, monkeypatch)
    item = first(result)

    mocks[2].assert_called_once()
    assert item["holder_source"] == "sample_fallback"
    assert item["full_holder_list_error"] == reason
    assert item["holder_evaluation"]["top10_status"] != "confirmed"
    assert item["holder_evaluation"]["dev_status"] != "confirmed"
    assert item["status"] != "ready_for_full_risk_screen"


@pytest.mark.parametrize("pages, ok", [(30, True), (31, False)])
def test_end_to_end_page_limit(pages, ok, monkeypatch):
    items = [account(POOL_VAULT, 0, SUPPLY, TOKEN_PROGRAM_ID)]
    items += [account(f"e{i}", 100 + i, 0, TOKEN_PROGRAM_ID) for i in range(pages - 1)]
    page_list = [
        page_response(1000, [item], f"k{n}" if n < pages - 1 else None)
        for n, item in enumerate(items)
    ]
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        page_list,
    )

    if ok:
        result, _ = real_scan(fake, monkeypatch)
        assert first(result)["holder_source"] == "full_holder_list"
        assert first(result)["holder_snapshot"]["pages"] == 30
    else:
        _expect_fallback(fake, monkeypatch, "too_many_pages")
        # Die 31. Seite wird nicht abgefragt.
        assert fake.methods.count("getProgramAccountsV2") == 30


@pytest.mark.parametrize("span, ok", [(150, True), (151, False)])
def test_end_to_end_slot_span(span, ok, monkeypatch):
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1000 + span)],
        holder_pages(TOKEN_PROGRAM_ID, pages=1),
    )

    if ok:
        result, _ = real_scan(fake, monkeypatch)
        assert first(result)["holder_snapshot"]["slot_span"] == 150
        assert first(result)["holder_evaluation"]["top10_status"] == "confirmed"
    else:
        _expect_fallback(fake, monkeypatch, "slot_span_exceeded")


def test_end_to_end_supply_changed(monkeypatch):
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY + 1, 1010)],
        holder_pages(TOKEN_PROGRAM_ID, pages=1),
    )
    _expect_fallback(fake, monkeypatch, "supply_changed")


def test_end_to_end_sum_mismatch(monkeypatch):
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY + 5, 1000), supply_response(SUPPLY + 5, 1010)],
        holder_pages(TOKEN_PROGRAM_ID, pages=1),
    )
    _expect_fallback(fake, monkeypatch, "supply_sum_mismatch")


def test_end_to_end_duplicate_account(monkeypatch):
    pages = holder_pages(TOKEN_PROGRAM_ID, pages=2)
    pages[1]["value"]["accounts"].append(pages[0]["value"]["accounts"][0])
    pages[1]["value"]["count"] += 1
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        pages,
    )
    _expect_fallback(fake, monkeypatch, "duplicate_account")


def test_end_to_end_count_mismatch(monkeypatch):
    pages = holder_pages(TOKEN_PROGRAM_ID, pages=1)
    pages[0]["value"]["count"] = 99
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        pages,
    )
    _expect_fallback(fake, monkeypatch, "page_count_mismatch")


@pytest.mark.parametrize(
    "broken, reason",
    [
        ({"value": {"accounts": [], "paginationKey": None}}, "page_slot_missing"),
        ({"context": {"slot": "1000"}, "value": {"accounts": []}}, "page_slot_missing"),
        ({"context": {"slot": 1000}, "value": []}, "page_response_invalid"),
        ({"context": {"slot": 1000}, "value": {"accounts": None}}, "page_response_invalid"),
        (
            {"context": {"slot": 1000}, "value": {"accounts": [], "paginationKey": ""}},
            "page_response_invalid",
        ),
        ({"context": {"slot": 999}, "value": {"accounts": []}}, "page_slot_before_supply"),
    ],
)
def test_end_to_end_malformed_page(broken, reason, monkeypatch):
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        [broken],
    )
    _expect_fallback(fake, monkeypatch, reason)


def test_end_to_end_falling_page_slot(monkeypatch):
    pages = holder_pages(TOKEN_PROGRAM_ID, pages=2, slot=1005)
    pages[1]["context"]["slot"] = 1004
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        pages,
    )
    _expect_fallback(fake, monkeypatch, "page_slot_inconsistent")


def test_end_to_end_invalid_account_data(monkeypatch):
    pages = holder_pages(TOKEN_PROGRAM_ID, pages=1)
    pages[0]["value"]["accounts"][0]["account"]["data"] = ["@@@", "base64"]
    fake = FakeRpc(
        mint_response(TOKEN_PROGRAM_ID),
        [supply_response(SUPPLY, 1000), supply_response(SUPPLY, 1010)],
        pages,
    )
    _expect_fallback(fake, monkeypatch, "invalid_account_data")


def test_no_secret_url_in_scan_result(monkeypatch):
    """Netzwerkfehler mit Key in der Meldung: weder Fehlergrund noch
    Protokolldaten enthalten URL oder Key."""
    monkeypatch.setattr("desk.security_scan.fetch_mint_info", solana_rpc.fetch_mint_info)
    monkeypatch.setattr(
        "desk.security_scan.fetch_full_holder_list", solana_rpc.fetch_full_holder_list
    )
    monkeypatch.setattr(
        "desk.security_scan.fetch_holder_snapshot", solana_rpc.fetch_holder_snapshot
    )
    monkeypatch.setenv("SOLANA_RPC_URL", SECRET_URL)
    monkeypatch.setattr("desk.solana_rpc.time.sleep", lambda seconds: None)

    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=requests.ConnectionError(f"failed for {SECRET_URL}"),
    ), patch(
        "desk.security_scan.prepare_pools", return_value=[make_pool()]
    ), patch(
        "desk.security_scan.check_token_security",
        return_value=make_security(mint=MINT),
    ):
        result = scan_pool_security([{}], CONFIG)

    text = json.dumps(result)
    item = first(result)

    assert item["mint_info_error"] == "rpc_error"
    assert item["holder_check_status"] == "error"
    assert "GEHEIM" not in text
    assert "api-key" not in text
    assert "helius" not in text
