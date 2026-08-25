"""Shared HistoryDB test fixtures.

Single canonical home for environment-pinning fixtures that HistoryDB
test modules need to behave identically on every machine.
"""

import pytest


@pytest.fixture(autouse=True)
def history_plaintext_mode(monkeypatch):
    """Pin HistoryDB writes/read seams to PLAINTEXT mode for this module.

    ``_text_crypto.resolve_dek`` policy #2 GENERATES and stores a new DEK
    whenever an OS keyring is available (Windows Credential Manager on a
    real user session), which silently activates at-rest encryption and
    breaks every raw-SQL assertion (LIKE / GLOB / iterdump round-trips)
    against ciphertext — while the same tests pass on keyless CI. These
    modules test SQL-level search semantics, not crypto, so the DEK
    resolution is forced to "unavailable" here; the encryption behaviors
    themselves are pinned by ``tests/test_history_db_encryption.py`` and
    ``tests/test_text_crypto.py`` with their own explicit fake-keyring
    fixtures.

    The pre-test ``reset_dek_cache`` also neutralizes a DEK cached by an
    earlier test in the same xdist worker process, and the post-test
    reset stops a real DEK from leaking INTO this module's neighbors.
    """
    from voice_typer.server import _text_crypto, credential_store
    from voice_typer.server.credential_store import _dek

    monkeypatch.setattr(_dek, "load_dek", lambda: None)
    monkeypatch.setattr(_dek, "store_dek", lambda dek: False)
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
    yield
    _text_crypto.reset_dek_cache()
    credential_store._reset_keyring_cache()
