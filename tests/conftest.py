import pytest

from app import config


@pytest.fixture(autouse=True)
def _setup_done(monkeypatch):
    """Tests run as an already-configured install; test_setup.py turns this off where it needs the first-run state."""
    monkeypatch.setattr(config, "SETUP_DONE", True)
