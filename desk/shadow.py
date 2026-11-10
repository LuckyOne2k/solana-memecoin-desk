"""Schattendepot: Kursentwicklung ALLER geprüften Token beobachten.

Nur zum Lernen, nie eine Kauf- oder Prüfentscheidung. Nach jedem
erfolgreichen Scan wird für jeden geprüften Pool (bereit, unvollständig
oder abgelehnt) ein Startkurs festgehalten. Bei späteren Scans wird
gemessen, wie sich der Kurs nach 1 h, 6 h und 24 h verändert hat.

Daraus entsteht eine Zusammenfassung je Status und je erstem
Ablehnungsgrund bzw. offenem Prüfpunkt. So lässt sich nach einigen Tagen
sehen, welche Regeln Gewinne verhindern und welche vor Verlusten schützen.

Grenzen (bewusst):
- Kursquelle ist GeckoTerminal (gleicher Pool, gebündelt). Eine Quelle
  genügt hier, weil keine Entscheidung daran hängt. Fehlt der Kurs, wird
  der Messpunkt nicht gesetzt (keine Ersatzwerte).
- Gemessen wird beim ersten Scan nach Ablauf der Frist. Die tatsächlich
  vergangene Zeit steht mit im Protokoll.
- Je Token höchstens eine neue Beobachtung pro 24 h.
- Ohne Kosten und ohne Preiseinfluss: Das sind Kursveränderungen, keine
  Handelsergebnisse.

Dateien in desk-data: shadow_state.json (offene + letzte abgeschlossene
Beobachtungen) und shadow_summary.json (klein, für die App).
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from desk.paper import (
    EVENTS_FILE,
    _parse_time,
    fetch_geckoterminal_quotes,
    load_scan,
)


STATE_VERSION = 1
STATE_FILE = "shadow_state.json"
SUMMARY_FILE = "shadow_summary.json"
HORIZONS_HOURS = (1, 6, 24)
# Beobachtung endet, wenn der letzte Messpunkt so lange überfällig ist
MAX_OBSERVATION_HOURS = 30
NEW_OBSERVATION_COOLDOWN_HOURS = 24
MAX_COMPLETED = 3000
# Eine Beobachtung stammt aus einem Scan, der höchstens so alt sein darf
MAX_SCAN_AGE_MINUTES = 30
BATCH_PAUSE_SECONDS = 2.0


class ShadowStateError(Exception):
    pass


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _hours(start, end):
    return (end - start).total_seconds() / 3600


def new_state(now):
    return {
        "schema_version": STATE_VERSION,
        "purpose": "learning_only_no_trading",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "open": [],
        "completed": [],
    }


def load_state(path, now):
    path = Path(path)
    if not path.exists():
        return new_state(now)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShadowStateError(f"shadow_state.json unlesbar: {type(error).__name__}")
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != STATE_VERSION
        or not isinstance(state.get("open"), list)
        or not isinstance(state.get("completed"), list)
    ):
        raise ShadowStateError("shadow_state.json unplausibel")
    return state


def _write_json(path, data, indent=None):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _group(pool):
    """Erster Ablehnungsgrund bzw. erster offener Prüfpunkt."""
    reasons = pool.get("rejection_reasons") or []
    missing = pool.get("missing_checks") or []
    if pool.get("status") == "rejected" and reasons:
        return f"rejected:{reasons[0]}"
    if pool.get("status") == "incomplete" and missing:
        return f"incomplete:{missing[0]}"
    return pool.get("status") or "unknown"


def _fetch_prices(pairs, fetcher):
    """{pool: Preis oder None}. GeckoTerminal in Blöcken (Pause gegen 429)."""
    prices = {}
    pairs = list(dict.fromkeys(pairs))
    for start in range(0, len(pairs), 30):
        if start:
            time.sleep(BATCH_PAUSE_SECONDS)
        chunk = pairs[start:start + 30]
        result = fetcher(chunk)
        for _, pool in chunk:
            prices[pool] = (result.get(pool) or {}).get("price_usd")
    return prices


def update_observations(state, prices, now):
    """Messpunkte setzen, fertige Beobachtungen abschließen."""
    still_open = []
    for item in state["open"]:
        started = _parse_time(item.get("started_at"))
        start_price = item.get("start_price_usd")
        if started is None or not start_price:
            continue
        elapsed = _hours(started, now)
        price = prices.get(item["pool_address"])
        for horizon in HORIZONS_HOURS:
            key = f"{horizon}h"
            if key in item["returns"] or elapsed < horizon:
                continue
            if price:
                item["returns"][key] = {
                    "return_pct": round((price / start_price - 1) * 100, 4),
                    "elapsed_hours": round(elapsed, 3),
                    "price_usd": price,
                }
        last = f"{HORIZONS_HOURS[-1]}h"
        if last in item["returns"] or elapsed >= MAX_OBSERVATION_HOURS:
            item["completed_at"] = _iso(now)
            state["completed"].append(item)
        else:
            still_open.append(item)
    state["open"] = still_open
    state["completed"] = state["completed"][-MAX_COMPLETED:]


def add_observations(state, pools, prices, scan_at, now):
    recent = {}
    for item in state["open"] + state["completed"]:
        started = _parse_time(item.get("started_at"))
        if started:
            token = item.get("token_address")
            recent[token] = max(recent.get(token, started), started)

    added = 0
    for pool in pools:
        token = pool.get("token_address")
        pool_address = pool.get("pool_address")
        price = prices.get(pool_address)
        if not token or not pool_address or not price:
            continue
        last = recent.get(token)
        if last and _hours(last, now) < NEW_OBSERVATION_COOLDOWN_HOURS:
            continue
        state["open"].append({
            "token_symbol": pool.get("token_symbol"),
            "token_address": token,
            "pool_address": pool_address,
            "status": pool.get("status"),
            "group": _group(pool),
            "rejection_reasons": (pool.get("rejection_reasons") or [])[:5],
            "missing_checks": (pool.get("missing_checks") or [])[:5],
            "scan_at": scan_at,
            "started_at": _iso(now),
            "start_price_usd": price,
            "returns": {},
        })
        recent[token] = now
        added += 1
    return added


def _stats(values):
    if not values:
        return None
    return {
        "count": len(values),
        "mean_pct": round(statistics.fmean(values), 2),
        "median_pct": round(statistics.median(values), 2),
        "positive_share_pct": round(sum(v > 0 for v in values) / len(values) * 100, 1),
    }


def summarize(state, now):
    items = state["completed"] + state["open"]

    def table(key):
        groups = {}
        for item in items:
            groups.setdefault(item.get(key) or "unknown", []).append(item)
        rows = []
        for name, members in groups.items():
            row = {"group": name, "observations": len(members)}
            for horizon in HORIZONS_HOURS:
                values = [
                    m["returns"][f"{horizon}h"]["return_pct"]
                    for m in members if f"{horizon}h" in m["returns"]
                ]
                row[f"{horizon}h"] = _stats(values)
            rows.append(row)
        rows.sort(key=lambda r: -r["observations"])
        return rows

    return {
        "schema_version": STATE_VERSION,
        "purpose": "learning_only_no_trading",
        "generated_at": _iso(now),
        "note": "Kursveränderung ohne Kosten seit erster Beobachtung; keine Kaufempfehlung.",
        "observations_total": len(items),
        "observations_open": len(state["open"]),
        "by_status": table("status"),
        "by_reason": table("group")[:25],
    }


def run_shadow(data_dir, scan_outcome, now=None, fetcher=fetch_geckoterminal_quotes):
    now = now or datetime.now(timezone.utc)
    data_dir = Path(data_dir)
    state = load_state(data_dir / STATE_FILE, now)
    scan = load_scan(data_dir / EVENTS_FILE, scan_outcome, now, MAX_SCAN_AGE_MINUTES)
    pools = [
        p for p in scan["pools"]
        if isinstance(p, dict) and p.get("token_address") and p.get("pool_address")
    ]

    pairs = [(i["token_address"], i["pool_address"]) for i in state["open"]]
    pairs += [(p["token_address"], p["pool_address"]) for p in pools]
    prices = _fetch_prices(pairs, fetcher) if pairs else {}

    update_observations(state, prices, now)
    added = add_observations(state, pools, prices, scan["scan_at"], now)
    state["updated_at"] = _iso(now)
    state["last_run"] = {"at": _iso(now), "scan_status": scan["status"], "added": added,
                         "prices_found": sum(1 for v in prices.values() if v),
                         "prices_requested": len(prices)}

    summary = summarize(state, now)
    _write_json(data_dir / STATE_FILE, state)
    _write_json(data_dir / SUMMARY_FILE, summary, indent=2)
    return {"state": state, "summary": summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Schattendepot (nur Lernen)")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--scan-outcome", default=None)
    args = parser.parse_args(argv)
    try:
        result = run_shadow(args.data_dir, args.scan_outcome)
    except ShadowStateError as error:
        print("Schattendepot gestoppt:", error)
        return 1
    run = result["state"]["last_run"]
    print("SCHATTENDEPOT (nur Lernen, kein Handel)")
    print("Scan:", run["scan_status"], "| neu:", run["added"],
          "| offen:", len(result["state"]["open"]),
          "| Kurse:", f"{run['prices_found']}/{run['prices_requested']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
