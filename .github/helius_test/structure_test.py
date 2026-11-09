"""Einmaliger, rein lesender Strukturtest: getProgramAccountsV2 mit
withContext: true. Höchstens zwei Holder-Seiten. Gibt nur Struktur
(Schlüssel, Typen, Längen, Slots, Anzahlen) aus, nie Kontodaten,
Adressen, paginationKey-Werte, URL oder Key."""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
MINT = "4zM6RaFugh86pMtrVZSfWawzA7E3wEeLaRnSCSpPpump"  # NAOF (Token-2022)
MAX_PAGES = 2

URL = os.environ.get("SOLANA_RPC_URL", "").strip()
SECRETS = [URL] + [
    v
    for values in urllib.parse.parse_qs(urllib.parse.urlsplit(URL).query).values()
    for v in values
    if len(v) >= 6
]
CALLS = {}


def say(*parts):
    text = " ".join(str(p) for p in parts)
    for secret in sorted(SECRETS, key=len, reverse=True):
        if secret:
            text = text.replace(secret, "***")
    print(text, flush=True)


def rpc(method, params):
    CALLS[method] = CALLS.get(method, 0) + 1
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        request = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        say(f"FEHLER {method}: HTTP {error.code}")
        sys.exit(1)
    except Exception as error:  # noqa: BLE001
        say(f"FEHLER {method}: {type(error).__name__}")
        sys.exit(1)
    if not isinstance(data, dict) or "error" in data:
        err = data.get("error") if isinstance(data, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        say(f"FEHLER {method}: RPC-Fehler code={code}")
        sys.exit(1)
    return data.get("result")


def shape(value, depth=0, max_depth=4):
    """Nur Typen und Schlüssel, keine Werte (außer Ganzzahl-Slots)."""
    pad = "  " * depth
    if isinstance(value, dict):
        say(f"{pad}dict keys={sorted(value.keys())}")
        if depth < max_depth:
            for key in sorted(value.keys()):
                if key in ("accounts",):
                    continue
                say(f"{pad}- {key}:")
                shape(value[key], depth + 1, max_depth)
    elif isinstance(value, list):
        say(f"{pad}list len={len(value)}")
    elif isinstance(value, bool):
        say(f"{pad}bool")
    elif isinstance(value, int):
        say(f"{pad}int")
    elif isinstance(value, str):
        say(f"{pad}str len={len(value)}")
    elif value is None:
        say(f"{pad}null")
    else:
        say(f"{pad}{type(value).__name__}")


def find_paths(value, names, path="result"):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            here = f"{path}.{key}"
            if key in names:
                found.append((here, child))
            if key != "accounts":
                found.extend(find_paths(child, names, here))
    return found


def describe_page(number, result):
    say("")
    say(f"=== Seite {number}: Struktur von result ===")
    shape(result)

    slots = find_paths(result, {"slot"})
    say(f"Slot-Fundstellen: {[(p, type(v).__name__) for p, v in slots]}")
    for path, value in slots:
        if isinstance(value, int) and not isinstance(value, bool):
            say(f"  {path} = {value} (Ganzzahl)")

    accounts = find_paths(result, {"accounts"})
    if isinstance(result, list):
        accounts.append(("result (Liste)", result))
    say(f"Konten-Fundstellen: {[(p, type(v).__name__) for p, v in accounts]}")
    for path, value in accounts:
        if isinstance(value, list):
            say(f"  {path}: {len(value)} Einträge")
            if value and isinstance(value[0], dict):
                first = value[0]
                say(f"  Eintrag-Schlüssel: {sorted(first.keys())}")
                account = first.get("account")
                if isinstance(account, dict):
                    say(f"  account-Schlüssel: {sorted(account.keys())}")
                    say(f"  account.owner == Token-2022: {account.get('owner') == TOKEN_2022}")
                    data = account.get("data")
                    if isinstance(data, list) and len(data) == 2:
                        raw = base64.b64decode(data[0])
                        say(f"  account.data: Liste[2], Kodierung={data[1]!r}, dekodiert {len(raw)} Byte")
                    else:
                        say(f"  account.data Typ: {type(data).__name__}")
                say(f"  pubkey Typ: {type(first.get('pubkey')).__name__}")

    keys = find_paths(result, {"paginationKey"})
    say(f"paginationKey-Fundstellen: {[(p, type(v).__name__, len(v) if isinstance(v, str) else None) for p, v in keys]}")

    # Vergleich mit dem Parser aus PR #11
    ctx = result.get("context") if isinstance(result, dict) else None
    val = result.get("value") if isinstance(result, dict) else None
    slot = ctx.get("slot") if isinstance(ctx, dict) else None
    ok_slot = isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0
    ok_value = isinstance(val, dict)
    ok_accounts = ok_value and isinstance(val.get("accounts"), list)
    key = val.get("paginationKey") if ok_value else None
    ok_key = key is None or (isinstance(key, str) and key)
    say(f"PR#11-Form: context.slot int={ok_slot}, value dict={ok_value}, "
        f"value.accounts Liste={ok_accounts}, value.paginationKey gültig/fehlt={ok_key}")
    say(f"PASST ZUR ERWARTETEN FORM: {ok_slot and ok_value and ok_accounts and ok_key}")

    for path, value in keys:
        if isinstance(value, str) and value:
            return value
    return None


def main():
    if not URL.startswith("https://"):
        say("FEHLER: SOLANA_RPC_URL fehlt oder ist keine https-URL")
        sys.exit(1)

    key = None
    for number in range(1, MAX_PAGES + 1):
        config = {
            "encoding": "base64",
            "commitment": "finalized",
            "withContext": True,
            "limit": 1000,
            "dataSlice": {"offset": 32, "length": 40},
            "filters": [{"memcmp": {"offset": 0, "bytes": MINT}}],
        }
        if key:
            config["paginationKey"] = key
        result = rpc("getProgramAccountsV2", [TOKEN_2022, config])
        key = describe_page(number, result)
        if not key:
            say("Kein paginationKey: keine weitere Seite.")
            break

    say("")
    say(f"Aufrufe: {CALLS} (geschätzt {sum(CALLS.values())} Credits)")


if __name__ == "__main__":
    main()
