import pytest

from desk.config import load_config
from desk.solana_rpc import HolderListError


@pytest.fixture
def desk_config():
    """Die echte config.yaml des Projekts (einzige Quelle der Grenzwerte)."""
    return load_config()


@pytest.fixture(autouse=True)
def no_helius_by_default(monkeypatch):
    """Kein Test erreicht Helius. Ohne eigenes Mock schlagen Mint-Info und
    vollständige Holder-Liste fehl; die Prüfkette nutzt dann den Rückfall
    auf die Stichprobe. Tests, die die vollständige Liste brauchen,
    ersetzen diese Funktionen selbst (patch innerhalb des Tests)."""

    def mint_info_unavailable(*args, **kwargs):
        raise HolderListError("rpc_error")

    def full_list_unavailable(*args, **kwargs):
        raise HolderListError("rpc_error")

    monkeypatch.setattr(
        "desk.security_scan.fetch_mint_info", mint_info_unavailable
    )
    monkeypatch.setattr(
        "desk.security_scan.fetch_full_holder_list", full_list_unavailable
    )

    def onchain_unavailable(*args, **kwargs):
        raise AssertionError("on-chain bounds query in test without mock")

    monkeypatch.setattr(
        "desk.security_scan.fetch_authority_accounts", onchain_unavailable
    )
    monkeypatch.setattr(
        "desk.security_scan.fetch_frozen_token_accounts", onchain_unavailable
    )


@pytest.fixture(autouse=True)
def isolated_log_file(tmp_path, monkeypatch):
    """Kein Test liest oder schreibt das echte Protokoll im Repository
    (wichtig für die Prüfreihenfolge aus desk.check_queue)."""
    monkeypatch.setenv("DESK_LOG_FILE", str(tmp_path / "events.jsonl"))


@pytest.fixture(autouse=True)
def no_real_retry_wait(monkeypatch):
    """GeckoTerminal-Wiederholungen warten in Tests nicht wirklich.
    Tests, die die Wartezeiten prüfen, ersetzen _wait selbst."""
    monkeypatch.setattr("desk.market_data._wait", lambda seconds: None)
