"""Faire Reihenfolge der Sicherheitsprüfungen (gegen Starvation).

Pro Lauf werden höchstens scanner.max_security_checks_per_run Token
geprüft. Ohne Rotation wären das immer die obersten Token der
DexPaprika-Liste (neueste Pools zuerst); dieselben, bereits abgelehnten
Token verbrauchen dann bei jedem Lauf das Kontingent, weiter unten
stehende Token werden nie geprüft.

Regel:
1. Token, deren letzte abgeschlossene Sicherheitsprüfung (frühere Scans
   in events.jsonl) mit "rejected" endete, rücken nach hinten:
   zuerst die am längsten zurückliegende Ablehnung.
2. Alle anderen Token kommen zuerst, in der bisherigen Reihenfolge.

Die Historie bestimmt nur die REIHENFOLGE. Alte Ergebnisse werden nie
übernommen: Jeder ausgewählte Token durchläuft alle Prüfungen frisch,
nicht ausgewählte bleiben "incomplete". Abgelehnte Token werden nicht
ausgeschlossen und kommen wieder an die Reihe, sobald das Kontingent
reicht oder andere Token jünger abgelehnt wurden.

Fehlt das Protokoll oder ist es beschädigt/unvollständig, gilt die
bisherige Reihenfolge (source != "previous_scans").
"""

import json
from pathlib import Path


SCAN_EVENT = "security_scan_completed"

# Nur die jüngsten Scans zählen (begrenzt Lesezeit und Einfluss).
MAX_SCAN_EVENTS = 50

SOURCE_HISTORY = "previous_scans"
SOURCE_NO_LOG = "no_log"
SOURCE_NO_SCANS = "no_previous_scan"
SOURCE_UNREADABLE = "log_unreadable"

QUEUE_FIRST = "not_rejected_in_recent_scans"
QUEUE_BACK = "rejected_in_previous_scan"
QUEUE_DEFAULT = "default_order_no_history"


def _fallback(source):
    return {"source": source, "last_rejected": {}, "scans_read": 0}


def _valid_pool(pool):
    return (
        isinstance(pool, dict)
        and isinstance(pool.get("security_check_status"), str)
        and isinstance(pool.get("status"), str)
        and (
            pool.get("token_address") is None
            or isinstance(pool.get("token_address"), str)
        )
        and isinstance(pool.get("rejection_reasons", []), list)
    )


def _valid_scan(event):
    details = event.get("details")

    if not isinstance(event.get("timestamp"), str) or not isinstance(details, dict):
        return False

    pools = details.get("checked_pools")

    return isinstance(pools, list) and all(_valid_pool(pool) for pool in pools)


def load_check_history(path):
    """Liest frühere Scans -> {"source", "last_rejected", "scans_read"}.

    last_rejected: {mint: (Scan-Nummer, Zeitpunkt)} für Token, deren
    letzte ABGESCHLOSSENE Sicherheitsprüfung abgelehnt wurde.
    Jede beschädigte Zeile oder unvollständige Scan-Zeile -> Rückfall
    auf die bisherige Reihenfolge (leere Historie).
    """
    path = Path(path)

    if not path.is_file():
        return _fallback(SOURCE_NO_LOG)

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return _fallback(SOURCE_UNREADABLE)

    scans = []

    for line in lines:
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except ValueError:
            return _fallback(SOURCE_UNREADABLE)

        if not isinstance(event, dict):
            return _fallback(SOURCE_UNREADABLE)

        if event.get("event_type") != SCAN_EVENT:
            continue

        if not _valid_scan(event):
            return _fallback(SOURCE_UNREADABLE)

        scans.append(event)

    scans = scans[-MAX_SCAN_EVENTS:]

    if not scans:
        return _fallback(SOURCE_NO_SCANS)

    # Letzte abgeschlossene Prüfung je Token: abgelehnt ja/nein.
    last = {}

    for number, event in enumerate(scans):
        outcome = {}

        for pool in event["details"]["checked_pools"]:
            mint = pool.get("token_address")

            if not mint or pool["security_check_status"] != "completed":
                continue

            rejected = (
                pool["status"] == "rejected"
                and bool(pool.get("rejection_reasons"))
            )
            # Nur abgelehnt, wenn alle geprüften Pools des Tokens
            # abgelehnt wurden.
            outcome[mint] = outcome.get(mint, True) and rejected

        for mint, rejected in outcome.items():
            last[mint] = (number, event["timestamp"], rejected)

    return {
        "source": SOURCE_HISTORY,
        "last_rejected": {
            mint: (number, timestamp)
            for mint, (number, timestamp, rejected) in last.items()
            if rejected
        },
        "scans_read": len(scans),
    }


def order_check_mints(mints, history):
    """Prüfreihenfolge -> Liste von (mint, queue_reason, previous_rejection_at).

    mints: eindeutige Token in bisheriger Reihenfolge.
    """
    if not history or history.get("source") != SOURCE_HISTORY:
        return [(mint, QUEUE_DEFAULT, None) for mint in mints]

    rejected = history.get("last_rejected") or {}
    first = []
    back = []

    for position, mint in enumerate(mints):
        if mint in rejected:
            number, timestamp = rejected[mint]
            back.append((number, position, mint, timestamp))
        else:
            first.append((mint, QUEUE_FIRST, None))

    back.sort(key=lambda entry: (entry[0], entry[1]))

    return first + [
        (mint, QUEUE_BACK, timestamp) for _, _, mint, timestamp in back
    ]
