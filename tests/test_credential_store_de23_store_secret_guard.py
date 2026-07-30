"""DE-23 regression tests for ``credential_store.store_secret`` non-string guard.

These tests pin the DE-23 fix in ``voice_typer/server/credential_store.py``'s
``store_secret`` function (around lines 594-628 post-fix):

    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Coerce to str (backward compat with old configs that stored api_key as int).
            value = str(value)
        else:
            # dict / list / other — reject with a warning.
            _set_last_store_outcome("plaintext", f"non-string value type {type(value).__name__}", ...)
            return False

Pre-fix, the function assumed ``value: str`` (per its signature). A
truthy non-string value (e.g. ``int 12345`` from a hand-edited config,
or a ``dict`` / ``list`` from a corrupted config.json) would reach
the ``except Exception`` branch where ``len(value)`` is called —
``len(12345)`` raises ``TypeError``, which propagates up through the
IPC handler thread and crashes the save.

The DE-23 finding's "Progress" note explicitly calls out this gap:
    "store_secret in credential_store.py calls len() on values inside
     the except Exception block with no type check on the caught value."

Post-fix:
- ``int`` / ``float`` values (excluding ``bool``, which is a subclass
  of ``int`` in Python) are coerced to ``str`` and stored normally.
- Other non-string truthy values (``dict``, ``list``, etc.) are
  rejected with a WARNING log + ``plaintext`` outcome (the secret is
  NOT written — the caller must fix the config).
- Falsy values (``None``, ``0``, ``[]``, ``{}``, ``""``) hit the
  existing ``if not value:`` short-circuit and are treated as a
  delete request (unchanged behaviour).

See:
- ``voice_typer/server/credential_store.py`` (``store_secret`` function)
- ``scripts/findings/DE-23.md``
- ``tests/test_credential_store_de_fixes.py`` (covers the
  ``migrate_secrets_to_keyring`` side of DE-23)
- ``tests/test_config_de23_save_api_key_guard.py`` (covers the
  ``Config.save()`` side of DE-23)
"""

from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache so each test re-probes
    (the probe is cached at module level for the lifetime of the
    process, which would leak state across tests).
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    credential_store._reset_keyring_cache()
    yield
    credential_store._reset_keyring_cache()


def _install_fake_keyring(monkeypatch, *, available: bool):
    """Install a dict-backed fake keyring module + fail backend module.

    Mirrors the conventions used by ``tests/test_credential_store.py``
    (TEST-033) so the two files share the same mocking style.
    """
    store: dict[tuple[str, str], str] = {}

    class _FakeBackend:
        name = "FakeKeyring"

        def get_password(self, service, username):
            return store.get((service, username))

        def set_password(self, service, username, password):
            store[(service, username)] = password

        def delete_password(self, service, username):
            store.pop((service, username), None)

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

        def delete_password(self, service, username):
            raise RuntimeError("no backend available")

    backend = _FakeBackend() if available else _FailKeyring()
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = backend
    fake_keyring.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = _FailKeyring if not available else type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    if available:
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (True, "FakeKeyring", None),
        )
    else:
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
        )
    return {"store": store, "backend": backend, "keyring": fake_keyring}


@pytest.fixture
def mock_keyring_available(monkeypatch):
    """Mock keyring as available with an in-memory store."""
    return _install_fake_keyring(monkeypatch, available=True)


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing)."""
    return _install_fake_keyring(monkeypatch, available=False)


# ── Tests: int / float coercion ────────────────────────────────────────


class TestStoreSecretCoercesNumericValue:
    """DE-23: ``int`` / ``float`` values are coerced to ``str`` and stored."""

    def test_int_value_coerced_and_stored_in_keyring(self, mock_keyring_available, caplog):
        """An ``int`` value must be coerced to ``str`` and stored in keyring.

        Pre-fix: ``len(12345)`` would raise ``TypeError`` inside the
        ``except Exception`` branch (after ``keyring.set_password``
        itself raised on the non-string value).
        """
        with caplog.at_level(logging.WARNING, logger=credential_store.log.name):
            result = credential_store.store_secret("openai", 12345)  # type: ignore[arg-type]

        assert result is True, (
            "DE-23: store_secret must succeed (return True) when coercing an int to str and storing in keyring."
        )

        # The coerced str value must actually be in the keyring store.
        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai"))
        assert stored == "12345", (
            f"DE-23: int 12345 should have been coerced to '12345' and stored in keyring. Got: {stored!r}"
        )

        # A WARNING must be logged so the user sees the coercion.
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai" in msg and "non-string" in msg and "coercing" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when an int value is coerced. Got: {warning_msgs!r}"
        )

    def test_float_value_coerced_and_stored_in_keyring(self, mock_keyring_available):
        """A ``float`` value is also coerced to ``str``."""
        result = credential_store.store_secret("groq", 67890.0)  # type: ignore[arg-type]

        assert result is True
        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "groq"))
        # float str representation: "67890.0"
        assert stored == "67890.0", f"DE-23: float 67890.0 should have been coerced to '67890.0'. Got: {stored!r}"

    def test_negative_int_coerced(self, mock_keyring_available):
        """A negative int is also coerced (the value itself is the
        caller's responsibility — we just stringify it)."""
        result = credential_store.store_secret("openai", -42)  # type: ignore[arg-type]

        assert result is True
        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai"))
        assert stored == "-42", f"DE-23: int -42 should coerce to '-42'. Got: {stored!r}"


# ── Tests: dict / list rejection ───────────────────────────────────────


class TestStoreSecretRejectsNonStringNonNumeric:
    """DE-23: ``dict`` / ``list`` / other non-string non-numeric values
    are rejected with a WARNING + ``plaintext`` outcome (the secret is
    NOT written — the caller must fix the config)."""

    def test_dict_value_rejected(self, mock_keyring_available, caplog):
        """A ``dict`` value must NOT crash and must return False."""
        with caplog.at_level(logging.WARNING, logger=credential_store.log.name):
            result = credential_store.store_secret("openai", {"secret": "sk-leaked"})  # type: ignore[arg-type]

        assert result is False, "DE-23: store_secret must return False when rejecting a dict value."

        # The dict must NOT have been written to keyring.
        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai"))
        assert stored is None, f"DE-23: dict value should NOT have been stored in keyring. Got: {stored!r}"

        # A WARNING must be logged.
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai" in msg and "non-string" in msg and "rejecting" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when a dict value is rejected. Got: {warning_msgs!r}"
        )

    def test_list_value_rejected(self, mock_keyring_available, caplog):
        """A ``list`` value must NOT crash and must return False."""
        with caplog.at_level(logging.WARNING, logger=credential_store.log.name):
            result = credential_store.store_secret("groq", ["sk-leaked"])  # type: ignore[arg-type]

        assert result is False

        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "groq"))
        assert stored is None, f"DE-23: list value should NOT have been stored in keyring. Got: {stored!r}"

    def test_rejection_records_plaintext_outcome(self, mock_keyring_available):
        """The ``last_store_outcome`` must record ``stored_in='plaintext'``
        with a reason mentioning the non-string type — so the IPC ack
        can surface the issue to the user."""
        credential_store.store_secret("openai", {"secret": "sk-leaked"})  # type: ignore[arg-type]

        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext", (
            f"DE-23: rejection should record stored_in='plaintext'. Got: {outcome!r}"
        )
        assert outcome["provider"] == "openai"
        assert "non-string" in (outcome.get("reason") or ""), (
            f"DE-23: rejection reason should mention 'non-string'. Got: {outcome!r}"
        )
        assert "dict" in (outcome.get("reason") or ""), (
            f"DE-23: rejection reason should mention the type ('dict'). Got: {outcome!r}"
        )


# ── Tests: bool exclusion ──────────────────────────────────────────────


class TestStoreSecretBoolValue:
    """DE-23: ``bool`` values (``True`` / ``False``) are NOT coerced to
    str (``"True"`` / ``"False"`` are not meaningful secrets).

    ``False`` is falsy and hits the ``if not value:`` short-circuit
    (treated as a delete request — unchanged behaviour).
    ``True`` is truthy but not int/float (we exclude bool explicitly),
    so it's rejected with a warning."""

    def test_false_treated_as_delete(self, mock_keyring_available):
        """``False`` is falsy → ``if not value:`` short-circuit → delete."""
        # Pre-populate the keyring with a stale entry so we can verify
        # the delete actually runs.
        mock_keyring_available["store"][(credential_store.KEYRING_SERVICE_NAME, "openai")] = "stale-secret"

        result = credential_store.store_secret("openai", False)  # type: ignore[arg-type]

        assert result is True, "DE-23: store_secret(False) should return True (delete path)."
        # The stale entry was deleted.
        assert mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai")) is None, (
            "DE-23: store_secret(False) should delete any stale keyring entry."
        )

    def test_true_rejected_with_warning(self, mock_keyring_available, caplog):
        """``True`` is truthy but not int/float → rejected with warning."""
        with caplog.at_level(logging.WARNING, logger=credential_store.log.name):
            result = credential_store.store_secret("openai", True)  # type: ignore[arg-type]

        assert result is False, "DE-23: store_secret(True) should return False (rejected)."

        stored = mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai"))
        assert stored is None, f"DE-23: bool True should NOT have been stored in keyring. Got: {stored!r}"

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("openai" in msg and "non-string" in msg for msg in warning_msgs), (
            f"DE-23: a WARNING must be logged when bool True is rejected. Got: {warning_msgs!r}"
        )


# ── Tests: falsy non-string values ─────────────────────────────────────


class TestStoreSecretFalsyNonStringValue:
    """DE-23: falsy non-string values (``None``, ``0``, ``[]``, ``{}``)
    are treated as a delete request via the existing ``if not value:``
    short-circuit — unchanged behaviour, but now explicitly verified."""

    @pytest.mark.parametrize("falsy_value", [None, 0, [], {}])
    def test_falsy_non_string_treated_as_delete(self, mock_keyring_available, falsy_value):
        # Pre-populate the keyring with a stale entry.
        mock_keyring_available["store"][(credential_store.KEYRING_SERVICE_NAME, "openai")] = "stale-secret"

        result = credential_store.store_secret("openai", falsy_value)  # type: ignore[arg-type]

        assert result is True, f"DE-23: store_secret({falsy_value!r}) should return True (delete path)."
        # The stale entry was deleted.
        assert mock_keyring_available["store"].get((credential_store.KEYRING_SERVICE_NAME, "openai")) is None, (
            f"DE-23: store_secret({falsy_value!r}) should delete any stale keyring entry."
        )
