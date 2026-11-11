"""Statische Prüfung der rein lesenden Web-App (app/) und ihres Pages-Workflows."""

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"
DATA_URL = (
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/"
    "desk-data/latest.json"
)
FILES = [
    "index.html",
    "style.css",
    "app.js",
    "paper.js",
    "trend.js",
    "sw.js",
    "manifest.webmanifest",
    "icon-192.png",
    "icon-512.png",
]


def text(name):
    return (APP / name).read_text(encoding="utf-8")


def code(name):
    """Quelltext ohne Kommentare (Kommentare dürfen z. B. „keine Wallet“ sagen)."""
    source = re.sub(r"/\*.*?\*/", "", text(name), flags=re.DOTALL)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_all_app_files_exist():
    for name in FILES:
        assert (APP / name).is_file(), name


def test_manifest_is_valid_and_icons_exist():
    manifest = json.loads(text("manifest.webmanifest"))

    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "./"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        assert (APP / icon["src"]).is_file()


def test_only_data_source_is_latest_json_on_desk_data():
    script = text("app.js")

    urls = set(re.findall(r"https?://[^\s\"'`]+", script))
    assert urls == {DATA_URL}
    assert "events.jsonl" not in script


def test_content_security_policy_limits_connections():
    html = text("index.html")

    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert "default-src 'none'" in csp
    assert "connect-src https://raw.githubusercontent.com;" in csp
    assert "script-src 'self';" in csp
    # Keine externen Skripte, Stylesheets oder Schriften.
    assert not re.search(r'<script[^>]+src="https?://', html)
    assert not re.search(r'<link[^>]+href="https?://', html)


def test_app_is_read_only_without_wallet():
    for name in ("app.js", "paper.js", "trend.js", "sw.js", "index.html"):
        source = code(name).lower()
        for forbidden in (
            "method:",
            "post",
            "wallet",
            "phantom",
            "solana.connect",
            "signtransaction",
            "privatekey",
            "secretkey",
            "eval(",
            "new function",
        ):
            assert forbidden not in source, (name, forbidden)


def test_external_data_is_never_inserted_as_html():
    for name in ("app.js", "paper.js", "trend.js"):
        script = text(name)

        assert "innerHTML" not in script
        assert "outerHTML" not in script
        assert "insertAdjacentHTML" not in script
        assert "document.write" not in script


def test_failed_or_invalid_data_hides_tokens():
    script = text("app.js")

    # Nicht "ok" → Dashboard wird geleert, keine Token.
    assert 'if (data.scan_status !== "ok") {\n    hideDashboard();' in script
    # Gespeicherter Stand nur bei fehlender Verbindung und nur gekennzeichnet.
    assert "Offline – Daten möglicherweise veraltet" in script
    assert "STALE_AFTER_MS = 24 * 60 * 60 * 1000" in script


def test_service_worker_never_caches_scan_data():
    worker = text("sw.js")

    assert "latest.json" not in code("sw.js")
    assert "raw.githubusercontent" not in worker
    assert "paper_state" not in worker
    assert "shadow_summary" not in worker
    assert "trend_state" not in worker
    assert "url.origin !== self.location.origin) return;" in worker


def test_pages_workflow_publishes_only_app_from_main():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow[True]  # YAML liest den Schlüssel "on" als True.

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }

    steps = workflow["jobs"]["deploy"]["steps"]
    uses = [step.get("uses", "").split("@")[0] for step in steps]
    assert uses == [
        "actions/checkout",
        "actions/upload-pages-artifact",
        "actions/deploy-pages",
    ]
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["with"]["path"] == "app"
    assert not any("run" in step for step in steps)


PAPER_URL = (
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/"
    "desk-data/paper_state.json"
)


SHADOW_URL = (
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/"
    "desk-data/shadow_summary.json"
)


def test_paper_view_reads_only_paper_state():
    script = text("paper.js")

    # SVG-Namensraum ist kein Abruf
    urls = set(re.findall(r"https?://[^\s\"'`]+", script)) - {"http://www.w3.org/2000/svg"}
    assert urls == {PAPER_URL, SHADOW_URL}
    assert script.count("fetch(") == 2
    assert "localStorage" not in script
    assert 'd.mode !== "paper" || d.live_trading !== false' in script


def test_paper_script_is_loaded_and_cached_as_app_file():
    assert '<script src="paper.js"></script>' in text("index.html")
    assert '"paper.js"' in text("sw.js")


TREND_URL = (
    "https://raw.githubusercontent.com/LuckyOne2k/solana-memecoin-desk/"
    "desk-data/trend_state.json"
)


def test_trend_view_reads_only_trend_state():
    script = text("trend.js")

    urls = set(re.findall(r"https?://[^\s\"'`]+", script))
    assert urls == {TREND_URL}
    assert script.count("fetch(") == 1
    assert "localStorage" not in script
    assert 'd.mode !== "paper" || d.live_trading !== false' in script


def test_trend_script_is_loaded_and_cached_as_app_file():
    assert '<script src="trend.js"></script>' in text("index.html")
    assert '"trend.js"' in text("sw.js")
