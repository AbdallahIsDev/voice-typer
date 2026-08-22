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

import threading
import time
from unittest.mock import MagicMock

import pytest

# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).


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
            f"_release_stuck_modifiers must NOT run on rate-limited paste; called {len(release_calls)} times"
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
            f"_release_stuck_modifiers must NOT run on disabled paste; called {len(release_calls)} times"
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
            f"_release_stuck_modifiers must run exactly once on a successful paste; called {len(release_calls)} times"
        )


# ═════════════════════════════════════════════════════════════════════════
# IN-23: credential_store._run_keyring_call wedged-backend cooldown
# ═════════════════════════════════════════════════════════════════════════


# Module-level state symbols in ``voice_typer.server.credential_store``.
# The production code (see ``credential_store._run_keyring_call``) uses a
# SINGLE GLOBAL wedge/cooldown state — not a per-backend dict. The symbols
# below are the actual production API; tests must mutate them under
# ``_keyring_state_lock`` to avoid racing with the runner thread.
#
# NOTE: an earlier draft of this test file assumed a per-backend wedge dict
# (``_wedged_backends``, ``_backend_consecutive_timeouts``) and a
# ``_reset_orphan_state()`` helper. None of those exist in production — the
# real API is the global trio below. The tests were rewritten to match the
# production API.


class TestRunKeyringCallWedgedCooldown:
    """``_run_keyring_call`` must bound the orphan-thread leak
    rate by short-circuiting calls when the backend has timed out twice
    in a row, for a 60 s cooldown window.

    Test strategy: monkeypatch ``_KEYRING_TIMEOUT_SECONDS`` to a tiny
    value so we don't have to wait 5s per timeout; use a backend
    method that sleeps longer than the timeout to force the timeout
    path; assert that after 2 consecutive timeouts the 3rd call
    raises ``TimeoutError`` immediately without spawning a thread.

    Production API (verified in
    ``voice_typer/server/credential_store/_backend.py``):

    * ``_orphaned_thread_count``   — global int, bumped on each timeout
      orphan, decremented when the orphan thread eventually finishes.
    * ``_consecutive_timeouts``    — global int, bumped on each
      consecutive timeout, reset to 0 on any non-timeout completion
      (success OR non-timeout exception).
    * ``_wedged_until``            — global float (monotonic timestamp).
      While ``_wedged_until > now``, every call short-circuits with a
      ``TimeoutError`` mentioning "wedged" — WITHOUT spawning a thread.
      Set on the 2nd consecutive timeout to ``now + _KEYRING_WEDGE_COOLDOWN_S``.
    * ``_keyring_state_lock``      — module-level ``threading.Lock`` guarding
      all of the above.
    * ``_KEYRING_TIMEOUT_SECONDS`` — per-call timeout (monkeypatched small
      here so each timeout is fast).
    * ``_KEYRING_WEDGE_COOLDOWN_S`` — wedge cooldown duration (default 60s;
      monkeypatched small here so cooldown-expiry tests don't wait a minute).
    * ``_KEYRING_ORPHAN_WARN_THRESHOLD`` — orphan count above which a
      WARNING is logged (default 20; monkeypatched small here so the
      warning is reachable from a short test).
    """

    @pytest.fixture(autouse=True)
    def _reset_orphan_state_between_tests(self):
        """Clear the orphan / wedged state before AND after each test
        so the module globals don't leak across tests.

        The production code does NOT expose a ``_reset_orphan_state()``
        helper (an earlier draft of this test assumed one). We mutate
        the global state directly under the production lock so we don't
        race with a still-running orphan thread from a prior test.

        The counters are re-bound globals OWNED by
        ``credential_store._backend`` and mutated there via bare-name
        lookup — writing through the package module would only shadow
        the PEP 562 delegation without reaching production code, so
        every access below targets the owning submodule.
        """
        from voice_typer.server import credential_store

        with credential_store._keyring_state_lock:
            credential_store._backend._orphaned_thread_count = 0
            credential_store._backend._consecutive_timeouts = 0
            credential_store._backend._wedged_until = 0.0
        yield
        with credential_store._keyring_state_lock:
            credential_store._backend._orphaned_thread_count = 0
            credential_store._backend._consecutive_timeouts = 0
            credential_store._backend._wedged_until = 0.0

    def _make_hung_backend_event(self):
        """Build a fake keyring-like backend whose ``get_password``
        blocks on a ``threading.Event`` (instead of ``time.sleep``).

        Using an Event (rather than ``time.sleep(hang_seconds)``) lets
        us deterministically release the orphan thread in a ``finally``
        block — so the test doesn't leak orphan threads across tests
        AND so the production code's "decrement on completion" path
        runs before our assertions, eliminating the race that bedeviled
        an earlier draft (which used ``time.sleep(0.5)`` and asserted
        ``_orphaned_thread_count == 2`` — flaky because the orphan
        could finish + decrement between the timeout and the assertion).

        Mirrors the pattern in
        ``tests/test_credential_store_keyring_orphan.py`` (the canonical
        test for this API).

        Returns ``(backend, done_event)`` — call ``done_event.set()`` in
        a ``finally`` block to release the orphan.
        """
        done = threading.Event()

        class _HungBackend:
            """A keyring backend whose get_password blocks until
            ``done`` is set (or 5s elapses, as a safety net).

            Simulates a libsecret D-Bus call waiting for a prompt that
            never comes, or a Keychain waiting for an unlock.
            """

            name = "HungBackend"

            def get_password(self, service, username):
                done.wait(timeout=5.0)
                return None

        return _HungBackend(), done

    def test_first_timeout_increments_orphan_count(self, monkeypatch) -> None:
        """The first timeout on a backend must increment the orphan
        count (but NOT wedge the backend — wedging requires 2
        consecutive timeouts)."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend, done = self._make_hung_backend_event()

        try:
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")

            assert credential_store._backend._orphaned_thread_count == 1, (
                "first timeout must bump the orphan count to 1"
            )
            # First timeout must NOT wedge — that requires 2 consecutive
            # timeouts. ``_wedged_until`` must still be 0.0 (no cooldown set).
            assert credential_store._backend._wedged_until == 0.0, (
                "first timeout must not wedge the backend (need 2 consecutive)"
            )
            assert credential_store._backend._consecutive_timeouts == 1
        finally:
            done.set()

    def test_second_consecutive_timeout_wedges_backend(self, monkeypatch) -> None:
        """After 2 consecutive timeouts on the same backend, the
        global wedge cooldown must engage (``_wedged_until`` is set to
        ``now + _KEYRING_WEDGE_COOLDOWN_S``)."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        # Two independent Events so each orphan can be released independently.
        done1 = threading.Event()
        done2 = threading.Event()

        class _HungBackend:
            name = "HungBackend"

            _call_n = 0

            def get_password(self, service, username):
                _HungBackend._call_n += 1
                if _HungBackend._call_n == 1:
                    done1.wait(timeout=5.0)
                else:
                    done2.wait(timeout=5.0)
                return None

        backend = _HungBackend()

        try:
            # First timeout — bumps orphan count, no wedge.
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            # Second timeout — wedges the backend (sets ``_wedged_until``).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")

            # Wedged-until timestamp must be in the future (≈ now + 60s).
            wedged_until = credential_store._backend._wedged_until
            now = time.monotonic()
            # Allow a small fudge for test scheduling latency.
            assert wedged_until > now + 55.0, f"cooldown must be ~60s; got wedged_until-now={wedged_until - now:.1f}s"
            assert credential_store._backend._orphaned_thread_count == 2
            assert credential_store._backend._consecutive_timeouts == 2
        finally:
            done1.set()
            done2.set()

    def test_wedged_backend_short_circuits_without_spawning_thread(self, monkeypatch) -> None:
        """The 3rd call (while wedge cooldown is active) must raise
        ``TimeoutError`` IMMEDIATELY without spawning another worker
        thread — this is the core of the IN-23 fix (bounding the orphan
        leak rate).

        We assert this by counting ``threading.Thread`` start calls.
        """
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done1 = threading.Event()
        done2 = threading.Event()

        class _HungBackend:
            name = "HungBackend"

            _call_n = 0

            def get_password(self, service, username):
                _HungBackend._call_n += 1
                if _HungBackend._call_n == 1:
                    done1.wait(timeout=5.0)
                else:
                    done2.wait(timeout=5.0)
                return None

        backend = _HungBackend()

        try:
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

            # 3rd call while wedged — must short-circuit.
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
            assert spawn_count["n"] == 0, f"wedge short-circuit must NOT spawn a thread; spawned {spawn_count['n']}"
            # Orphan count stays at 2 (no new orphan from the short-circuit).
            # The two prior orphans are still blocked on `done1`/`done2`
            # (released in the `finally` below), so they haven't
            # decremented yet.
            assert credential_store._backend._orphaned_thread_count == 2
        finally:
            done1.set()
            done2.set()

    def test_successful_call_resets_consecutive_timeout_count(self, monkeypatch) -> None:
        """A successful call must reset the consecutive-timeout counter
        so a future transient blip doesn't immediately re-wedge the
        backend (the cooldown is a "two strikes in a row" rule, not
        "two strikes ever")."""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done = threading.Event()

        class _RecoveringBackend:
            """Backend that hangs the first time, succeeds the second,
            then hangs the third.

            Simulates a backend that recovers after a transient blip
            (e.g. D-Bus briefly slow on a cold boot).
            """

            name = "RecoveringBackend"
            _calls = 0

            def get_password(self, service, username):
                _RecoveringBackend._calls += 1
                if _RecoveringBackend._calls in (1, 3):
                    done.wait(timeout=5.0)  # 1st + 3rd calls hang
                return "recovered-value"

        backend = _RecoveringBackend()

        try:
            # First call — timeout (1st consecutive).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._consecutive_timeouts == 1

            # Second call — success. Must reset the consecutive count.
            result = credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert result == "recovered-value"
            assert credential_store._backend._consecutive_timeouts == 0, (
                "successful call must reset the consecutive-timeout counter"
            )

            # Third call — timeout again. Consecutive count must be 1
            # (NOT 2 — the prior success reset it), so no wedge.
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._consecutive_timeouts == 1
            assert credential_store._backend._wedged_until == 0.0, (
                "single timeout after a success must NOT wedge the backend"
            )
        finally:
            done.set()

    def test_wedge_is_global_across_backends(self, monkeypatch) -> None:
        """The production ``_run_keyring_call`` tracks wedge state
        GLOBALLY (a single ``_wedged_until`` timestamp), not per
        backend. Once a wedge fires on backend A, calls to ANY backend
        (B, C, …) also short-circuit.

        An earlier draft of this test assumed per-backend wedge dicts
        (``_wedged_backends``) — that API does NOT exist in production
        (see ``credential_store._run_keyring_call``). The test was
        rewritten to assert the actual production behavior: a wedge
        from ANY backend short-circuits calls to ALL backends.

        This is a defensible design choice: in practice there is only
        one keyring backend per platform (SecretService on Linux,
        Keychain on macOS, Windows Credential Manager on Windows), so
        a per-backend wedge dict would be over-engineering. The global
        wedge bounds the orphan leak rate regardless.
        """
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done_a1 = threading.Event()
        done_a2 = threading.Event()

        class _HungA:
            name = "HungA"

            _call_n = 0

            def get_password(self, service, username):
                _HungA._call_n += 1
                if _HungA._call_n == 1:
                    done_a1.wait(timeout=5.0)
                else:
                    done_a2.wait(timeout=5.0)
                return None

        class _HungB:
            name = "HungB"

            def get_password(self, service, username):
                # B is never actually called — the wedge short-circuits
                # before its thread is spawned. But if it WERE called,
                # it would hang too (defensive).
                done_a2.wait(timeout=5.0)
                return None

        a = _HungA()
        b = _HungB()

        try:
            # Wedge via backend A (two consecutive timeouts).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(a.get_password, "svc", "user")
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(a.get_password, "svc", "user")

            # Global wedge must now be active.
            assert credential_store._backend._wedged_until > time.monotonic()

            # Backend B's call must short-circuit too (global wedge),
            # raising the wedge TimeoutError WITHOUT spawning a thread.
            original_thread_start = threading.Thread.start
            b_spawn_count = {"n": 0}

            def _counting_start(self):
                b_spawn_count["n"] += 1
                return original_thread_start(self)

            monkeypatch.setattr(threading.Thread, "start", _counting_start)
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._run_keyring_call(b.get_password, "svc", "user")
            # The wedge short-circuit message must mention "wedged".
            assert "wedged" in str(exc_info.value).lower(), (
                "backend B's call must short-circuit via the global wedge (TimeoutError message must mention 'wedged')"
            )
            # NO thread must have been spawned for backend B — the wedge
            # short-circuited before the Thread() constructor ran.
            assert b_spawn_count["n"] == 0, (
                "backend B's call must short-circuit (no thread spawned) — "
                "the wedge is global, so a wedge from A covers B too"
            )
        finally:
            done_a1.set()
            done_a2.set()

    def test_cooldown_expiry_retries_backend(self, monkeypatch) -> None:
        """After the cooldown window elapses, the next call must
        actually try the backend (spawn a thread) rather than
        short-circuit.

        NOTE: production code (``credential_store._run_keyring_call``
        lines 188-190) RESETS ``_consecutive_timeouts`` to 0 when the
        cooldown expires. So a single post-cooldown timeout does NOT
        immediately re-wedge — the backend gets a fresh "two strikes"
        count. To observe a re-wedge, we need 2 MORE consecutive
        timeouts after the cooldown expires.

        An earlier draft of this test assumed the consecutive count
        was preserved across cooldown expiry (it wasn't — production
        resets it). The test now reflects the actual production
        behavior.
        """
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done1 = threading.Event()
        done2 = threading.Event()
        done3 = threading.Event()
        done4 = threading.Event()
        # Capture the Events in a list so the inner ``get_password``
        # method can index them by call-number without referencing the
        # local ``doneN`` names (which are NOT in scope inside the
        # method body — Python closures capture variables, not aliases).
        done_events = [done1, done2, done3, done4]

        class _HungBackend:
            name = "HungBackend"

            _call_n = 0

            def get_password(self, service, username):
                _HungBackend._call_n += 1
                # Each of the 4 calls blocks on its own Event so we
                # can release them all deterministically at the end.
                idx = min(_HungBackend._call_n - 1, len(done_events) - 1)
                done_events[idx].wait(timeout=5.0)
                return None

        backend = _HungBackend()

        try:
            # Wedge the backend (2 consecutive timeouts).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._wedged_until > time.monotonic()

            # Manually expire the cooldown by backdating the wedged-until
            # timestamp to the past. Production code resets
            # ``_consecutive_timeouts`` to 0 when it observes the
            # expired wedge on the next call.
            with credential_store._keyring_state_lock:
                credential_store._backend._wedged_until = time.monotonic() - 1.0

            # Next call must NOT short-circuit — it must spawn a thread
            # (and time out for real). Consecutive count goes 0 → 1
            # (NOT re-wedged — only 1 strike after the reset).
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
            # After this single post-cooldown timeout: consecutive=1,
            # NOT wedged (need 2 consecutive to wedge).
            assert credential_store._backend._consecutive_timeouts == 1
            assert credential_store._backend._wedged_until == 0.0, (
                "single post-cooldown timeout must NOT re-wedge "
                "(production resets consecutive count on cooldown expiry)"
            )

            # 2nd post-cooldown timeout — NOW the backend re-wedges
            # (consecutive count: 1 → 2 ≥ 2).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._wedged_until > time.monotonic(), (
                "2nd consecutive post-cooldown timeout must re-wedge the backend"
            )
        finally:
            done1.set()
            done2.set()
            done3.set()
            done4.set()

    def test_warning_logged_when_orphan_count_exceeds_threshold(self, monkeypatch, caplog) -> None:
        """When the orphan count crosses
        ``_KEYRING_ORPHAN_WARN_THRESHOLD`` (default 20), a WARNING
        must be logged so operators have a visible signal before
        resource exhaustion bites."""
        from voice_typer.server import credential_store

        # Lower the timeout + cooldown so the test runs fast. We also
        # lower ``_KEYRING_ORPHAN_WARN_THRESHOLD`` to 1 so we only need
        # 2 timeouts to cross it (the warning fires on the SAME timeout
        # that engages the wedge — both check the incremented
        # ``orphan_count`` in the same critical section, mirroring the
        # pattern in ``test_credential_store_keyring_orphan.py``).
        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(credential_store, "_KEYRING_WEDGE_COOLDOWN_S", 60.0)
        monkeypatch.setattr(credential_store, "_KEYRING_ORPHAN_WARN_THRESHOLD", 1)

        done1 = threading.Event()
        done2 = threading.Event()

        class _HungBackend:
            name = "HungBackend"

            _call_n = 0

            def get_password(self, service, username):
                _HungBackend._call_n += 1
                if _HungBackend._call_n == 1:
                    done1.wait(timeout=5.0)
                else:
                    done2.wait(timeout=5.0)
                return None

        backend = _HungBackend()

        try:
            with caplog.at_level("WARNING", logger="voice_typer.server.credential_store"):
                # Spawn 2 orphans — 1st: orphan_count=1, no threshold log
                # (1 > 1 is False). 2nd: orphan_count=2, threshold log
                # fires (2 > 1 is True). Wedge also engages on the 2nd
                # (both check the incremented orphan_count in the same
                # critical section).
                with pytest.raises(TimeoutError):
                    credential_store._run_keyring_call(backend.get_password, "svc", "user")
                with pytest.raises(TimeoutError):
                    credential_store._run_keyring_call(backend.get_password, "svc", "user")

            warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
            orphan_warnings = [r for r in warning_records if "orphaned" in r.getMessage().lower()]
            assert orphan_warnings, (
                "expected at least one WARNING about orphaned keyring-io thread "
                "count crossing the threshold; got: " + repr([r.getMessage() for r in warning_records])
            )
        finally:
            done1.set()
            done2.set()
