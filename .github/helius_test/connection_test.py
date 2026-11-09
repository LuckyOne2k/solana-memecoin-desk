"""Einmaliger, rein lesender Verbindungstest gegen die RPC aus SOLANA_RPC_URL.

Nur Standardbibliothek. Gibt nie die URL oder den API-Key aus: jede Ausgabe
läuft durch redact(), Fehler werden nur mit Typ und HTTP-Status gemeldet.
Schreibt nichts ins Repository und kein Protokoll.
"""

import base64
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP7VEhdkAS5EF5szkC79YNmPMTEkbbP"

# Aus dem letzten Scan (Branch desk-data): Holder-Prüfung lief, Top-20
# deckten nur ca. 2 % der Menge ab.
PRIMARY = [
    ("NAOF", "4zM6RaFugh86pMtrVZSfWawzA7E3wEeLaRnSCSpPpump"),
    ("PAD", "4nJoR7zYPKRMZmmf3jE1debHuTuvQTT841VVsYLMpump"),
    ("USAR", "6REMu7RHGpqMnVzMnqVZNggy2zQHbyamYCxdcBMVpump"),
]
# Ersatz, falls keiner der drei ein Token-2022 ist (ebenfalls aus dem Scan).
EXTRA = [
    ("CHILL", "9RZeokzHNXoRyJkWDFdvcQTAzUhKpXm7RrqkUM3Fpump"),
    ("FP", "FEmBXbH5hL1CVR532jxpEWf2hj4A5HhUiWnUS9eXpump"),
    ("PEPE69", "G3PzMuSp2A7oP2qiTwnEywEYsVgTPFKzR8V7QrS9pump"),
    ("Rockstar", "zeeQyPN9wH2tpU3Gr7bRVbKt3ymU1yHzsEDYFM8pump"),
    ("trauma", "FHCntFVzzGmbcUNmf1jBGaBsi4duboB1cwjg4yrcpump"),
]
MAX_TOKENS = 3
PAGE_LIMIT = 1000
MAX_PAGES = 30

# Credit-Schätzung laut Helius-Doku (tatsächlicher Wert nur im Dashboard).
CREDIT_COST = {
    "getTokenAccounts": 10,
    "getProgramAccounts": 10,
    "getProgramAccountsV2": 1,
}

URL = os.environ.get("SOLANA_RPC_URL", "").strip()
_SECRETS = []
calls = defaultdict(int)


def _init_redaction():
    if not URL:
        return
    _SECRETS.append(URL)
    parsed = urllib.parse.urlsplit(URL)
    for values in urllib.parse.parse_qs(parsed.query).values():
        _SECRETS.extend(v for v in values if len(v) >= 6)
    if parsed.path.strip("/") and len(parsed.path.strip("/")) >= 6:
        _SECRETS.append(parsed.path.strip("/"))


def redact(text):
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def say(*parts):
    print(redact(" ".join(str(p) for p in parts)), flush=True)


class RpcFailure(Exception):
    pass


def rpc(method, params):
    calls[method] += 1
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    request = urllib.request.Request(
        URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise RpcFailure(f"{method}: HTTPError (HTTP {error.code})") from None
    except Exception as error:  # noqa: BLE001
        raise RpcFailure(f"{method}: {type(error).__name__}") from None
    elapsed = time.monotonic() - started
    try:
        data = json.loads(raw)
    except ValueError:
        raise RpcFailure(f"{method}: ungültiges JSON (HTTP {status})") from None
    if data.get("error"):
        err = data["error"]
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message", "") if isinstance(err, dict) else ""
        raise RpcFailure(
            f"{method}: RPC-Fehler {code} {redact(msg)[:150]} (HTTP {status})"
        )
    return status, data.get("result"), elapsed, len(raw)


def section(title):
    say("")
    say("=" * 70)
    say(title)
    say("=" * 70)


def connection_check():
    section("1) Verbindung")
    parsed = urllib.parse.urlsplit(URL)
    host = parsed.hostname or ""
    say("Secret gesetzt:", bool(URL))
    say("Host ist Helius:", host.endswith("helius-rpc.com"))
    say("Öffentliche Solana-RPC:", host.endswith("solana.com"))
    ok = True
    for method in ("getHealth", "getSlot", "getVersion"):
        try:
            status, result, elapsed, _ = rpc(method, [])
            say(f"{method}: HTTP {status}, Ergebnis {result}, {elapsed:.2f}s")
        except RpcFailure as error:
            ok = False
            say("FEHLER", error)
    return ok


def mint_info(mint):
    _, result, _, _ = rpc(
        "getAccountInfo", [mint, {"encoding": "jsonParsed"}]
    )
    value = (result or {}).get("value")
    if not value:
        return None
    parsed = value.get("data", {}).get("parsed", {}).get("info", {})
    return {
        "program": value.get("owner"),
        "decimals": parsed.get("decimals"),
        "extensions": [e.get("extension") for e in parsed.get("extensions", [])],
    }


def fetch_das(mint):
    """Helius DAS getTokenAccounts mit Cursor-Pagination."""
    accounts = {}
    pages = 0
    cursor = None
    missing_owner = 0
    complete = False
    started = time.monotonic()
    while pages < MAX_PAGES:
        params = {"mint": mint, "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        _, result, _, _ = rpc("getTokenAccounts", params)
        pages += 1
        items = (result or {}).get("token_accounts") or []
        for item in items:
            if not item.get("owner"):
                missing_owner += 1
            accounts[item["address"]] = (item.get("owner"), int(item.get("amount") or 0))
        cursor = (result or {}).get("cursor")
        if not items or not cursor:
            complete = True
            break
    return {
        "accounts": accounts,
        "pages": pages,
        "complete": complete,
        "missing_owner": missing_owner,
        "seconds": time.monotonic() - started,
    }


def fetch_gpa_v2(mint, program):
    """getProgramAccountsV2 (paginiert) mit Mint-Filter, nur Owner+Menge."""
    filters = [{"memcmp": {"offset": 0, "bytes": mint}}]
    if program == TOKEN_PROGRAM:
        filters.append({"dataSize": 165})
    accounts = {}
    pages = 0
    key = None
    complete = False
    started = time.monotonic()
    while pages < MAX_PAGES:
        config = {
            "encoding": "base64",
            "filters": filters,
            "dataSlice": {"offset": 32, "length": 40},
            "limit": PAGE_LIMIT,
        }
        if key:
            config["paginationKey"] = key
        _, result, _, _ = rpc("getProgramAccountsV2", [program, config])
        pages += 1
        items = (result or {}).get("accounts") or []
        for item in items:
            raw = base64.b64decode(item["account"]["data"][0])
            owner = _b58encode(raw[:32])
            amount = struct.unpack("<Q", raw[32:40])[0]
            accounts[item["pubkey"]] = (owner, amount)
        key = (result or {}).get("paginationKey")
        if not key:
            complete = True
            break
    return {
        "accounts": accounts,
        "pages": pages,
        "complete": complete,
        "seconds": time.monotonic() - started,
    }


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data):
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = _B58[rest] + out
    pad = len(data) - len(data.lstrip(b"\0"))
    return "1" * pad + out


def summarize(label, fetched, supply):
    accounts = fetched["accounts"]
    total = sum(amount for _, amount in accounts.values())
    nonzero = sum(1 for _, amount in accounts.values() if amount > 0)
    by_owner = defaultdict(int)
    for owner, amount in accounts.values():
        by_owner[owner] += amount
    top10 = sum(sorted(by_owner.values(), reverse=True)[:10])
    say(f"  [{label}] Seiten: {fetched['pages']}, vollständig: {fetched['complete']}, "
        f"Dauer {fetched['seconds']:.1f}s")
    say(f"  [{label}] Konten: {len(accounts)} (mit Bestand: {nonzero}), "
        f"Eigentümer: {len(by_owner)}")
    if "missing_owner" in fetched:
        say(f"  [{label}] Konten ohne Eigentümer: {fetched['missing_owner']}")
    if supply:
        say(f"  [{label}] Summe/Gesamtmenge: {total}/{supply} -> "
            f"{'GLEICH' if total == supply else 'ABWEICHUNG ' + str(total - supply)}")
        say(f"  [{label}] Top-10-Eigentümer roh (ohne Abzug Pool/Burn, nur Info): "
            f"{100 * top10 / supply:.2f} %")
    return total, by_owner


def token_check(symbol, mint, info):
    section(f"Token {symbol} ({mint})")
    say("Programm:", "Token-2022" if info["program"] == TOKEN_2022_PROGRAM else
        "SPL Token" if info["program"] == TOKEN_PROGRAM else info["program"])
    if info["extensions"]:
        say("Token-2022-Erweiterungen:", ", ".join(map(str, info["extensions"])))
    slot_before = rpc("getSlot", [])[1]
    _, supply_result, _, _ = rpc("getTokenSupply", [mint])
    supply = int(supply_result["value"]["amount"])
    say("Gesamtmenge (roh):", supply, "Dezimalstellen:", info["decimals"])

    results = {}
    for label, fetch in (
        ("DAS getTokenAccounts", lambda: fetch_das(mint)),
        ("getProgramAccountsV2", lambda: fetch_gpa_v2(mint, info["program"])),
    ):
        try:
            fetched = fetch()
            results[label] = (fetched,) + summarize(label, fetched, supply)
        except RpcFailure as error:
            say(f"  [{label}] FEHLER", error)
        except Exception as error:  # noqa: BLE001
            say(f"  [{label}] FEHLER", type(error).__name__)
    slot_after = rpc("getSlot", [])[1]
    say("Slots während der Abfrage:", slot_after - slot_before)

    if len(results) == 2:
        (a, sum_a, own_a), (b, sum_b, own_b) = results.values()
        same_accounts = set(a["accounts"]) == set(b["accounts"])
        same_owner = all(
            a["accounts"][k][0] == b["accounts"][k][0]
            for k in set(a["accounts"]) & set(b["accounts"])
        )
        say("Abgleich DAS vs. V2: gleiche Konten:", same_accounts,
            "| gleiche Eigentümer:", same_owner,
            "| gleiche Summe:", sum_a == sum_b)


def main():
    if not URL:
        say("FEHLER: SOLANA_RPC_URL ist nicht gesetzt.")
        return 1
    _init_redaction()
    if not connection_check():
        say("Verbindung fehlgeschlagen, Token-Test übersprungen.")
        return 1

    section("2) Auswahl der Test-Token")
    infos = {}
    for symbol, mint in PRIMARY + EXTRA:
        try:
            info = mint_info(mint)
        except RpcFailure as error:
            say(symbol, "FEHLER", error)
            continue
        if info:
            infos[(symbol, mint)] = info
            say(symbol, "->", "Token-2022" if info["program"] == TOKEN_2022_PROGRAM
                else "SPL Token")
    chosen = [k for k in PRIMARY if k in infos][:MAX_TOKENS]
    if not any(infos[k]["program"] == TOKEN_2022_PROGRAM for k in chosen):
        t22 = [k for k in EXTRA if k in infos and infos[k]["program"] == TOKEN_2022_PROGRAM]
        if t22:
            chosen = chosen[: MAX_TOKENS - 1] + [t22[0]]
        else:
            say("Kein Token-2022 unter den Kandidaten gefunden.")
    say("Getestet werden:", ", ".join(s for s, _ in chosen))

    for key in chosen:
        try:
            token_check(key[0], key[1], infos[key])
        except RpcFailure as error:
            say("FEHLER", error)

    section("3) Aufrufe und geschätzte Credits")
    estimate = 0
    for method, count in sorted(calls.items()):
        cost = CREDIT_COST.get(method, 1)
        estimate += count * cost
        say(f"{method}: {count} Aufrufe x {cost} = {count * cost}")
    say("Geschätzte Credits gesamt:", estimate,
        "(tatsächlicher Wert: Helius-Dashboard)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
