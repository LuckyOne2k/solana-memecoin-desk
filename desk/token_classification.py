"""Erkennt Token, die keine Memecoins sind (Stablecoins, Staking-Token).

Grundsatz: Nur exakte Mint-Adressen gelten als sicher erkannt. Die Liste
wurde am 09.11.2026 über GeckoTerminal (Name, Symbol, CoinGecko-ID)
geprüft. Ein Token, das nur Symbol oder Namen eines bekannten
Stablecoins/Staking-Tokens trägt, aber eine andere Adresse hat, wird
nicht ausgefiltert, sondern als "unklar" markiert (mögliche Nachahmung).
"""


# Mint-Adresse -> Symbol (zur Nachvollziehbarkeit)
STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA": "USDS",
    "DEkqHyPN7GMRJ5cArtQFAWefqbZb33Hyf6s5iCwjEonT": "USDe",
    "A1KLoBrKBde8Ty9qtNQUtq3C2ortoC3u7twggz7sEto6": "USDY",
    "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr": "EURC",
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB": "USD1",
    "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH": "USDG",
    "9zNQRsGLjNKwCUU5Gq5LR8beUCPzQMVMqKAi3SSZh54u": "FDUSD",
}

# Liquid-Staking- und Rendite-Token
STAKING_TOKEN_MINTS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "JitoSOL",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": "bSOL",
    "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm": "INF",
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v": "JupSOL",
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
    "he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A": "hSOL",
    "7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn": "JSOL",
    "Bybit2vBJGhPF52GBdNaQfUJ6ZpThSgHBobjWZpLPb4B": "bbSOL",
    "BNso1VUJnh4zcfpZa6986Ea66P6TCp59hvtNJ8b1X85": "BNSOL",
    "LAinEtNLgpmCP9Rvsf5Hn8W6EhNiKLZQti1xfWMLy6X": "laineSOL",
    "vSoLxydx6akxyMD9XEcPvGYNGq6Nn66oqVb3UkGkei7": "vSOL",
    "Dso1bDeDjCQxTrWHqUUi63oBvV7Mdm6WaobLbQ7gnPQ": "dSOL",
    "sctmB7GPi5L2Q5G9tUSzXvhZ4YiDMEGcRov9KfArQpx": "dfdvSOL",
    "6SSMUrBr9GEBsvQeHGKmC8eNXj4e6BAHBfBrycWi5AzL": "sUSN",
    "5Y8NV33Vv7WbnLfq3zBcKSdYPrk7g2KoiQoe7M2tcxp5": "ONyc",
}

# Symbole, die nur echte Stablecoins/Staking-Token tragen sollten.
# Ein fremdes Token mit diesem Symbol gilt als "unklar".
_PROTECTED_SYMBOLS = {
    symbol.upper()
    for symbol in (
        list(STABLECOIN_MINTS.values())
        + list(STAKING_TOKEN_MINTS.values())
        + ["DAI", "PYUSD", "USDP", "TUSD", "USDH", "UXD", "WSOL"]
    )
}

# Namensbestandteile, die auf Staking oder Stablecoins hinweisen.
_SUSPICIOUS_NAME_PARTS = (
    "staked sol",
    "stake pool",
    "liquid staking",
    "stablecoin",
)

MEMECOIN_CANDIDATE = "memecoin_candidate"
STABLECOIN = "stablecoin"
STAKING_TOKEN = "staking_token"
UNCLEAR = "unclear"


def classify_token(mint, symbol=None, name=None):
    """Gibt (Kategorie, Begründung) zurück."""
    if mint in STABLECOIN_MINTS:
        return STABLECOIN, f"known_stablecoin_mint:{STABLECOIN_MINTS[mint]}"

    if mint in STAKING_TOKEN_MINTS:
        return STAKING_TOKEN, f"known_staking_mint:{STAKING_TOKEN_MINTS[mint]}"

    symbol_text = symbol.strip().upper() if isinstance(symbol, str) else ""
    name_text = name.strip().lower() if isinstance(name, str) else ""

    if symbol_text in _PROTECTED_SYMBOLS:
        return UNCLEAR, f"symbol_of_known_non_memecoin:{symbol_text}"

    for part in _SUSPICIOUS_NAME_PARTS:
        if part in name_text:
            return UNCLEAR, f"name_suggests_non_memecoin:{part}"

    return MEMECOIN_CANDIDATE, "not_a_known_non_memecoin"


def is_known_non_memecoin(mint):
    return mint in STABLECOIN_MINTS or mint in STAKING_TOKEN_MINTS
