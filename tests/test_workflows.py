"""Statische Prüfung der GitHub-Workflows (ohne sie auszuführen)."""

from pathlib import Path

import yaml


WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def load(name):
    return yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


def all_run_scripts(workflow):
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                yield step["run"]


def test_scan_never_pushes_to_main():
    text = (WORKFLOW_DIR / "scan.yml").read_text(encoding="utf-8")
    workflow = load("scan.yml")

    assert workflow["env"]["DATA_BRANCH"] == "desk-data"
    assert "git add data/events.jsonl" not in text

    for script in all_run_scripts(workflow):
        for line in script.splitlines():
            if "git" in line and " push" in line:
                assert "refs/heads/$DATA_BRANCH" in line
                assert "main" not in line


def test_scan_uses_correct_bot_email():
    text = (WORKFLOW_DIR / "scan.yml").read_text(encoding="utf-8")

    assert BOT_EMAIL in text
    assert '"41898282+users.noreply.github.com"' not in text


def triggers_of(workflow):
    return workflow.get("on", workflow.get(True))


def test_scan_is_started_manually_or_by_clock():
    # Kein eigener Zeitplan: der Taktgeber (clock.yml) ist die einzige Uhr,
    # damit nie doppelte Scans entstehen.
    assert set(triggers_of(load("scan.yml"))) == {"workflow_dispatch"}


def test_scan_requires_tests_before_scanning():
    workflow = load("scan.yml")

    assert workflow["jobs"]["scan"]["needs"] == "test"


def test_write_permission_only_for_scan_job():
    workflow = load("scan.yml")

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["test"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["scan"]["permissions"] == {"contents": "write"}


def test_checkout_does_not_store_credentials():
    for name in ("scan.yml", "tests.yml", "paper-monitor.yml"):
        workflow = load(name)

        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout"):
                    assert step["with"]["persist-credentials"] is False


def test_secrets_only_in_scan_steps():
    workflow = load("scan.yml")

    for step in workflow["jobs"]["test"]["steps"]:
        assert "secrets." not in yaml.safe_dump(step)

    tests_text = (WORKFLOW_DIR / "tests.yml").read_text(encoding="utf-8")
    assert "secrets." not in tests_text


def test_market_data_diagnostic_step_removed():
    text = (WORKFLOW_DIR / "scan.yml").read_text(encoding="utf-8")

    assert "python -m desk.market_data" not in text


def test_tests_workflow_is_read_only():
    workflow = load("tests.yml")

    assert workflow["permissions"] == {"contents": "read"}


def _scan_step(name):
    workflow = load("scan.yml")
    for step in workflow["jobs"]["scan"]["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Schritt fehlt: {name}")


def test_latest_json_generated_from_scan_log():
    step = _scan_step("Kurzübersicht erzeugen")

    assert step["id"] == "latest"
    assert "python -m desk.latest" in step["run"]
    assert "desk-data/events.jsonl desk-data/latest.json" in step["run"]
    assert "steps.scan.outcome" in step["run"]
    assert "!cancelled()" in step["if"]
    assert "secrets." not in yaml.safe_dump(step)
    assert _scan_step("Programm starten")["id"] == "scan"


def test_latest_json_saved_only_on_data_branch_and_removed_if_not_generated():
    script = _scan_step("Protokoll speichern")["run"]

    assert "git add events.jsonl" in script
    assert "git add latest.json" in script
    assert 'steps.latest.outcome }}" = "success"' in script
    assert "git rm -q --cached --ignore-unmatch latest.json" in script
    assert "data/latest.json" not in script


# --------------------------------------------------
# Paper-Broker
# --------------------------------------------------

def test_paper_step_runs_after_scan_and_only_paper_secrets():
    step = _scan_step("Paper-Broker")
    names = [s.get("name") for s in load("scan.yml")["jobs"]["scan"]["steps"]]

    assert names.index("Paper-Broker") > names.index("Kurzübersicht erzeugen")
    assert names.index("Paper-Broker") < names.index("Protokoll speichern")
    assert "python -m desk.paper after-scan" in step["run"]
    assert "steps.scan.outcome" in step["run"]
    assert "!cancelled()" in step["if"]
    assert set(step["env"]) == {
        "DEXPAPRIKA_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
    }
    assert "SOLANA_RPC_URL" not in yaml.safe_dump(step)


def test_paper_files_saved_only_after_successful_paper_step():
    script = _scan_step("Protokoll speichern")["run"]

    assert 'steps.paper.outcome }}" = "success"' in script
    assert "paper_state.json paper_trades.jsonl" in script


def test_scan_and_monitor_share_write_lock():
    scan = load("scan.yml")["concurrency"]
    monitor = load("paper-monitor.yml")["concurrency"]

    assert scan["group"] == monitor["group"] == "desk-data-write"
    assert scan["cancel-in-progress"] is False
    assert monitor["cancel-in-progress"] is False


def test_monitor_workflow_is_limited():
    text = (WORKFLOW_DIR / "paper-monitor.yml").read_text(encoding="utf-8")
    workflow = load("paper-monitor.yml")
    triggers = triggers_of(workflow)

    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["monitor"]["permissions"] == {"contents": "write"}
    assert "python -m desk.paper monitor" in text
    assert "after-scan" not in text
    # keine Helius-Abfragen, kein Scan
    assert "SOLANA_RPC_URL" not in text
    assert "python -m desk\n" not in text
    assert BOT_EMAIL in text
    for script in all_run_scripts(workflow):
        for line in script.splitlines():
            if "git" in line and " push" in line:
                assert "refs/heads/$DATA_BRANCH" in line
                assert "main" not in line


def test_shadow_step_is_learning_only_and_saved_separately():
    step = _scan_step("Schattendepot")
    names = [s.get("name") for s in load("scan.yml")["jobs"]["scan"]["steps"]]
    script = _scan_step("Protokoll speichern")["run"]

    assert names.index("Paper-Broker") < names.index("Schattendepot") < names.index("Protokoll speichern")
    assert "python -m desk.shadow" in step["run"]
    assert "!cancelled()" in step["if"]
    assert "secrets." not in yaml.safe_dump(step)
    assert 'steps.shadow.outcome }}" = "success"' in script
    assert "shadow_state.json shadow_summary.json" in script


# --------------------------------------------------
# Taktgeber
# --------------------------------------------------

def test_clock_only_dispatches_known_workflows():
    workflow = load("clock.yml")
    text = (WORKFLOW_DIR / "clock.yml").read_text(encoding="utf-8")
    steps = workflow["jobs"]["clock"]["steps"]

    assert set(triggers_of(workflow)) == {"workflow_dispatch", "schedule"}
    assert workflow["permissions"] == {"actions": "write", "contents": "read"}
    assert workflow["concurrency"] == {"group": "taktgeber", "cancel-in-progress": False}
    assert not any("uses" in step for step in steps)
    assert "07) dispatch scan.yml" in text
    assert "02|12|17|22|27|32|37|42|47|52|57) dispatch paper-monitor.yml" in text
    assert "workflows/clock.yml/dispatches" in text
    assert '{"ref":"main"}' in text
    # Nur das automatische GITHUB_TOKEN, keine anderen Secrets
    assert text.count("secrets.") == 1
    assert "secrets.GITHUB_TOKEN" in text


def test_clock_restarts_itself_within_job_limit():
    workflow = load("clock.yml")
    job = workflow["jobs"]["clock"]

    assert int(job["env"]["RUN_SECONDS"]) < job["timeout-minutes"] * 60 < 6 * 3600
    assert job["steps"][-1]["if"] == "${{ !cancelled() }}"
