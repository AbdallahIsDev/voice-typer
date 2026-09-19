"""
Shared HistoryDB test fixtures.
Single canonical home for environment-pinning fixtures that HistoryDB
"""

import pytest


@pytest.fixture(autouse=True)
def history_plaintext_mode(monkeypatch):
    """Pin HistoryDB writes/read seams to PLAINTEXT mode for this module."""
    from voice_typer.server import _text_crypto, credential_store
    from voice_typer.server.credential_store import _dek

    monkeypatch.setattr(_dek, "load_dek", lambda: None)
    monkeypatch.setattr(_dek, "store_dek", lambda dek: False)
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
    yield
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
