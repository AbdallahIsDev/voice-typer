"""Tests for :func:`voice_typer.server.credential_store.last_store_outcome`.

CR-94: ``store_secret`` returns a plain ``bool`` and silently falls
back to plaintext on keyring failure. The IPC handler (``set_config``
— Fix-G's territory) needs to surface *why* the store fell back so
the renderer can show a "your API key was stored in plaintext because
<reason>" warning. ``last_store_outcome`` returns the outcome of the
most recent ``store_secret`` call on the *current thread* (thread-local
state).

These tests verify:

  - ``last_store_outcome`` returns ``{"stored_in": "unknown", "reason":
    None, "provider": None}`` on a fresh thread that has never called
    ``store_secret``.
  - After a successful ``store_secret`` (keyring available), the
    outcome is ``{"stored_in": "keyring", "reason": None, "provider":
    "openai"}``.
  - After a fallback ``store_secret`` (keyring unavailable / errored),
    the outcome is ``{"stored_in": "plaintext", "reason": "...",
    "provider": "openai"}`` with the keyring exception message
    (redacted).
  - After a delete ``store_secret`` (empty value), the outcome is
    ``{"stored_in": "deleted", "reason": None, "provider": "openai"}``.
  - The reason is passed through ``_redact_sensitive`` (paths and
    API-key-like substrings are stripped) before being stored.
  - The provider field reflects the most recent call (overwritten on
    each new ``store_secret``).
  - The outcome is thread-local: a ``store_secret`` on thread A does
    not change the outcome seen by thread B.
  - The returned dict is a copy — mutating it does not affect
    subsequent ``last_store_outcome`` calls.

The fixtures mirror those in ``tests/test_credential_store.py`` (mock
keyring available / unavailable / raises-on-set) so the two test
files share the same mocking convention (TEST-033).
"""

from __future__ import annotations

import sys
import threading
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


@pytest.fixture(autouse=True)
def _reset_last_store_outcome():
    """Clear the thread-local ``last_store_outcome`` before & after each test.

    Without this, the outcome from one test (e.g. a successful keyring
    store) would leak into the next test running on the same pytest
    worker thread and produce flaky "expected unknown, got keyring"
    failures. We delete the ``outcome`` attribute on the thread-local
    object so ``getattr(_last_store_outcome, "outcome", None)`` returns
    None (the "no store on this thread yet" state).
    """
    if hasattr(credential_store._last_store_outcome, "outcome"):
        del credential_store._last_store_outcome.outcome
    yield
    if hasattr(credential_store._last_store_outcome, "outcome"):
        del credential_store._last_store_outcome.outcome


@pytest.fixture
def mock_keyring_available(monkeypatch):
    """Mock keyring as available with an in-memory store.

    Mirrors the fixture of the same name in ``test_credential_store.py``
    so the two test files share a consistent mocking convention.
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

    backend = _FakeBackend()

    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = backend
    fake_keyring.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring.delete_password.side_effect = lambda s, u: store.pop((s, u), None)

    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "FakeKeyring", None),
    )
    return {"store": store, "backend": backend, "keyring": fake_keyring}


@pytest.fixture
def mock_keyring_unavailable(monkeypatch):
    """Mock keyring as unavailable (fail backend / D-Bus missing).

    Mirrors the fixture of the same name in ``test_credential_store.py``.
    """

    class _FailKeyring:
        name = "fail"

        def get_password(self, service, username):
            raise RuntimeError("no backend available")

        def set_password(self, service, username, password):
            raise RuntimeError("no backend available")

        def delete_password(self, service, username):
            raise RuntimeError("no backend available")

    fail_backend = _FailKeyring()

    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = fail_backend

    fail_module = MagicMock()
    fail_module.Keyring = _FailKeyring
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "no usable keyring backend (fail backend selected)"),
    )
    return fake_keyring


@pytest.fixture
def mock_keyring_raises_on_set(monkeypatch):
    """Mock keyring as available for probing but raising on ``set_password``.

    Simulates the case where the backend is selected but the actual
    write fails (e.g. keychain locked, D-Bus dropped mid-call). The
    store should fall back to plaintext in config.json.
    """
    fake_keyring = MagicMock()

    class _SelectableBackend:
        name = "BrokenKeyring"

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            raise RuntimeError("keychain locked")

        def delete_password(self, service, username):
            raise RuntimeError("keychain locked")

    fake_keyring.get_keyring.return_value = _SelectableBackend()
    fake_keyring.set_password.side_effect = RuntimeError("keychain locked")
    fake_keyring.get_password.return_value = None
    fake_keyring.delete_password.side_effect = RuntimeError("keychain locked")

    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "BrokenKeyring", None),
    )
    return fake_keyring


# ── Tests ───────────────────────────────────────────────────────────────


class TestLastStoreOutcomeUnknown:
    """``last_store_outcome`` before any ``store_secret`` call on this thread."""

    def test_returns_unknown_before_any_store(self):
        """On a fresh thread, ``last_store_outcome`` returns ``unknown``."""
        outcome = credential_store.last_store_outcome()
        assert outcome == {
            "stored_in": "unknown",
            "reason": None,
            "provider": None,
        }

    def test_unknown_outcome_is_a_dict(self):
        """Even on the ``unknown`` path, the return value is a dict."""
        outcome = credential_store.last_store_outcome()
        assert isinstance(outcome, dict)
        assert set(outcome.keys()) == {"stored_in", "reason", "provider"}


class TestLastStoreOutcomeKeyring:
    """``last_store_outcome`` after a successful ``store_secret`` (keyring)."""

    def test_returns_keyring_after_successful_store(self, mock_keyring_available):
        """After a keyring-successful store, outcome is ``keyring``."""
        result = credential_store.store_secret("openai", "sk-test-12345")
        assert result is True  # backwards-compat: store_secret still returns bool
        outcome = credential_store.last_store_outcome()
        assert outcome == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "openai",
        }

    def test_outcome_updated_on_each_call(self, mock_keyring_available):
        """Each ``store_secret`` call overwrites the previous outcome."""
        # First store: succeeds in keyring.
        credential_store.store_secret("openai", "sk-first")
        assert credential_store.last_store_outcome()["stored_in"] == "keyring"
        assert credential_store.last_store_outcome()["provider"] == "openai"
        # Second store: also succeeds — outcome should reflect the latest call.
        credential_store.store_secret("groq", "gsk_second")
        outcome = credential_store.last_store_outcome()
        assert outcome == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "groq",  # latest call's provider wins
        }


class TestLastStoreOutcomePlaintext:
    """``last_store_outcome`` after a fallback ``store_secret`` (plaintext)."""

    def test_returns_plaintext_with_reason_when_keyring_unavailable(self, mock_keyring_unavailable):
        """When keyring is unavailable, outcome is ``plaintext`` with a reason."""
        result = credential_store.store_secret("openai", "sk-test-12345")
        assert result is False  # backwards-compat: plaintext fallback returns False
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext"
        assert outcome["provider"] == "openai"
        # The reason is the redacted exception message. The exact text
        # depends on the mock, but it must be a non-empty string.
        assert isinstance(outcome["reason"], str)
        assert outcome["reason"]  # non-empty

    def test_returns_plaintext_with_reason_when_keyring_raises_on_set(self, mock_keyring_raises_on_set):
        """When keyring raises on set_password, outcome is ``plaintext`` with reason."""
        result = credential_store.store_secret("openai", "sk-test-12345")
        assert result is False
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext"
        assert outcome["provider"] == "openai"
        # The mock raises RuntimeError("keychain locked") — the reason
        # should include that text (after redaction, which doesn't
        # strip the words "keychain" or "locked").
        assert isinstance(outcome["reason"], str)
        assert "keychain" in outcome["reason"]
        assert "locked" in outcome["reason"]

    def test_reason_is_redacted(self, mock_keyring_unavailable, monkeypatch):
        """The reason string is run through ``_redact_sensitive``.

        If a buggy backend embeds the secret value or a filesystem path
        in its exception message, the stored reason must NOT contain
        the secret or the path. We simulate this by making the keyring
        backend raise an exception whose message contains an
        API-key-like substring and a path.
        """
        # Re-mock _probe_keyring to claim the backend works (so we
        # reach the keyring.set_password call), but make set_password
        # raise an exception whose message contains both a path and
        # an API-key-like substring.
        secret_value = "sk-AbCdEfGhIjKlMnOpQrStUv"  # API-key-like

        leaky_exc = RuntimeError(f"failed to write to /home/user/.keyring (secret was {secret_value})")

        class _LeakyBackend:
            name = "LeakyKeyring"

            def get_password(self, service, username):
                return None

            def set_password(self, service, username, password):
                raise leaky_exc

            def delete_password(self, service, username):
                raise RuntimeError("nope")

        fake_keyring = MagicMock()
        fake_keyring.get_keyring.return_value = _LeakyBackend()
        # ``store_secret`` calls ``keyring.set_password(...)`` as a
        # module-level function — the MagicMock's bound method, NOT the
        # backend's ``set_password``. So we MUST set ``side_effect``
        # on the module-level mock too, otherwise the call silently
        # succeeds (MagicMock returns a MagicMock by default) and the
        # store does not fall back to plaintext.
        fake_keyring.set_password.side_effect = leaky_exc
        monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
        fail_module = MagicMock()
        fail_module.Keyring = type("FailKeyring", (), {})
        monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

        credential_store._reset_keyring_cache()
        monkeypatch.setattr(
            credential_store,
            "_probe_keyring",
            lambda: (True, "LeakyKeyring", None),
        )

        result = credential_store.store_secret("openai", secret_value)
        assert result is False
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext"
        assert outcome["provider"] == "openai"
        reason = outcome["reason"]
        assert isinstance(reason, str)
        # The secret value must NOT appear in the reason.
        assert secret_value not in reason
        # The path must be redacted to "[path]".
        assert "/home/user" not in reason
        assert "[path]" in reason


class TestLastStoreOutcomeDeleted:
    """``last_store_outcome`` after a delete ``store_secret`` (empty value)."""

    def test_returns_deleted_after_empty_value_store(self, mock_keyring_available):
        """An empty value triggers a delete; outcome is ``deleted``."""
        result = credential_store.store_secret("openai", "")
        assert result is True  # delete path returns True (backwards-compat)
        outcome = credential_store.last_store_outcome()
        assert outcome == {
            "stored_in": "deleted",
            "reason": None,
            "provider": "openai",
        }


class TestLastStoreOutcomeThreadLocal:
    """``last_store_outcome`` is thread-local."""

    def test_outcome_is_thread_local(self, mock_keyring_available):
        """A ``store_secret`` on thread A must not affect thread B's outcome.

        This is the critical correctness property: the IPC server is
        multi-threaded, and the IPC handler thread that called
        ``store_secret`` is the one that should see the matching
        outcome. A different handler thread serving an unrelated
        request must see ``unknown`` (or its own most recent outcome),
        NOT a stale outcome from thread A.
        """
        # Sanity: on the main thread, before any store, outcome is unknown.
        assert credential_store.last_store_outcome()["stored_in"] == "unknown"

        # Spawn a worker thread that does a store and records its own
        # outcome. The main thread waits for the worker to finish before
        # re-checking its own outcome.
        worker_outcome: dict = {}

        def _worker():
            credential_store.store_secret("openai", "sk-from-worker")
            worker_outcome.update(credential_store.last_store_outcome())

        t = threading.Thread(target=_worker)
        t.start()
        t.join()

        # The worker thread saw the keyring-success outcome.
        assert worker_outcome == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "openai",
        }
        # The main thread STILL sees ``unknown`` — the worker's store
        # did not leak into the main thread's outcome.
        assert credential_store.last_store_outcome() == {
            "stored_in": "unknown",
            "reason": None,
            "provider": None,
        }


class TestLastStoreOutcomeReturnCopy:
    """``last_store_outcome`` returns a copy, not the internal state."""

    def test_returned_dict_is_a_copy(self, mock_keyring_available):
        """Mutating the returned dict does not affect future calls.

        This is a defensive property — the IPC handler may want to
        add fields to the ack payload without worrying about leaking
        mutations back into the credential_store module's thread-local
        state.
        """
        credential_store.store_secret("openai", "sk-test-12345")
        outcome1 = credential_store.last_store_outcome()
        assert outcome1 == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "openai",
        }

        # Mutate the returned dict.
        outcome1["stored_in"] = "tampered"
        outcome1["reason"] = "injected"
        outcome1["provider"] = "tampered"
        outcome1["extra"] = "field"

        # The next call returns a fresh dict — mutations did not stick.
        outcome2 = credential_store.last_store_outcome()
        assert outcome2 == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "openai",
        }
        assert "extra" not in outcome2


class TestSetLastStoreOutcomeInternal:
    """Direct tests for the internal ``_set_last_store_outcome`` helper.

    These verify the storage layer in isolation from the ``store_secret``
    code paths — useful for diagnosing whether a bug is in the helper
    itself or in the wiring inside ``store_secret``.
    """

    def test_set_then_get_roundtrip(self):
        """``_set_last_store_outcome`` then ``last_store_outcome`` round-trips."""
        credential_store._set_last_store_outcome("keyring", None, provider="openai")
        assert credential_store.last_store_outcome() == {
            "stored_in": "keyring",
            "reason": None,
            "provider": "openai",
        }

        credential_store._set_last_store_outcome(
            "plaintext",
            "keyring backend probe failed: D-Bus timeout",
            provider="groq",
        )
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext"
        assert outcome["provider"] == "groq"
        assert outcome["reason"] == "keyring backend probe failed: D-Bus timeout"

    def test_set_redacts_reason(self):
        """``_set_last_store_outcome`` redacts the reason before storing."""
        credential_store._set_last_store_outcome(
            "plaintext",
            "failed to write to /home/alice/.config/voice-typer (secret was sk-AbCdEfGhIjKl)",
            provider="openai",
        )
        outcome = credential_store.last_store_outcome()
        assert outcome["stored_in"] == "plaintext"
        assert outcome["provider"] == "openai"
        # Path is redacted.
        assert "/home/alice" not in outcome["reason"]
        assert "[path]" in outcome["reason"]
        # API-key-like substring is redacted.
        assert "sk-AbCdEfGhIjKl" not in outcome["reason"]
        assert "[redacted]" in outcome["reason"]

    def test_set_none_reason_normalized(self):
        """A ``None`` reason is stored as ``None`` (not "None" string)."""
        credential_store._set_last_store_outcome("deleted", None, provider="groq")
        outcome = credential_store.last_store_outcome()
        assert outcome["reason"] is None
        assert outcome["provider"] == "groq"

    def test_set_empty_reason_normalized_to_none(self):
        """An empty-string reason is normalized to ``None`` for cleanliness."""
        credential_store._set_last_store_outcome("keyring", "", provider="openai")
        outcome = credential_store.last_store_outcome()
        assert outcome["reason"] is None
