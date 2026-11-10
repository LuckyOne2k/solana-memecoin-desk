"""Faire Reihenfolge der Sicherheitsprüfungen (gegen Starvation).

Pro Lauf werden höchstens scanner.max_security_checks_per_run Token
geprüft. Die Reihenfolge entscheidet nur, WELCHE Token in diesem Lauf
geprüft werden, nie das Ergebnis.

Regel (aus den jüngsten Scans in events.jsonl, ohne den laufenden Scan):
zwei Gruppen, in jeder Gruppe am längsten wartend zuerst.
1. Alle Token, deren letzter Prüfversuch NICHT abgelehnt endete: noch
   nie geprüfte, zuletzt unvollständige/fehlerhafte und zuletzt bereite.
2. Danach Token, deren letzter Prüfversuch abgelehnt endete (wie bisher
   nach hinten, Nutzerentscheidung PR #13; älteste Prüfung zuerst).
- Wartezeit eines bereits geprüften Tokens = Zahl der Scans seit seinem
  letzten Prüfversuch (abgeschlossen oder Fehler). Im letzten Scan
  geprüft -> 1.
- Wartezeit eines noch nie geprüften Tokens = Zahl der Scans seit
  seinem ersten Auftritt im Protokoll + 1. Ganz neu -> 1.
- Innerhalb der Gruppe größere Wartezeit zuerst. Bei Gleichstand: noch
  nie geprüft vor zuletzt unvollständig/fehlerhaft vor zuletzt bereit;
  dann bisherige Reihenfolge (DexPaprika).

Folgen:
- Neue Token gehen allen vor, die im letzten Scan geprüft wurden (auch
  bereiten), aber nicht Token, die schon länger warten.
- Jeder nicht abgelehnte Token, der nicht geprüft wird, wartet einen
  Scan länger und rückt dadurch nach vorn; er wird nicht dauerhaft
  verdrängt.
- Zuletzt abgelehnte Token kommen nur an die Reihe, wenn Plätze übrig
  sind (wie bisher); bei dauerhaft mehr als max_checks anderen
  Kandidaten können sie unbegrenzt warten.
- Bereite Token belegen nicht jeden Lauf einen Platz; ein nicht
  ausgewählter, zuvor bereiter Token ist in diesem Lauf "incomplete".

Die Historie bestimmt nur die REIHENFOLGE. Alte Ergebnisse werden nie
übernommen: Jeder ausgewählte Token durchläuft alle Prüfungen frisch,
nicht ausgewählte bleiben "incomplete".

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

QUEUE_NEW = "never_checked_in_recent_scans"
QUEUE_AFTER_INCOMPLETE = "recheck_after_incomplete"
QUEUE_AFTER_READY = "recheck_after_ready"
QUEUE_AFTER_REJECTION = "recheck_after_rejection"
QUEUE_DEFAULT = "default_order_no_history"

# Prüfversuch = Sicherheitsprüfung gestartet (abgeschlossen oder Fehler).
ATTEMPTED_CHECKS = ("completed", "error")

OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_READY = "ready"
OUTCOME_REJECTED = "rejected"

# Rangfolge bei gleicher Wartezeit (kleiner = früher); Rang 0 ist
# "noch nie geprüft".
_NEW_RANK = 0
_OUTCOME_RANK = {OUTCOME_INCOMPLETE: 1, OUTCOME_READY: 2, OUTCOME_REJECTED: 3}
_OUTCOME_REASON = {
    OUTCOME_INCOMPLETE: QUEUE_AFTER_INCOMPLETE,
    OUTCOME_READY: QUEUE_AFTER_READY,
    OUTCOME_REJECTED: QUEUE_AFTER_REJECTION,
}


def _fallback(source):
    return {
        "source": source,
        "last_rejected": {},
        "last_checked": {},
        "first_seen": {},
        "scans_read": 0,
    }


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


def _pool_outcome(pool):
    """Ergebnis eines geprüften Pools für die Reihenfolge."""
    if (
        pool["security_check_status"] == "completed"
        and pool["status"] == "rejected"
        and bool(pool.get("rejection_reasons"))
    ):
        return OUTCOME_REJECTED

    if (
        pool["security_check_status"] == "completed"
        and pool["status"] == "ready_for_full_risk_screen"
    ):
        return OUTCOME_READY

    # unvollständig oder Fehler der Sicherheitsprüfung
    return OUTCOME_INCOMPLETE


def load_check_history(path):
    """Liest frühere Scans.

    Ergebnis:
    - last_checked: {mint: (Scan-Nummer, Zeitpunkt, Ergebnis)} des
      letzten Prüfversuchs (abgeschlossen oder Fehler). Ergebnis eines
      Tokens mit mehreren Pools: unvollständig, wenn ein Pool
      unvollständig/fehlerhaft war; sonst bereit, wenn ein Pool bereit
      war; sonst abgelehnt.
    - last_rejected: {mint: (Scan-Nummer, Zeitpunkt)} für Token, deren
      letzter Prüfversuch abgelehnt endete (nur Protokollangabe).
    - first_seen: {mint: Scan-Nummer} des ersten Auftritts im Protokoll
      (auch ungeprüft), für die Wartezeit noch nie geprüfter Token.
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

    last = {}
    first_seen = {}

    for number, event in enumerate(scans):
        outcome = {}

        for pool in event["details"]["checked_pools"]:
            mint = pool.get("token_address")

            if not mint:
                continue

            first_seen.setdefault(mint, number)

            if pool["security_check_status"] not in ATTEMPTED_CHECKS:
                continue

            current = _pool_outcome(pool)
            previous = outcome.get(mint)

            if previous is None or _OUTCOME_RANK[current] < _OUTCOME_RANK[previous]:
                outcome[mint] = current

        for mint, result in outcome.items():
            last[mint] = (number, event["timestamp"], result)

    return {
        "source": SOURCE_HISTORY,
        "last_checked": last,
        "last_rejected": {
            mint: (number, timestamp)
            for mint, (number, timestamp, result) in last.items()
            if result == OUTCOME_REJECTED
        },
        "first_seen": first_seen,
        "scans_read": len(scans),
    }


def order_check_mints(mints, history):
    """Prüfreihenfolge -> Liste von (mint, queue_reason, previous_rejection_at).

    mints: eindeutige Token in bisheriger Reihenfolge.
    previous_rejection_at: Zeitpunkt, wenn der letzte Prüfversuch
    abgelehnt endete, sonst None.
    """
    if not history or history.get("source") != SOURCE_HISTORY:
        return [(mint, QUEUE_DEFAULT, None) for mint in mints]

    checked = history.get("last_checked") or {}
    first_seen = history.get("first_seen") or {}
    scans = history.get("scans_read") or 0
    entries = []

    for position, mint in enumerate(mints):
        if mint in checked:
            number, timestamp, result = checked[mint]
            waiting = scans - number
            rank = _OUTCOME_RANK[result]
            entry = (
                mint,
                _OUTCOME_REASON[result],
                timestamp if result == OUTCOME_REJECTED else None,
            )
        else:
            waiting = scans - first_seen.get(mint, scans) + 1
            rank = _NEW_RANK
            entry = (mint, QUEUE_NEW, None)

        rejected_group = 1 if rank == _OUTCOME_RANK[OUTCOME_REJECTED] else 0
        entries.append(((rejected_group, -waiting, rank, position), entry))

    entries.sort(key=lambda item: item[0])

    return [entry for _, entry in entries]
