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


def test_scan_runs_only_manually():
    workflow = load("scan.yml")
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers) == {"workflow_dispatch"}


def test_scan_requires_tests_before_scanning():
    workflow = load("scan.yml")

    assert workflow["jobs"]["scan"]["needs"] == "test"


def test_write_permission_only_for_scan_job():
    workflow = load("scan.yml")

    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["test"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["scan"]["permissions"] == {"contents": "write"}


def test_checkout_does_not_store_credentials():
    for name in ("scan.yml", "tests.yml"):
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
