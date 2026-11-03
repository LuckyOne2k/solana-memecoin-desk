from datetime import datetime, timezone
from pathlib import Path
import json

def main():
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "scanner_heartbeat",
        "mode": "paper",
        "status": "demo_only",
        "message": "Heartbeat succeeded. Market data, AI debate and simulated order engine are not connected."
    }
    with (data_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(event["message"])

if __name__ == "__main__":
    main()
