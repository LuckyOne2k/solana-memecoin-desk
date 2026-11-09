"""Zweiter, rein lesender Validierungstest gegen die RPC aus SOLANA_RPC_URL.

Teil A: klassischer SPL-Token (vollständige Konten, Pagination, Owner,
        Summe gegen Mint-Supply, Vergleich DAS/V2).
Teil B: USAR-Widerspruch: alte Holder-Methode (desk.solana_rpc,
        getTokenLargestAccounts) direkt nacheinander gegen die vollständige
        Liste, dazu Transaktionsaktivität des Mints seit dem alten Scan.

Gibt nie URL oder API-Key aus. Fehler nur mit Typ und HTTP-Status.
"""

import base64
import datetime as dt
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.getcwd())

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Nicht-pump-Mints aus dem Scan-Protokoll (memecoin_candidate).
CLASSIC_CANDIDATES = [
    ("MAGGIE", "DTqoJ4Usft1mtH7svcXZZEBSN2Rwbat9P71igp53AXjj"),
    ("TAOLF", "HHgNzGNwvS4MPWqDXp2zkwLEpsxASiWZGtW3MYbV1NhF"),
    ("invest", "5shzBFRCpn5H8fJcWyCpd1s7avjgY1gMbPBU6nFvukij"),
    ("HongKong", "G4So438MRPhEUbV2ZQCKFKENhE3GoQkDT6v2f8obFBgk"),
    ("Rocco", "AGMrCiJRZLGBXtQbnSzRAw4c7uDrwZLgGekDQG1BdqWB"),
    ("José", "87CQtouHSb6YCXaPZ1pp8jD56VudRMXBVqU3oDMD1UJT"),
    ("RM", "6Bs5min6YVfnbXUmuYMjcJsb3SmKhuNZ2hqHHLNHZMc5"),
    ("APU", "6nGocDUtGSJoSz1gUPgFGTpHtUSK3s7FN6zhB6AfLoat"),
]
MAX_CLASSIC_FULL = 2

USAR_MINT = "6REMu7RHGpqMnVzMnqVZNggy2zQHbyamYCxdcBMVpump"
USAR_POOL = "FU8vnN6g4x1YH6dDGtV4sgJNXwyVfStALf2mqByWG61e"
# Alte Scans mit Holder-Daten zu USAR: 11:27, 11:40, 11:53, 12:02 UTC.
OLD_SCAN_FIRST = dt.datetime(2026, 11, 9, 11, 27, 9, tzinfo=dt.timezone.utc)
OLD_SCAN_LAST = dt.datetime(2026, 11, 9, 12, 2, 9, tzinfo=dt.timezone.utc)
HELIUS_TEST_1 = dt.datetime(2026, 11, 9, 12, 38, 1, tzinfo=dt.timezone.utc)
SIG_LOOKBACK = dt.datetime(2026, 11, 9, 11, 0, 0, tzinfo=dt.timezone.utc)

PAGE_LIMIT = 1000
MAX_PAGES = 15
MAX_SIG_PAGES = 8

CREDIT_COST = {
    "getTokenAccounts": 10,
    "getProgramAccounts": 10,
    "getProgramAccountsV2": 1,
    "getSignaturesForAddress": 10,  # konservativ geschätzt
}

URL = os.environ.get("SOLANA_RPC_URL", "").strip()
_SECRETS = []
calls = defaultdict(int)


def _init_redaction():
    _SECRETS.append(URL)
    parsed = urllib.parse.urlsplit(URL)
    for values in urllib.parse.parse_qs(parsed.query).values():
        _SECRETS.extend(v for v in values if len(v) >= 6)


def redact(text):
    text = str(text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    return text


def say(*parts):
    print(redact(" ".join(str(p) for p in parts)), flush=True)


def section(title):
    say("")
    say("=" * 70)
    say(title)
    say("=" * 70)


class RpcFailure(Exception):
    pass


def rpc(method, params):
    calls[method] += 1
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    try:
        request = urllib.request.Request(
            URL, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raise RpcFailure(f"{method}: HTTPError (HTTP {error.code})") from None
    except Exception as error:  # noqa: BLE001
        raise RpcFailure(f"{method}: {type(error).__name__}") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise RpcFailure(f"{method}: ungültiges JSON (HTTP {status})") from None
    if data.get("error"):
        err = data["error"]
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message", "") if isinstance(err, dict) else ""
        raise RpcFailure(f"{method}: RPC-Fehler {code} {redact(msg)[:150]}")
    return data.get("result")


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(data):
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = _B58[rest] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def program_of(mint):
    result = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    value = (result or {}).get("value")
    if not value:
        return None, None
    info = value.get("data", {}).get("parsed", {}).get("info", {})
    return value.get("owner"), info


def supply_of(mint):
    result = rpc("getTokenSupply", [mint])
    return int(result["value"]["amount"]), result["context"]["slot"]


def fetch_v2(mint, program):
    filters = [{"memcmp": {"offset": 0, "bytes": mint}}]
    if program == TOKEN_PROGRAM:
        filters.append({"dataSize": 165})
    accounts, pages, key, complete = {}, 0, None, False
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
        result = rpc("getProgramAccountsV2", [program, config])
        pages += 1
        for item in (result or {}).get("accounts") or []:
            raw = base64.b64decode(item["account"]["data"][0])
            accounts[item["pubkey"]] = (b58(raw[:32]), struct.unpack("<Q", raw[32:40])[0])
        key = (result or {}).get("paginationKey")
        if not key:
            complete = True
            break
    return {"accounts": accounts, "pages": pages, "complete": complete,
            "seconds": time.monotonic() - started}


def fetch_das(mint):
    accounts, pages, cursor, complete, missing = {}, 0, None, False, 0
    started = time.monotonic()
    while pages < MAX_PAGES:
        params = {"mint": mint, "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        result = rpc("getTokenAccounts", params)
        pages += 1
        items = (result or {}).get("token_accounts") or []
        for item in items:
            if not item.get("owner"):
                missing += 1
            accounts[item["address"]] = (item.get("owner"), int(item.get("amount") or 0))
        cursor = (result or {}).get("cursor")
        if not items or not cursor:
            complete = True
            break
    return {"accounts": accounts, "pages": pages, "complete": complete,
            "missing_owner": missing, "seconds": time.monotonic() - started}


def describe(label, fetched, supply):
    acc = fetched["accounts"]
    total = sum(a for _, a in acc.values())
    nonzero = {k: v for k, v in acc.items() if v[1] > 0}
    owners = defaultdict(int)
    for owner, amount in nonzero.values():
        owners[owner] += amount
    top10 = sum(sorted(owners.values(), reverse=True)[:10])
    say(f"  [{label}] Seiten {fetched['pages']}, vollständig {fetched['complete']}, "
        f"{fetched['seconds']:.1f}s")
    say(f"  [{label}] Konten gesamt {len(acc)}, mit Bestand {len(nonzero)}, "
        f"leer {len(acc) - len(nonzero)}, Eigentümer mit Bestand {len(owners)}")
    multi = sum(1 for o in owners if sum(1 for v in nonzero.values() if v[0] == o) > 1)
    say(f"  [{label}] Eigentümer mit mehreren Konten: {multi}")
    if "missing_owner" in fetched:
        say(f"  [{label}] Konten ohne Eigentümer: {fetched['missing_owner']}")
    say(f"  [{label}] Summe/Supply: {total}/{supply} -> "
        f"{'GLEICH' if total == supply else 'ABWEICHUNG ' + str(total - supply)}")
    say(f"  [{label}] Top-10-Eigentümer roh (nur Info): {100 * top10 / supply:.4f} %")
    return nonzero, owners


# ---------------------------------------------------------------- Teil A


def part_a():
    section("A) Klassischer SPL-Token")
    tested = 0
    t22_seen = 0
    for symbol, mint in CLASSIC_CANDIDATES:
        if tested >= MAX_CLASSIC_FULL:
            break
        try:
            program, info = program_of(mint)
        except RpcFailure as error:
            say(symbol, "FEHLER", error)
            continue
        kind = ("SPL Token" if program == TOKEN_PROGRAM else
                "Token-2022" if program == TOKEN_2022_PROGRAM else str(program))
        say(f"{symbol}: {kind}")
        if program != TOKEN_PROGRAM:
            t22_seen += program == TOKEN_2022_PROGRAM
            continue
        say("")
        say(f"--- {symbol} ({mint}) Dezimalstellen {info.get('decimals')}, "
            f"Mint-Authority {'gesetzt' if info.get('mintAuthority') else 'keine'}, "
            f"Freeze-Authority {'gesetzt' if info.get('freezeAuthority') else 'keine'}")
        try:
            supply, slot_a = supply_of(mint)
            say("  Supply (roh):", supply, "Slot", slot_a)
            v2 = fetch_v2(mint, program)
            if not v2["complete"]:
                say(f"  V2 nach {MAX_PAGES} Seiten nicht vollständig "
                    f"(> {MAX_PAGES * PAGE_LIMIT} Konten), DAS übersprungen.")
                describe("getProgramAccountsV2", v2, supply)
                continue
            nz_v2, own_v2 = describe("getProgramAccountsV2", v2, supply)
            das = fetch_das(mint)
            nz_das, own_das = describe("DAS getTokenAccounts", das, supply)
            slot_b = rpc("getSlot", [])
            say("  Slots während der Abfrage:", slot_b - slot_a)
            say("  Abgleich (Konten mit Bestand): gleiche Konten",
                set(nz_v2) == set(nz_das), "| gleiche Eigentümer",
                all(nz_v2[k][0] == nz_das[k][0] for k in set(nz_v2) & set(nz_das)),
                "| gleiche Mengen",
                all(nz_v2[k][1] == nz_das[k][1] for k in set(nz_v2) & set(nz_das)))
            tested += 1
        except RpcFailure as error:
            say("  FEHLER", error)
    if tested == 0:
        say("Kein klassischer SPL-Token vollständig getestet.")
    say(f"Klassisch vollständig getestet: {tested}, Token-2022 übersprungen: {t22_seen}")


# ---------------------------------------------------------------- Teil B


def old_method_snapshot():
    """Exakt die Projektfunktion, die der Scan benutzt."""
    try:
        from desk.solana_rpc import fetch_holder_snapshot
    except Exception as error:  # noqa: BLE001
        say("  Import desk.solana_rpc fehlgeschlagen:", type(error).__name__)
        return None
    calls["getTokenLargestAccounts"] += 1
    calls["getMultipleAccounts"] += 1
    calls["getTokenSupply"] += 1
    try:
        return fetch_holder_snapshot(USAR_MINT, rpc_url=URL)
    except Exception as error:  # noqa: BLE001
        # Meldung nicht ausgeben: auf main kann sie noch die URL enthalten.
        say("  Alte Methode FEHLER:", type(error).__name__)
        return None


def part_b():
    section("B) USAR-Widerspruch")
    program, info = program_of(USAR_MINT)
    say("Programm:", "Token-2022" if program == TOKEN_2022_PROGRAM else program,
        "| Mint-Authority:", "gesetzt" if info.get("mintAuthority") else "keine")

    say("")
    say("B1) Alte Methode (desk.solana_rpc.fetch_holder_snapshot) über Helius")
    snap = old_method_snapshot()
    supply, slot_full = supply_of(USAR_MINT)
    full = fetch_v2(USAR_MINT, program)
    slot_after = rpc("getSlot", [])
    if snap:
        rows = snap.get("accounts") or []
        sampled = sum(r["amount"] for r in rows)
        say(f"  Slot alte Methode: {snap.get('rpc_slot')}, Konten zurück: "
            f"{snap.get('largest_accounts_returned')}, ungültige Zeilen: "
            f"{snap.get('invalid_account_rows')}")
        say(f"  Stichprobe deckt ab: {100 * sampled / snap['supply_amount_raw']:.4f} % "
            f"(alter Scan 12:02: ca. 1,08 %)")
    say("")
    say("B2) Vollständige Liste (V2) direkt danach")
    say(f"  Slot Supply/Liste: {slot_full}..{slot_after}")
    nonzero, owners = describe("getProgramAccountsV2", full, supply)
    if snap:
        top_full = sorted(nonzero.items(), key=lambda kv: kv[1][1], reverse=True)
        sample_addresses = [r["address"] for r in snap.get("accounts") or []]
        n = len(sample_addresses)
        top_n_full = [k for k, _ in top_full[:n]]
        say(f"  Größte {n} Konten laut Liste == Stichprobe der alten Methode: "
            f"{set(top_n_full) == set(sample_addresses)}")
        say(f"  Überschneidung: {len(set(top_n_full) & set(sample_addresses))}/{n}")
        missing = [k for k in top_n_full if k not in sample_addresses]
        say(f"  In Liste groß, aber in Stichprobe fehlend: {len(missing)}")
        for k in missing[:5]:
            say(f"    {k}: {100 * nonzero[k][1] / supply:.4f} %, Eigentümer {nonzero[k][0]}")
        sample_amounts = {r["address"]: r["amount"] for r in snap.get("accounts") or []}
        diff = [k for k, a in sample_amounts.items()
                if k in nonzero and nonzero[k][1] != a]
        say(f"  Stichproben-Konten mit anderer Menge in der Liste: {len(diff)}")
    say("  Größte 5 Konten (Anteil an Supply, Eigentümer = Pool?):")
    for k, (owner, amount) in sorted(nonzero.items(), key=lambda kv: kv[1][1], reverse=True)[:5]:
        say(f"    {k}: {100 * amount / supply:.4f} % | Pool-Eigentümer: {owner == USAR_POOL}")

    say("")
    say("B3) Aktivität des Mints (getSignaturesForAddress)")
    buckets = defaultdict(int)
    failed = 0
    before = None
    oldest = None
    pages = 0
    reached = False
    while pages < MAX_SIG_PAGES:
        params = {"limit": 1000}
        if before:
            params["before"] = before
        try:
            sigs = rpc("getSignaturesForAddress", [USAR_MINT, params])
        except RpcFailure as error:
            say("  FEHLER", error)
            break
        pages += 1
        if not sigs:
            reached = True
            break
        for s in sigs:
            bt = s.get("blockTime")
            if bt is None:
                buckets["ohne Zeit"] += 1
                continue
            t = dt.datetime.fromtimestamp(bt, dt.timezone.utc)
            oldest = t
            if s.get("err"):
                failed += 1
            if t < SIG_LOOKBACK:
                reached = True
                continue
            if t < OLD_SCAN_FIRST:
                buckets["1) 11:00-11:27"] += 1
            elif t <= OLD_SCAN_LAST:
                buckets["2) 11:27-12:02 (alte Scans)"] += 1
            elif t <= HELIUS_TEST_1:
                buckets["3) 12:02-12:38 (bis 1. Helius-Test)"] += 1
            else:
                buckets["4) nach 12:38"] += 1
        before = sigs[-1]["signature"]
        if reached:
            break
    for name in sorted(buckets):
        say(f"  {name}: {buckets[name]} Transaktionen")
    say(f"  davon fehlgeschlagen: {failed} | Seiten: {pages} | "
        f"Zeitraum vollständig bis 11:00 erfasst: {reached} | "
        f"älteste gesehen: {oldest.isoformat() if oldest else '-'}")


def main():
    if not URL:
        say("FEHLER: SOLANA_RPC_URL ist nicht gesetzt.")
        return 1
    _init_redaction()
    say("Host ist Helius:", (urllib.parse.urlsplit(URL).hostname or "").endswith("helius-rpc.com"))
    for part in (part_a, part_b):
        try:
            part()
        except RpcFailure as error:
            say("FEHLER", error)
        except Exception as error:  # noqa: BLE001
            say("FEHLER (unerwartet):", type(error).__name__)
    section("C) Aufrufe und geschätzte Credits")
    total = 0
    for method, count in sorted(calls.items()):
        cost = CREDIT_COST.get(method, 1)
        total += count * cost
        say(f"{method}: {count} x {cost} = {count * cost}")
    say("Geschätzt gesamt:", total, "(tatsächlich: Helius-Dashboard)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
