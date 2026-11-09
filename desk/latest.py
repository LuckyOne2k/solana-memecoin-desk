"""Kompakte Kurzübersicht des letzten Scans (latest.json).

Die Datei wird ausschließlich aus dem vorhandenen Scan-Protokoll
(events.jsonl) abgeleitet. Es wird nichts neu bewertet: Status, Gründe
und Zahlen werden 1:1 übernommen. Zusätzlich gibt es nur
Plausibilitätsprüfungen. Jede Unstimmigkeit führt dazu, dass KEIN Token
als bereit erscheint.

scan_status:
- "ok": Scan erfolgreich, letzte Protokollzeile ist ein lesbares
  security_scan_completed, alle Zahlen stimmen überein.
- "failed": der Scan-Schritt war nicht erfolgreich.
- "log_unreadable": Protokoll fehlt, ist leer oder die letzte Zeile ist
  beschädigt bzw. kein security_scan_completed.
- "inconsistent": Zahlen widersprechen sich oder ein Status ist unbekannt.

Bei jedem Status außer "ok" sind ready_tokens und pools leer, und es
werden keine alten Ergebnisse übernommen.

events.jsonl wird nur gelesen, nie verändert.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
SCAN_EVENT = "security_scan_completed"
READY = "ready_for_full_risk_screen"
KNOWN_STATUSES = {"rejected", "incomplete", READY}
COUNT_FIELDS = (
    "total_pools",
    "rejected_pools",
    "incomplete_pools",
    "ready_for_full_risk_screen",
)
POOL_FIELDS = (
    "token_symbol",
    "token_name",
    "token_address",
    "pool_address",
    "dex_name",
    "status",
    "security_check_status",
    "rejection_reasons",
    "missing_checks",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _last_line(path):
    """Letzte nicht leere Zeile oder None (Datei fehlt oder leer)."""
    try:
        with Path(path).open("r", encoding="utf-8") as file:
            last = None
            for line in file:
                if line.strip():
                    last = line
            return last
    except (OSError, UnicodeDecodeError):
        return None


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _empty(scan_status, reason, scan_outcome, run_info):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "scan_status": scan_status,
        "status_reason": reason,
        "scan_outcome": scan_outcome,
        "run": run_info,
        "scan_at": None,
        "counts": None,
        "pools_not_listed": None,
        "ready_tokens": [],
        "pools": [],
        "live_trading": False,
    }


def _check_event(event):
    """Grund für eine Unstimmigkeit oder None, wenn alles passt."""
    details = event.get("details")
    if not isinstance(details, dict):
        return "details_missing"

    for field in COUNT_FIELDS:
        if not _is_count(details.get(field)):
            return f"count_invalid:{field}"

    total = details["total_pools"]
    if (
        details["rejected_pools"]
        + details["incomplete_pools"]
        + details["ready_for_full_risk_screen"]
        != total
    ):
        return "counts_do_not_add_up"

    pools = details.get("checked_pools")
    if not isinstance(pools, list):
        return "checked_pools_missing"
    if len(pools) > total:
        return "more_pools_than_total"

    per_status = {status: 0 for status in KNOWN_STATUSES}
    for pool in pools:
        if not isinstance(pool, dict):
            return "pool_invalid"
        status = pool.get("status")
        if status not in KNOWN_STATUSES:
            return "unknown_status"
        if not _is_str_list(pool.get("rejection_reasons", [])):
            return "rejection_reasons_invalid"
        if not _is_str_list(pool.get("missing_checks", [])):
            return "missing_checks_invalid"
        per_status[status] += 1

    if per_status["rejected"] > details["rejected_pools"]:
        return "rejected_count_mismatch"
    if per_status["incomplete"] > details["incomplete_pools"]:
        return "incomplete_count_mismatch"
    # Bereit kann nur ein geprüfter Pool sein: beide Zahlen müssen gleich sein.
    if per_status[READY] != details["ready_for_full_risk_screen"]:
        return "ready_count_mismatch"

    for pool in pools:
        if pool["status"] == READY and (
            pool.get("rejection_reasons") or pool.get("missing_checks")
        ):
            return "ready_pool_with_open_checks"

    return None


def build_latest(events_path, scan_outcome, run_info=None):
    run_info = run_info or {}

    if scan_outcome != "success":
        return _empty("failed", "scan_step_not_successful", scan_outcome, run_info)

    line = _last_line(events_path)
    if line is None:
        return _empty("log_unreadable", "log_missing_or_empty", scan_outcome, run_info)

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return _empty("log_unreadable", "last_line_not_json", scan_outcome, run_info)

    if not isinstance(event, dict) or event.get("event_type") != SCAN_EVENT:
        return _empty(
            "log_unreadable", "last_event_not_scan_result", scan_outcome, run_info
        )

    problem = _check_event(event)
    if problem:
        return _empty("inconsistent", problem, scan_outcome, run_info)

    details = event["details"]
    pools = [
        {field: pool.get(field) for field in POOL_FIELDS}
        for pool in details["checked_pools"]
    ]
    ready_tokens = [
        {
            "token_symbol": pool["token_symbol"],
            "token_name": pool["token_name"],
            "token_address": pool["token_address"],
            "pool_address": pool["pool_address"],
            "dex_name": pool["dex_name"],
        }
        for pool in pools
        if pool["status"] == READY
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "scan_status": "ok",
        "status_reason": None,
        "scan_outcome": scan_outcome,
        "run": run_info,
        "scan_at": event.get("timestamp"),
        "counts": {field: details[field] for field in COUNT_FIELDS},
        "pools_not_listed": details["total_pools"] - len(pools),
        "ready_tokens": ready_tokens,
        "pools": pools,
        "live_trading": False,
    }


def write_latest(data, output_path):
    """Atomar schreiben: erst temporäre Datei, dann ersetzen."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)


def _run_info_from_env():
    return {
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "run_number": os.getenv("GITHUB_RUN_NUMBER"),
        "commit": os.getenv("GITHUB_SHA"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="latest.json erzeugen")
    parser.add_argument("events")
    parser.add_argument("output")
    parser.add_argument("--scan-outcome", required=True)
    args = parser.parse_args(argv)

    data = build_latest(args.events, args.scan_outcome, _run_info_from_env())
    write_latest(data, args.output)
    print("latest.json:", data["scan_status"], data["status_reason"] or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
