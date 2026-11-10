"""Ablehnungsregeln für Token-2022-Erweiterungen, Gegenprobe der
Mint-/Freeze-Authority (RPC gegen RugCheck) und zusätzliche
Protokollfelder.

Grundsatz: Ablehnung nur bei eindeutig belegtem Risiko. Fehlende, falsch
typisierte oder unbekannte Daten -> unklar, nie Freigabe. Kein Netzwerk.
"""

from unittest.mock import patch

import pytest

from desk import __main__ as app
from desk import solana_rpc
from desk.security_scan import (
    EXTENSION_REJECT,
    EXTENSION_SAFE,
    EXTENSION_UNCLEAR,
    SAFE_TOKEN_2022_EXTENSIONS,
    classify_token_extension,
)
from desk.solana_rpc import (
    KNOWN_TOKEN_2022_EXTENSIONS,
    TOKEN_2022_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
)
from factories import make_full_list, make_mint_info, make_security
from test_security_scan_full_list import MINT, first, make_pool, scan


ADDR = "2apBGMsS6ti9RyF5TwQTDswXBWskiJP2LD4cUEDqYJjk"


@pytest.fixture(autouse=True)
def no_network():
    with patch(
        "desk.solana_rpc.requests.post",
        side_effect=AssertionError("network access in test"),
    ):
        yield


# --------------------------------------------------
# classify_token_extension
# --------------------------------------------------

REJECT_CASES = [
    ("nonTransferable", {}, "non_transferable"),
    ("nonTransferable", None, "non_transferable"),
    ("permanentDelegate", {"delegate": ADDR}, "permanent_delegate_active"),
    ("pausableConfig", {"authority": ADDR, "paused": False}, "pausable_authority_active"),
    ("pausableConfig", {"authority": None, "paused": True}, "pausable_paused"),
    ("defaultAccountState", {"accountState": "frozen"}, "default_account_state_frozen"),
    ("transferHook", {"authority": None, "programId": ADDR}, "transfer_hook_program_active"),
    ("transferHook", {"authority": ADDR, "programId": None}, "transfer_hook_authority_active"),
    ("transferHook", {"programId": ADDR}, "transfer_hook_program_active"),
    (
        "transferFeeConfig",
        {
            "transferFeeConfigAuthority": ADDR,
            "olderTransferFee": {"transferFeeBasisPoints": 0},
            "newerTransferFee": {"transferFeeBasisPoints": 0},
        },
        "transfer_fee_authority_active",
    ),
    (
        "transferFeeConfig",
        {
            "transferFeeConfigAuthority": None,
            "olderTransferFee": {"transferFeeBasisPoints": 0},
            "newerTransferFee": {"transferFeeBasisPoints": 25},
        },
        "transfer_fee_nonzero",
    ),
    (
        "transferFeeConfig",
        {
            "transferFeeConfigAuthority": None,
            "olderTransferFee": {"transferFeeBasisPoints": 100},
            "newerTransferFee": {"transferFeeBasisPoints": 0},
        },
        "transfer_fee_nonzero",
    ),
]


@pytest.mark.parametrize("name, state, reason", REJECT_CASES)
def test_proven_risk_is_rejected(name, state, reason):
    assert classify_token_extension(name, state) == (EXTENSION_REJECT, reason)


UNCLEAR_CASES = [
    # Kategorie 2: nicht aktiv, aber nicht ausdrücklich freigegeben
    ("permanentDelegate", {"delegate": None}),
    ("pausableConfig", {"authority": None, "paused": False}),
    ("defaultAccountState", {"accountState": "initialized"}),
    ("transferHook", {"authority": None, "programId": None}),
    (
        "transferFeeConfig",
        {
            "transferFeeConfigAuthority": None,
            "olderTransferFee": {"transferFeeBasisPoints": 0},
            "newerTransferFee": {"transferFeeBasisPoints": 0},
        },
    ),
    ("mintCloseAuthority", {"closeAuthority": ADDR}),
    ("confidentialTransferMint", {"authority": None}),
    ("interestBearingConfig", {}),
    ("scaledUiAmountConfig", {}),
    # Konto-Erweiterungen dürfen auf einem Mint nicht vorkommen
    ("immutableOwner", {}),
    ("transferFeeAmount", {}),
    # unbekannt / nicht lesbar
    ("brandNewExtension", {"anything": True}),
    ("unparseableExtension", None),
    # fehlende oder falsch typisierte Felder
    ("permanentDelegate", None),
    ("permanentDelegate", []),
    ("permanentDelegate", {}),
    ("permanentDelegate", {"delegate": ""}),
    ("permanentDelegate", {"delegate": 123}),
    ("pausableConfig", {}),
    ("pausableConfig", {"authority": None, "paused": "true"}),
    ("defaultAccountState", {}),
    ("defaultAccountState", {"accountState": "Frozen"}),
    ("transferHook", {}),
    ("transferHook", {"programId": 5, "authority": False}),
    ("transferFeeConfig", {}),
    ("transferFeeConfig", "fee"),
    (
        "transferFeeConfig",
        {
            "transferFeeConfigAuthority": None,
            "olderTransferFee": {"transferFeeBasisPoints": "25"},
            "newerTransferFee": {"transferFeeBasisPoints": True},
        },
    ),
]


@pytest.mark.parametrize("name, state", UNCLEAR_CASES)
def test_uncertain_or_not_classified_stays_unclear(name, state):
    decision, _ = classify_token_extension(name, state)

    assert decision == EXTENSION_UNCLEAR


@pytest.mark.parametrize("name", sorted(SAFE_TOKEN_2022_EXTENSIONS))
def test_only_metadata_and_group_are_safe(name):
    assert classify_token_extension(name, {})[0] == EXTENSION_SAFE


def test_no_other_known_extension_is_safe():
    for name in KNOWN_TOKEN_2022_EXTENSIONS - SAFE_TOKEN_2022_EXTENSIONS:
        for state in ({}, None, {"authority": None, "delegate": None}):
            assert classify_token_extension(name, state)[0] != EXTENSION_SAFE


# --------------------------------------------------
# Einbindung in die Prüfkette
# --------------------------------------------------

def token2022_info(states, **kwargs):
    return make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=[entry["extension"] for entry in states],
        extension_states=states,
        **kwargs,
    )


def full_list():
    return make_full_list(mint=MINT, token_program=TOKEN_2022_PROGRAM_ID)


@pytest.mark.parametrize("name, state, reason", REJECT_CASES)
def test_risky_extension_rejects_pool_and_skips_holder_queries(name, state, reason):
    states = [
        {"extension": "metadataPointer", "state": {}},
        {"extension": name, "state": state},
    ]

    result, mocks = scan(mint_info=token2022_info(states), full_list=full_list())
    item = first(result)

    assert item["status"] == "rejected"
    assert f"token_extension_{reason}" in item["rejection_reasons"]
    assert item["holder_check_status"] == "skipped_token_rejection"
    assert {
        "extension": name,
        "decision": "reject",
        "reason": reason,
    } in item["token_extension_findings"]
    # Keine Holder-Liste, keine Stichprobe nach der Ablehnung
    mocks[2].assert_not_called()
    mocks[4].assert_not_called()


@pytest.mark.parametrize("name, state", UNCLEAR_CASES)
def test_unclear_extension_never_rejects_and_never_ready(name, state):
    states = [{"extension": name, "state": state}]

    result, _ = scan(mint_info=token2022_info(states), full_list=full_list())
    item = first(result)

    assert item["rejection_reasons"] == []
    assert "token_extensions_safe" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_safe_extensions_with_states_stay_ready():
    states = [
        {"extension": "metadataPointer", "state": {"authority": ADDR}},
        {"extension": "tokenMetadata", "state": {"updateAuthority": ADDR}},
    ]

    result, _ = scan(mint_info=token2022_info(states), full_list=full_list())
    item = first(result)

    assert item["missing_checks"] == []
    assert "token_extension_findings" not in item
    assert item["status"] == "ready_for_full_risk_screen"


@pytest.mark.parametrize(
    "states",
    [
        None,
        [],
        [{"extension": "permanentDelegate", "state": {"delegate": ADDR}}],
        "broken",
    ],
)
def test_states_not_matching_names_cannot_reject_or_confirm(states):
    info = make_mint_info(
        mint=MINT,
        token_program=TOKEN_2022_PROGRAM_ID,
        extensions=["transferHook", "permanentDelegate"],
        extension_states=states,
        delegate_status="present",
    )

    result, _ = scan(mint_info=info, full_list=full_list())
    item = first(result)

    assert item["rejection_reasons"] == []
    assert "token_extensions_safe" in item["missing_checks"]
    assert "permanent_delegate_absent" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_pyusd_like_mint_is_rejected():
    # Form wie live am Mainnet gelesen (PYUSD, jsonParsed).
    states = [
        {"extension": "mintCloseAuthority", "state": {"closeAuthority": ADDR}},
        {"extension": "permanentDelegate", "state": {"delegate": ADDR}},
        {
            "extension": "transferFeeConfig",
            "state": {
                "newerTransferFee": {"epoch": 605, "maximumFee": 0, "transferFeeBasisPoints": 0},
                "olderTransferFee": {"epoch": 605, "maximumFee": 0, "transferFeeBasisPoints": 0},
                "transferFeeConfigAuthority": ADDR,
                "withdrawWithheldAuthority": ADDR,
                "withheldAmount": 0,
            },
        },
        {"extension": "transferHook", "state": {"authority": ADDR, "programId": None}},
        {"extension": "metadataPointer", "state": {"authority": ADDR}},
    ]

    result, _ = scan(
        mint_info=token2022_info(states, delegate_status="present", delegate=ADDR),
        full_list=full_list(),
    )
    item = first(result)

    assert item["status"] == "rejected"
    assert set(item["rejection_reasons"]) == {
        "token_extension_permanent_delegate_active",
        "token_extension_transfer_fee_authority_active",
        "token_extension_transfer_hook_authority_active",
    }


# --------------------------------------------------
# Gegenprobe Mint-/Freeze-Authority
# --------------------------------------------------

def authority_scan(onchain_mint, onchain_freeze, rug_mint=True, rug_freeze=True):
    info = make_mint_info(
        mint=MINT,
        mint_authority_onchain=onchain_mint,
        freeze_authority_onchain=onchain_freeze,
    )
    security = make_security(mint=MINT, mint_revoked=rug_mint, freeze_revoked=rug_freeze)
    result, _ = scan(security=security, mint_info=info, full_list=make_full_list(mint=MINT))
    return first(result)


def test_agreeing_sources_resolve_cross_check():
    item = authority_scan(True, True)

    assert "authority_cross_check" not in item["missing_checks"]
    assert item["mint_authority_revoked_onchain"] is True
    assert item["freeze_authority_revoked_onchain"] is True
    assert item["status"] == "ready_for_full_risk_screen"


@pytest.mark.parametrize(
    "onchain_mint, onchain_freeze, reason",
    [
        (False, True, "mint_authority_sources_disagree"),
        (True, False, "freeze_authority_sources_disagree"),
        (None, True, "mint_authority_cross_check_data_missing"),
        (True, None, "freeze_authority_cross_check_data_missing"),
        ("yes", True, "mint_authority_cross_check_data_missing"),
    ],
)
def test_disagreement_or_missing_onchain_value_is_unclear(onchain_mint, onchain_freeze, reason):
    item = authority_scan(onchain_mint, onchain_freeze)

    assert "authority_cross_check" in item["missing_checks"]
    assert reason in item["token_check_reasons"]
    assert item["rejection_reasons"] == []
    assert item["status"] == "incomplete"


def test_missing_rugcheck_freeze_value_keeps_cross_check_open():
    item = authority_scan(True, True, rug_freeze=None)

    assert "authority_cross_check" in item["missing_checks"]
    assert "freeze_authority_cross_check_data_missing" in item["token_check_reasons"]
    assert item["status"] != "ready_for_full_risk_screen"


def test_mint_info_error_keeps_cross_check_open():
    result, _ = scan(
        mint_info=solana_rpc.HolderListError("mint_not_found"),
        full_list=make_full_list(mint=MINT),
    )
    item = first(result)

    assert "authority_cross_check" in item["missing_checks"]
    assert item["status"] == "incomplete"


def test_cross_check_is_mandatory_even_if_not_marked_open():
    pool = make_pool(missing=["top10_share_pct"])
    info = make_mint_info(mint=MINT, mint_authority_onchain=None)

    result, _ = scan(pools=[pool], mint_info=info, full_list=make_full_list(mint=MINT))
    item = first(result)

    assert item["missing_checks"] == ["authority_cross_check"]
    assert item["status"] == "incomplete"


# --------------------------------------------------
# fetch_mint_info: Authorities und Zustände aus jsonParsed
# --------------------------------------------------

def mint_account(program, info_extra, extensions=None):
    info = {"decimals": 6, "supply": "1000", **info_extra}
    if extensions is not None:
        info["extensions"] = extensions
    return {
        "context": {"slot": 900},
        "value": {
            "owner": program,
            "space": 82 if extensions is None else 300,
            "data": {"parsed": {"type": "mint", "info": info}},
        },
    }


@pytest.mark.parametrize(
    "fields, mint_revoked, freeze_revoked",
    [
        ({"mintAuthority": None, "freezeAuthority": None}, True, True),
        ({"mintAuthority": ADDR, "freezeAuthority": None}, False, True),
        ({"mintAuthority": None, "freezeAuthority": ADDR}, True, False),
        ({}, None, None),
        ({"mintAuthority": "", "freezeAuthority": "  "}, None, None),
        ({"mintAuthority": 0, "freezeAuthority": False}, None, None),
    ],
)
def test_fetch_mint_info_reads_authorities(fields, mint_revoked, freeze_revoked):
    with patch(
        "desk.solana_rpc._rpc_call",
        return_value=mint_account(TOKEN_PROGRAM_ID, fields),
    ):
        info = solana_rpc.fetch_mint_info(MINT, rpc_url="https://rpc.test.invalid")

    assert info["mint_authority_revoked_onchain"] is mint_revoked
    assert info["freeze_authority_revoked_onchain"] is freeze_revoked


def test_fetch_mint_info_passes_extension_states():
    extensions = [
        {"extension": "transferHook", "state": {"authority": ADDR, "programId": None}},
        {"extension": "tokenMetadata", "state": {"name": "X"}},
    ]

    with patch(
        "desk.solana_rpc._rpc_call",
        return_value=mint_account(TOKEN_2022_PROGRAM_ID, {}, extensions),
    ):
        info = solana_rpc.fetch_mint_info(MINT, rpc_url="https://rpc.test.invalid")

    assert info["token2022_extension_states"] == [
        {"extension": "transferHook", "state": {"authority": ADDR, "programId": None}},
        {"extension": "tokenMetadata", "state": {"name": "X"}},
    ]
    assert info["token2022_extensions"] == ["transferHook", "tokenMetadata"]


def test_fetch_mint_info_classic_token_has_no_states():
    with patch(
        "desk.solana_rpc._rpc_call",
        return_value=mint_account(TOKEN_PROGRAM_ID, {"mintAuthority": None}),
    ):
        info = solana_rpc.fetch_mint_info(MINT, rpc_url="https://rpc.test.invalid")

    assert info["token2022_extension_states"] == []


def test_fetch_mint_info_unreadable_extensions_have_no_states():
    with patch(
        "desk.solana_rpc._rpc_call",
        return_value=mint_account(TOKEN_2022_PROGRAM_ID, {}, extensions="broken"),
    ):
        info = solana_rpc.fetch_mint_info(MINT, rpc_url="https://rpc.test.invalid")

    assert info["token2022_extension_states"] is None
    assert info["extensions_readable"] is False


# --------------------------------------------------
# Protokoll: nur zusätzliche Felder, keine Bewertung
# --------------------------------------------------

def test_check_details_log_market_activity_and_rugcheck_risks():
    pool = {
        "pair_age_hours": 41.5,
        "volume_usd_24h": 123456.7,
        "transactions_24h": 890,
        "security": {
            "rugcheck_risks": [
                {
                    "name": "Low Liquidity",
                    "level": "warn",
                    "score": 100,
                    "value": "$1.2k",
                    "description": "lang " * 100,
                    "extra": {"nested": True},
                },
                "kein dict",
                {"name": "x" * 500, "score": True},
            ]
        },
        "token_extension_findings": [
            {"extension": "transferHook", "decision": "unclear", "reason": "r"}
        ],
        "mint_authority_revoked_onchain": True,
        "freeze_authority_revoked_onchain": False,
    }

    details = app._check_details(pool)

    assert details["pair_age_hours"] == 41.5
    assert details["volume_usd_24h"] == 123456.7
    assert details["transactions_24h"] == 890
    assert details["rugcheck_risks"] == [
        {"name": "Low Liquidity", "level": "warn", "score": 100, "value": "$1.2k"},
        {"name": "x" * 200},
    ]
    assert details["token"]["token_extension_findings"] == pool["token_extension_findings"]
    assert details["token"]["mint_authority_revoked_onchain"] is True
    assert details["token"]["freeze_authority_revoked_onchain"] is False


def test_rugcheck_risk_list_is_limited():
    pool = {"security": {"rugcheck_risks": [{"name": f"r{i}"} for i in range(50)]}}

    assert len(app._check_details(pool)["rugcheck_risks"]) == 30


@pytest.mark.parametrize("security", [None, {}, {"rugcheck_risks": "x"}])
def test_missing_rugcheck_risks_are_logged_as_none(security):
    pool = {} if security is None else {"security": security}

    assert app._check_details(pool)["rugcheck_risks"] is None
