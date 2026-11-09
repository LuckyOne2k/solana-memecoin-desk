import pytest

from desk.token_classification import (
    MEMECOIN_CANDIDATE,
    STABLECOIN,
    STAKING_TOKEN,
    STABLECOIN_MINTS,
    STAKING_TOKEN_MINTS,
    UNCLEAR,
    classify_token,
)


@pytest.mark.parametrize("mint", sorted(STABLECOIN_MINTS))
def test_known_stablecoins_are_detected(mint):
    assert classify_token(mint)[0] == STABLECOIN


@pytest.mark.parametrize("mint", sorted(STAKING_TOKEN_MINTS))
def test_known_staking_tokens_are_detected(mint):
    assert classify_token(mint)[0] == STAKING_TOKEN


def test_lists_do_not_overlap():
    assert not set(STABLECOIN_MINTS) & set(STAKING_TOKEN_MINTS)


@pytest.mark.parametrize(
    "symbol, name",
    [
        ("USDC", "Totally USD Coin"),
        ("usdt", "Tether"),
        ("JitoSOL", "Jito"),
        ("USDP", "USDP"),  # echter Fall: pump.fun-Token mit Stablecoin-Symbol
        ("XYZ", "Super Staked SOL"),
        ("ABC", "The best stablecoin"),
    ],
)
def test_lookalikes_with_other_address_are_unclear(symbol, name):
    category, detail = classify_token("SomeOtherMint111", symbol, name)

    assert category == UNCLEAR
    assert detail


@pytest.mark.parametrize(
    "symbol, name",
    [
        ("HABIBI", "Habibi"),
        ("USPR", "USPR"),  # ähnlich, aber kein geschütztes Symbol
        ("SOLCAT", "Sol Cat"),
        (None, None),
        ("", ""),
    ],
)
def test_ordinary_tokens_are_memecoin_candidates(symbol, name):
    assert classify_token("SomeMint", symbol, name)[0] == MEMECOIN_CANDIDATE
