"""Tests for the three Medium-severity perf/reliability findings:"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


class TestRedactPiiFastPath:
    """``_redact_text`` so callers that don't go through the log filter"""

    def test_redact_pii_returns_input_unchanged_for_no_trigger(self) -> None:
        """Inputs with no ``_FAST_TRIGGER`` match must be returned"""
        from voice_typer.server.security import redact_pii

        # "hello world", no @, no +, no 3+ digits, no Bearer/Token/sk-/
        text = "hello world"
        assert redact_pii(text) == text
        # Same identity check (the fast-path returns the SAME str
        assert redact_pii(text) is text

    def test_redact_pii_matches_redact_text_output(self) -> None:
        """``redact_pii`` delegates to ``_redact_text`` so the two"""
        from voice_typer.server.security import _redact_text, redact_pii

        cases = [
            # No trigger, fast-path for both.
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
            "/usr/local/lib/python3.11/site-packages/voice_typer",
        ]
        for text in cases:
            assert redact_pii(text) == _redact_text(text), (
                f"redact_pii diverged from _redact_text on input: {text!r}\n"
                f"  redact_pii  = {redact_pii(text)!r}\n"
                f"  _redact_text = {_redact_text(text)!r}"
            )

    def test_redact_pii_still_redacts_real_pii(self) -> None:
        """Regression guard: the fast-path delegation must not break"""
        from voice_typer.server.security import redact_pii

        assert "[EMAIL]" in redact_pii("contact john.doe@example.com")
        assert "[PHONE]" in redact_pii("call 555-123-4567")
        assert "[SSN]" in redact_pii("ssn 123-45-6789")
        assert "[CC]" in redact_pii("card 4111-1111-1111-1111")
        # API-key path (redact_secret).
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        assert secret not in redact_pii(f"Using API key: {secret}")


class TestReleaseStuckModifiersGated:
    """rate-limit, paste_enabled, and pynput-availability gates, not"""

    def _make_cm(self, **kwargs):
        from voice_typer.server.clipboard import ClipboardManager

        cm = ClipboardManager(**kwargs)
        # Bypass rate-limit by default, individual tests override.
        cm._last_paste_time = -999.0
        cm._keyboard = MagicMock()
        return cm

    def test_release_not_called_when_rate_limited(self, monkeypatch) -> None:
        """Rate-limited paste must short-circuit BEFORE"""
        from voice_typer.server import clipboard as mod
        from voice_typer.server.clipboard import ClipboardManager

        # Force the rate-limit window to be active (last paste was
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)
        cm = self._make_cm(paste_enabled=True)
        cm._last_paste_time = 100.0  # == now → 0 ms since last paste → rate-limited

        # Spy on _release_stuck_modifiers, if it fires, the test fails.
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
        cm._keyboard.press.assert_not_called()
        cm._keyboard.release.assert_not_called()

    def test_release_not_called_when_paste_disabled(self, monkeypatch) -> None:
        """paste_enabled=False must short-circuit BEFORE"""
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
        """keystroke, ``_release_stuck_modifiers`` MUST still be called"""
        from voice_typer.server import clipboard as mod
        from voice_typer.server.clipboard import ClipboardManager

        # Force the non-Windows, non-macOS keystroke path (Ctrl+V via pynput).
        monkeypatch.setattr(mod, "is_windows", lambda: False)
        monkeypatch.setattr(mod, "is_macos", lambda: False)
        monkeypatch.setattr(mod.time, "monotonic", lambda: 100.0)

        cm = self._make_cm(paste_enabled=True)
        cm._keyboard = MagicMock()
        # Safety check passes, release fires after the gates but before
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


# Module-level state symbols in ``voice_typer.server.credential_store``.


class TestRunKeyringCallWedgedCooldown:
    """``_run_keyring_call`` must bound the orphan-thread leak"""

    @pytest.fixture(autouse=True)
    def _reset_orphan_state_between_tests(self):
        """Clear the orphan / wedged state before AND after each test"""
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
        """Build a fake keyring-like backend whose ``get_password``"""
        done = threading.Event()

        class _HungBackend:
            """A keyring backend whose get_password blocks until"""

            name = "HungBackend"

            def get_password(self, service, username):
                done.wait(timeout=5.0)
                return None

        return _HungBackend(), done

    def test_first_timeout_increments_orphan_count(self, monkeypatch) -> None:
        """The first timeout on a backend must increment the orphan"""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        backend, done = self._make_hung_backend_event()

        try:
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")

            assert credential_store._backend._orphaned_thread_count == 1, (
                "first timeout must bump the orphan count to 1"
            )
            # First timeout must NOT wedge, that requires 2 consecutive
            assert credential_store._backend._wedged_until == 0.0, (
                "first timeout must not wedge the backend (need 2 consecutive)"
            )
            assert credential_store._backend._consecutive_timeouts == 1
        finally:
            done.set()

    def test_second_consecutive_timeout_wedges_backend(self, monkeypatch) -> None:
        """``now + _KEYRING_WEDGE_COOLDOWN_S``)."""
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
            # First timeout, bumps orphan count, no wedge.
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            # Second timeout, wedges the backend (sets ``_wedged_until``).
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
        """The 3rd call (while wedge cooldown is active) must raise"""
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

            # 3rd call while wedged, must short-circuit.
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._run_keyring_call(backend.get_password, "svc", "user")

            # The TimeoutError message must indicate the short-circuit
            assert "wedged" in str(exc_info.value).lower(), (
                f"wedge short-circuit TimeoutError must mention 'wedged'; got: {exc_info.value!s}"
            )
            assert spawn_count["n"] == 0, f"wedge short-circuit must NOT spawn a thread; spawned {spawn_count['n']}"
            # Orphan count stays at 2 (no new orphan from the short-circuit).
            assert credential_store._backend._orphaned_thread_count == 2
        finally:
            done1.set()
            done2.set()

    def test_successful_call_resets_consecutive_timeout_count(self, monkeypatch) -> None:
        """A successful call must reset the consecutive-timeout counter"""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done = threading.Event()

        class _RecoveringBackend:
            """Backend that hangs the first time, succeeds the second,"""

            name = "RecoveringBackend"
            _calls = 0

            def get_password(self, service, username):
                _RecoveringBackend._calls += 1
                if _RecoveringBackend._calls in (1, 3):
                    done.wait(timeout=5.0)  # 1st + 3rd calls hang
                return "recovered-value"

        backend = _RecoveringBackend()

        try:
            # First call, timeout (1st consecutive).
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._consecutive_timeouts == 1

            # Second call, success. Must reset the consecutive count.
            result = credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert result == "recovered-value"
            assert credential_store._backend._consecutive_timeouts == 0, (
                "successful call must reset the consecutive-timeout counter"
            )

            # Third call, timeout again. Consecutive count must be 1
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(backend.get_password, "svc", "user")
            assert credential_store._backend._consecutive_timeouts == 1
            assert credential_store._backend._wedged_until == 0.0, (
                "single timeout after a success must NOT wedge the backend"
            )
        finally:
            done.set()

    def test_wedge_is_global_across_backends(self, monkeypatch) -> None:
        """The production ``_run_keyring_call`` tracks wedge state"""
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
                # B is never actually called, the wedge short-circuits
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
            # NO thread must have been spawned for backend B, the wedge
            assert b_spawn_count["n"] == 0, (
                "backend B's call must short-circuit (no thread spawned), "
                "the wedge is global, so a wedge from A covers B too"
            )
        finally:
            done_a1.set()
            done_a2.set()

    def test_cooldown_expiry_retries_backend(self, monkeypatch) -> None:
        """After the cooldown window elapses, the next call must"""
        from voice_typer.server import credential_store

        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.1)
        done1 = threading.Event()
        done2 = threading.Event()
        done3 = threading.Event()
        done4 = threading.Event()
        # Capture the Events in a list so the inner ``get_password``
        done_events = [done1, done2, done3, done4]

        class _HungBackend:
            name = "HungBackend"

            _call_n = 0

            def get_password(self, service, username):
                _HungBackend._call_n += 1
                # Each of the 4 calls blocks on its own Event so we
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
            with credential_store._keyring_state_lock:
                credential_store._backend._wedged_until = time.monotonic() - 1.0

            # Next call must NOT short-circuit, it must spawn a thread
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
            assert credential_store._backend._consecutive_timeouts == 1
            assert credential_store._backend._wedged_until == 0.0, (
                "single post-cooldown timeout must NOT re-wedge "
                "(production resets consecutive count on cooldown expiry)"
            )

            # 2nd post-cooldown timeout, NOW the backend re-wedges
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
        """When the orphan count crosses"""
        from voice_typer.server import credential_store

        # Lower the timeout + cooldown so the test runs fast. We also
        monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", 0.5)
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
                # Spawn 2 orphans, 1st: orphan_count=1, no threshold log
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
