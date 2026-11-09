import pytest

from desk.config import load_config


@pytest.fixture
def desk_config():
    """Die echte config.yaml des Projekts (einzige Quelle der Grenzwerte)."""
    return load_config()
