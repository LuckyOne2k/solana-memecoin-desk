import json
import os
from datetime import datetime, timezone
from pathlib import Path


# Standardpfad für lokale Läufe. Der GitHub-Workflow setzt
# DESK_LOG_FILE auf eine Datei im Branch "desk-data", damit das
# Protokoll nicht mehr auf main geschrieben wird.
DEFAULT_LOG_FILE = Path("data") / "events.jsonl"
LOG_FILE_ENV = "DESK_LOG_FILE"


def log_file_path() -> Path:
    configured = os.getenv(LOG_FILE_ENV)
    return Path(configured) if configured else DEFAULT_LOG_FILE


def log_event(event_type: str, details: dict) -> None:
    log_file = log_file_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details,
    }

    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
