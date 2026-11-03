import json
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path("data")
LOG_FILE = DATA_DIR / "events.jsonl"


def log_event(event_type: str, details: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details,
    }

    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
