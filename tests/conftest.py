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
