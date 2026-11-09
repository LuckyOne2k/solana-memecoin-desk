"""Einmaliger, rein lesender Test: letzte Seite von getProgramAccountsV2
(withContext) für MAGGIE (klassischer SPL-Token). Genau ein RPC-Aufruf.
Gibt nur Struktur aus: keine URL, kein Key, kein paginationKey-Wert,
keine Adressen oder Bestände."""

import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
MINT = "DTqoJ4Usft1mtH7svcXZZEBSN2Rwbat9P71igp53AXjj"  # MAGGIE

URL = os.environ.get("SOLANA_RPC_URL", "").strip()


def main():
    if not URL.startswith("https://"):
        print("FEHLER: SOLANA_RPC_URL fehlt oder ist keine https-URL")
        sys.exit(1)

    config = {
        "encoding": "base64",
        "commitment": "finalized",
        "withContext": True,
        "limit": 1000,
        "dataSlice": {"offset": 32, "length": 40},
        "filters": [
            {"memcmp": {"offset": 0, "bytes": MINT}},
            {"dataSize": 165},
        ],
    }
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "getProgramAccountsV2",
         "params": [TOKEN_PROGRAM, config]}
    ).encode()

    try:
        request = urllib.request.Request(
            URL, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        print(f"FEHLER: HTTP {error.code}")
        sys.exit(1)
    except Exception as error:  # noqa: BLE001
        print(f"FEHLER: {type(error).__name__}")
        sys.exit(1)

    if not isinstance(data, dict) or "error" in data:
        err = data.get("error") if isinstance(data, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        print(f"FEHLER: RPC-Fehler code={code}")
        sys.exit(1)

    result = data.get("result")
    print(f"result Typ: {type(result).__name__}")
    if not isinstance(result, dict):
        sys.exit(1)
    print(f"result Schlüssel: {sorted(result.keys())}")

    context = result.get("context")
    slot = context.get("slot") if isinstance(context, dict) else None
    print(f"context Schlüssel: {sorted(context.keys()) if isinstance(context, dict) else None}")
    print(f"context.slot Ganzzahl: {isinstance(slot, int) and not isinstance(slot, bool)}")

    value = result.get("value")
    print(f"value Typ: {type(value).__name__}")
    if not isinstance(value, dict):
        sys.exit(1)
    print(f"value Schlüssel: {sorted(value.keys())}")

    accounts = value.get("accounts")
    print(f"value.accounts vorhanden: {'accounts' in value}, Typ: {type(accounts).__name__}, "
          f"Anzahl: {len(accounts) if isinstance(accounts, list) else None}")
    count = value.get("count")
    print(f"value.count: {count if isinstance(count, int) else type(count).__name__}")

    if "paginationKey" not in value:
        state = "fehlt"
    elif value["paginationKey"] is None:
        state = "null"
    elif value["paginationKey"] == "":
        state = "leerer String"
    elif isinstance(value["paginationKey"], str):
        state = f"nicht leerer String (Länge {len(value['paginationKey'])}), also keine letzte Seite"
    else:
        state = f"anderer Typ: {type(value['paginationKey']).__name__}"
    print(f"value.paginationKey: {state}")

    parser_ok = (
        isinstance(slot, int) and not isinstance(slot, bool) and slot >= 0
        and isinstance(accounts, list)
        and state in ("fehlt", "null")
    )
    print(f"PR#11-Parser erkennt letzte Seite korrekt: {parser_ok}")
    print("RPC-Aufrufe: 1 (getProgramAccountsV2)")


if __name__ == "__main__":
    main()
