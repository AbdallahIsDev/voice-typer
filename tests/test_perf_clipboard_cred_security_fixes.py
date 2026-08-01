"""Tests for the three Medium-severity perf/reliability findings:

* **IN-21** — ``security.redact_pii`` was missing the fast-path that
  ``_redact_text`` has. Every direct caller (``llm_polish``,
  transcription-PII gating, …) paid the full 8-12 ``re.sub`` cost
  even on inputs that carried no PII / secret / URL-credential
  trigger. Fix: delegate to ``_redact_text``, which gates the whole
  pass behind ``_FAST_TRIGGER.search``.

* **IN-22** — ``ClipboardManager.paste()`` called
  ``_release_stuck_modifiers()`` BEFORE the rate-limit and
  paste_enabled gates. A rate-limited or disabled paste still paid
  4 pynput ``.release()`` round-trips (Ctrl / Shift / Alt / Cmd).
  Fix: move the call AFTER both gates, just before
  ``_is_safe_paste_target()``.

* **IN-23** — ``credential_store._run_keyring_call`` spawned a fresh
  daemon worker thread on every call; on a hung backend (D-Bus
  waiting for a prompt, Keychain waiting for unlock) every call
  timed out and left another orphaned thread running. Fix: track
  orphan count + per-backend consecutive-timeout count; after 2
  consecutive timeouts on the same backend, mark it "wedged" for a
  60 s cooldown during which subsequent calls short-circuit
  (``TimeoutError`` immediately, no thread spawned).
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# ─── pynput / pyperclip mocks (must be in sys.modules before import) ────
# Same pattern as test_clipboard.py — headless CI boxes don't have pynput.

mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())


# ═════════════════════════════════════════════════════════════════════════
# IN-21: security.redact_pii fast-path parity with _redact_text
# ═════════════════════════════════════════════════════════════════════════


class TestRedactPiiFastPath:
    """IN-21: ``redact_pii`` must take the same fast-path as
    ``_redact_text`` so callers that don't go through the log filter
    (``llm_polish``, transcription-PII gating) get the 5-10x speedup
    on inputs that carry no PII / secret / URL-credential trigger.
    """

    def test_redact_pii_returns_input_unchanged_for_no_trigger(self) -> None:
        """Inputs with no ``_FAST_TRIGGER`` match must be returned
        verbatim — the substitution loop must not run."""
        from voice_typer.server.security import redact_pii

        # "hello world" — no @, no +, no 3+ digits, no Bearer/Token/sk-/
        # key=, no 20+ char alnum run. ``_FAST_TRIGGER.search`` misses
        # → fast-path returns the input unchanged.
        text = "hello world"
        assert redact_pii(text) == text
        # Same identity check (the fast-path returns the SAME str
        # object, not a copy — ``==`` would pass even for a copy, so
        # use ``is`` for a stronger fast-path-taken assertion).
        assert redact_pii(text) is text

    def test_redact_pii_matches_redact_text_output(self) -> None:
        """``redact_pii`` delegates to ``_redact_text`` so the two
        functions must produce identical output across a range of
        inputs (trigger and non-trigger). This is the strongest
        behavioral pin — if someone re-inlines the logic in
        ``redact_pii`` and lets it drift, this test catches it."""
        from voice_typer.server.security import _redact_text, redact_pii

        cases = [
            # No trigger — fast-path for both.
            "hello world",
            "the quick brown fox",
            # Email trigger.
            "contact john.doe@example.com for details",
            # Phone trigger (US-style).
            "call 555-123-4567 now",
            # Phone trigger (international E.164).
            "call +1 (415) 555-2671 now",
            # SSN trigger.
            "ssn 123-45-6789",
            # Credit-card trigger.
            "card 4111-1111-1111-1111",
            # IBAN trigger.
            "iban GB82WEST12345698765432",
            # API key (Bearer).
            "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890",
            # API key (bare 20+ char token).
            "key: abcdefghijklmnopqrstuvwxyz0123456789abcd",
            # URL userinfo.
            "curl https://user:pass@example.com/path",
            # Long path with no trigger (path delimiters prevent the
            # 20+ char alnum alternation from matching).
            "/usr/local/lib/python3.11/site-packages/voice_typer",
        ]
        for text in cases:
            assert redact_pii(text) == _redact_text(text), (
                f"redact_pii diverged from _redact_text on input: {text!r}\n"
                f"  redact_pii  = {redact_pii(text)!r}\n"
                f"  _redact_text = {_redact_text(text)!r}"
            )

    def test_redact_pii_still_redacts_real_pii(self) -> None:
        """Regression guard: the fast-path delegation must not break
        actual redaction (the existing test_redact_pii_regressions.py
        covers this too, but the assertion is duplicated here so the
        test file is self-contained)."""
        from voice_typer.server.security import redact_pii

        assert "[EMAIL]" in redact_pii("contact john.doe@example.com")
        assert "[PHONE]" in redact_pii("call 555-123-4567")
        assert "[SSN]" in redact_pii("ssn 123-45-6789")
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")
        # API-key path (redact_secret).
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        assert secret not in redact_pii(f"Using API key: {secret}")


# ═════════════════════════════════════════════════════════════════════════
# IN-22: clipboard paste() must not call _release_stuck_modifiers on the
#        rate-limited / disabled / pynput-unavailable early-return paths
# ═════════════════════════════════════════════════════════════════════════


class TestReleaseStuckModifiersGated:
    """IN-22: ``_release_stuck_modifiers`` must run AFTER the
    rate-limit, paste_enabled, and pynput-availability gates — not
    before. A rate-limited or disabled paste cycle must NOT pay the
    4 pynput ``.release()`` round-trips."""

    def _make_cm(self, **kwargs):
        from voice_typer.server.clipboard import ClipboardManager

        cm = ClipboardManager(**kwargs)
        # Bypass rate-limit by default — individual tests override.
        cm._last_paste_time = -999.0
        cm._keyboard = MagicMock()
        return cm

    def test_release_not_called_when_rate_limited(self, monkeypatch) -> None:
        """Rate-limited paste must short-circuit BEFORE
        ``_release_stuck_modifiers`` runs (previously it ran first,
        paying 4 pynput calls per rate-limited attempt)."""
        from voice_typer.server import clipboard as mod
        from voice_typer.server.clipboard import ClipboardManager

        # Force the rate-limit window to be active (last paste was
        # "now" → next paste within the rate-limit window).
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)
        cm = self._make_cm(paste_enabled=True)
        cm._last_paste_time = 100.0  # == now → 0 ms since last paste → rate-limited

        # Spy on _release_stuck_modifiers — if it fires, the test fails.
        release_calls = []
        monkeypatch.setattr(
            ClipboardManager,
            "_release_stuck_modifiers",
            lambda self: release_calls.append(1),
        )

        result = cm.paste()

        assert result is False, "rate-limited paste must return False"
        assert release_calls == [], (
            f"_release_stuck_modifiers must NOT run on rate-limited paste; called {len(release_calls)} time(s)"
        )
        # Keyboard controller also must not be touched (the spy above
        # is the strongest signal, but this is a defense-in-depth
        # assertion that no other pynput path fired).
        cm._keyboard.press.assert_not_called()
        cm._keyboard.release.assert_not_called()

    def test_release_not_called_when_paste_disabled(self, monkeypatch) -> None:
        """paste_enabled=False must short-circuit BEFORE
        ``_release_stuck_modifiers`` runs."""
        from voice_typer.server import clipboard as mod
        from voice_typer.server.clipboard import ClipboardManager

        # Bypass rate-limit so we actually reach the paste_enabled gate.
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)
        cm = self._make_cm(paste_enabled=False)
        cm._last_paste_time = -999.0

        release_calls = []
        monkeypatch.setattr(
            ClipboardManager,
            "_release_stuck_modifiers",
            lambda self: release_calls.append(1),
        )

        result = cm.paste()

        assert result is False, "disabled paste must return False"
        assert release_calls == [], (
            f"_release_stuck_modifiers must NOT run on disabled paste; called {len(release_calls)} time(s)"
        )

    def test_release_called_on_happy_path(self, monkeypatch) -> None:
        """Positive assertion: on a paste cycle that actually sends a
        keystroke, ``_release_stuck_modifiers`` MUST still be called
        (the move is "after the gates", not "removed entirely")."""
        from voice_typer.server import clipboard as mod
        from voice_typer.server.clipboard import ClipboardManager

        # Force the non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
        monkeypatch.setattr(mod, "is_windows", lambda: False)
        monkeypatch.setattr(mod, "is_macos", lambda: False)
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)

        cm = self._make_cm(paste_enabled=True)
        cm._keyboard = MagicMock()
        # Safety check passes — release fires after the gates but before
        # the keystroke send, which is the property the original
        # PLAT-STUCK fix needed.
        monkeypatch.setattr(ClipboardManager, "_is_safe_paste_target", lambda self: True)
        monkeypatch.setattr(ClipboardManager, "_detect_focused_process", lambda self: None)

        release_calls = []
        monkeypatch.setattr(
            ClipboardManager,
            "_release_stuck_modifiers",
            lambda self: release_calls.append(1),
        )

        result = cm.paste()

        assert result is True, "happy-path paste must return True"
        assert len(release_calls) == 1, (
            f"_release_stuck_modifiers must run exactly once on a successful paste; called {len(release_calls)} time(s)"
        )


# ═════════════════════════════════════════════════════════════════════════
# IN-23: credential_store._run_keyring_call wedged-backend cooldown
# ═════════════════════════════════════════════════════════════════════════


class TestRunKeyringCallWedgedCooldown:
    """IN-23: ``_run_keyring_call`` must bound the orphan-thread leak
    rate by short-circuiting calls to a backend that has timed out
    twice in a row, for a 60 s cooldown window.

    Test strategy: monkeypatch ``_KEYRING_TIMEOUT_SECONDS`` to a tiny
    value so we don't have to wait 5s per timeout; use a backend
    method that sleeps longer than the timeout to force the timeout
    path; assert that after 2 consecutive timeouts the 3rd call
    raises ``TimeoutError`` immediately without spawning a thread.
    """

    @pytest.fixture(autouse=True)
    def _reset_orphan_state_between_tests(self):
        """Clear the orphan / wedged state before AND after each test
        so the module globals don't leak across tests."""
        from voice_typer.server import credential_store

        credential_store._reset_orphan_state()
        yield
        credential_store._reset_orphan_state()

    def _make_hung_backend(self, hang_seconds: float):
        """Build a fake keyring-like backend whose methods sleep
        longer than the (monkeypatched) ``_KEYRING_TIMEOUT_SECONDS``.

        Returns the backend instance — its bound methods are what we
        pass to ``_run_keyring_call`` so the backend_key (derived from
        ``type(func.__self__).__name__``) is stable across calls.
        """

        class _HungBackend:
            """A keyring backend whose get_password hangs forever.

            Simulates a libsecret D-Bus call waiting for a prompt that
            never comes, or a Keychain waiting for an unlock.
            """

            name = "HungBackend"

            def get_password(self, service, username):
                time.sleep(hang_seconds)
                return None

        return _HungBackend()

    def test_first_timeout_increments_orphan_count(self, monkeypatch) -> None:
        """The first timeout on a backend must increment the orphan
        count (but NOT wedge the backend — wedging requires 2
        consecutive timeouts)."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend = self._make_hung_backend(hang_seconds=0.5)

        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        assert credential_store._orphaned_thread_count == 1, "first timeout must bump the orphan count to 1"
        # First timeout must NOT wedge the backend — that requires 2
        # consecutive timeouts.
        backend_key = type(backend).__name__
        assert backend_key not in credential_store._wedged_backends, (
            "first timeout must not wedge the backend (need 2 consecutive)"
        )
        assert credential_store._backend_consecutive_timeouts.get(backend_key) == 1

    def test_second_consecutive_timeout_wedges_backend(self, monkeypatch) -> None:
        """After 2 consecutive timeouts on the same backend, the
        backend must be marked wedged for the cooldown window."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend = self._make_hung_backend(hang_seconds=0.5)

        # First timeout — bumps orphan count, no wedge.
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")
        # Second timeout — wedges the backend.
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        backend_key = type(backend).__name__
        assert backend_key in credential_store._wedged_backends, "second consecutive timeout must wedge the backend"
        # Wedged-until timestamp must be in the future (≈ now + 60s).
        wedged_until = credential_store._wedged_backends[backend_key]
        now = time.monotonic()
        # Allow a small fudge for test scheduling latency.
        assert wedged_until > now + 55.0, f"cooldown must be ~60s; got wedged_until-now={wedged_until - now:.1f}s"
        assert credential_store._orphaned_thread_count == 2

    def test_wedged_backend_short_circuits_without_spawning_thread(self, monkeypatch) -> None:
        """The 3rd call to a wedged backend must raise ``TimeoutError``
        IMMEDIATELY without spawning another worker thread — this is
        the core of the IN-23 fix (bounding the orphan leak rate).

        We assert this by counting ``threading.Thread`` start calls.
        """
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend = self._make_hung_backend(hang_seconds=0.5)

        # Two timeouts → wedge.
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        # Spy on threading.Thread.start to count thread spawns.
        original_thread_start = threading.Thread.start
        spawn_count = {"n": 0}

        def _counting_start(self):
            spawn_count["n"] += 1
            return original_thread_start(self)

        monkeypatch.setattr(threading.Thread, "start", _counting_start)

        # 3rd call to the wedged backend — must short-circuit.
        with pytest.raises(TimeoutError) as exc_info:
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        # The TimeoutError message must indicate the short-circuit
        # path (so operators can distinguish "real timeout" from
        # "wedged short-circuit" in logs).
        assert "wedged" in str(exc_info.value).lower(), (
            f"wedge short-circuit TimeoutError must mention 'wedged'; got: {exc_info.value!s}"
        )
        # NO new thread must have been spawned — this is the
        # orphan-leak bound.
        assert spawn_count["n"] == 0, (
            f"wedged-backend short-circuit must NOT spawn a thread; spawned {spawn_count['n']}"
        )
        # Orphan count stays at 2 (no new orphan from the short-circuit).
        assert credential_store._orphaned_thread_count == 2

    def test_successful_call_resets_consecutive_timeout_count(self, monkeypatch) -> None:
        """A successful call must reset the per-backend consecutive-
        timeout counter so a future transient blip doesn't immediately
        re-wedge the backend (the cooldown is a "two strikes in a row"
        rule, not "two strikes ever")."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)

        class _RecoveringBackend:
            """Backend that hangs the first time, succeeds the second.

            Simulates a backend that recovers after a transient blip
            (e.g. D-Bus briefly slow on a cold boot).
            """

            name = "RecoveringBackend"
            _calls = 0

            def get_password(self, service, username):
                _RecoveringBackend._calls += 1
                if _RecoveringBackend._calls == 1:
                    time.sleep(0.5)  # first call hangs
                return "recovered-value"

        backend = _RecoveringBackend()

        # First call — timeout (1st consecutive).
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")
        backend_key = type(backend).__name__
        assert credential_store._backend_consecutive_timeouts[backend_key] == 1

        # Second call — success. Must reset the consecutive count.
        result = credential_store._run_keyring_call(backend.get_password, "svc", "user")
        assert result == "recovered-value"
        assert backend_key not in credential_store._backend_consecutive_timeouts, (
            "successful call must reset the consecutive-timeout counter"
        )

        # Third call — timeout again. Consecutive count must be 1
        # (NOT 2 — the prior success reset it), so no wedge.
        _RecoveringBackend._calls = 0  # reset so next call hangs again
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")
        assert credential_store._backend_consecutive_timeouts[backend_key] == 1
        assert backend_key not in credential_store._wedged_backends, (
            "single timeout after a success must NOT wedge the backend"
        )

    def test_distinct_backends_wedged_independently(self, monkeypatch) -> None:
        """Two different backends must be wedged independently — a
        wedged libsecret backend must NOT short-circuit calls to a
        (hypothetical) second keyring backend that's working fine."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)

        class _HungA:
            name = "HungA"

            def get_password(self, service, username):
                time.sleep(0.5)

        class _HungB:
            name = "HungB"

            def get_password(self, service, username):
                time.sleep(0.5)

        a = _HungA()
        b = _HungB()

        # Wedge backend A only.
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(a.get_password, "svc", "user")
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(a.get_password, "svc", "user")

        key_a = type(a).__name__
        key_b = type(b).__name__
        assert key_a in credential_store._wedged_backends
        assert key_b not in credential_store._wedged_backends

        # Backend B's first timeout must NOT short-circuit (it's not
        # wedged yet — only 0 consecutive timeouts for B).
        # We assert this indirectly: the call still spawns a thread
        # (and thus still takes ~_KEYRING_TIMEOUT_SECONDS to time out,
        # rather than returning immediately).
        original_thread_start = threading.Thread.start
        b_spawn_count = {"n": 0}

        def _counting_start(self):
            b_spawn_count["n"] += 1
            return original_thread_start(self)

        monkeypatch.setattr(threading.Thread, "start", _counting_start)
        with pytest.raises(TimeoutError) as exc_info:
            credential_store._run_keyring_call(b.get_password, "svc", "user")
        # B's TimeoutError must NOT be the wedge short-circuit message
        # — it must be the real-timeout message.
        assert "wedged" not in str(exc_info.value).lower(), "backend B must NOT short-circuit (not wedged yet)"
        assert b_spawn_count["n"] == 1, "backend B must actually spawn a thread"

    def test_cooldown_expiry_retries_backend(self, monkeypatch) -> None:
        """After the cooldown window elapses, the next call must
        actually try the backend (spawn a thread) rather than
        short-circuit. If the backend is still hung, it gets re-wedged
        immediately — the cooldown is a "give it a moment to recover"
        hint, not a permanent fix."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend = self._make_hung_backend(hang_seconds=0.5)

        # Wedge the backend.
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")
        with pytest.raises(TimeoutError):
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        backend_key = type(backend).__name__
        assert backend_key in credential_store._wedged_backends

        # Manually expire the cooldown by backdating the wedged-until
        # timestamp to the past.
        with credential_store._orphan_state_lock:
            credential_store._wedged_backends[backend_key] = time.monotonic() - 1.0

        # Next call must NOT short-circuit — it must spawn a thread
        # (and time out for real, re-wedging the backend).
        original_thread_start = threading.Thread.start
        spawn_count = {"n": 0}

        def _counting_start(self):
            spawn_count["n"] += 1
            return original_thread_start(self)

        monkeypatch.setattr(threading.Thread, "start", _counting_start)
        with pytest.raises(TimeoutError) as exc_info:
            credential_store._run_keyring_call(backend.get_password, "svc", "user")

        assert "wedged" not in str(exc_info.value).lower(), (
            "post-cooldown call must be a real timeout, not a short-circuit"
        )
        assert spawn_count["n"] == 1, "post-cooldown call must spawn a thread"
        # Backend gets re-wedged immediately (consecutive count was
        # preserved across the cooldown expiry — that's by design).
        assert backend_key in credential_store._wedged_backends, (
            "post-cooldown timeout must re-wedge the backend immediately"
        )

    def test_warning_logged_when_orphan_count_exceeds_threshold(self, monkeypatch, caplog) -> None:
        """When the orphan count crosses ``_ORPHAN_WARN_THRESHOLD``
        (default 20), a WARNING must be logged so operators have a
        visible signal before resource exhaustion bites."""
        from voice_typer.server import credential_store

        # Lower the threshold + cooldown so the test runs fast.
        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(credential_store, "_WEDGE_COOLDOWN_SECONDS", 0.05)
        monkeypatch.setattr(credential_store, "_WEDGE_AFTER_CONSECUTIVE_TIMEOUTS", 100)
        monkeypatch.setattr(credential_store, "_ORPHAN_WARN_THRESHOLD", 3)

        backend = self._make_hung_backend(hang_seconds=0.3)

        with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
            # Trigger 4 timeouts — orphan count crosses the threshold
            # of 3 on the 4th call.
            for _ in range(4):
                # Manually clear any wedged state between calls so we
                # actually spawn a thread each time (we're testing the
                # orphan-threshold WARNING, not the wedge behavior).
                with credential_store._orphan_state_lock:
                    credential_store._wedged_backends.clear()
                    credential_store._backend_consecutive_timeouts.clear()
                with pytest.raises(TimeoutError):
                    credential_store._run_keyring_call(backend.get_password, "svc", "user")

        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        orphan_warnings = [r for r in warning_records if "orphaned" in r.getMessage().lower()]
        assert orphan_warnings, (
            "expected at least one WARNING about orphaned keyring-io thread "
            "count crossing the threshold; got: " + repr([r.getMessage() for r in warning_records])
        )
