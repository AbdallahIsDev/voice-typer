"""focused tests for ``MicrophoneDeviceWatcher.set_idle()``.

the watcher had dead ``_is_idle`` / ``_idle_poll_interval_s`` /
``_active_poll_interval_s`` state — the attributes were initialised in
``__init__`` but never read by any code path, so the idle/active cadence
selection never actually happened. The macOS polling path hardcoded a
3 s cadence; the Linux secondary ``sd.query_devices()`` poll hardcoded
a 5 s cadence (``_LINUX_SD_QUERY_INTERVAL_S``).

Post-fix : ``set_idle(bool)`` is a public method that toggles
``self._is_idle``, and both ``_run_macos`` and the ``_run_linux``
secondary poll select between ``_idle_poll_interval_s`` (default 12 s)
and ``_active_poll_interval_s`` (default 3 s) based on ``self._is_idle``.

These tests pin the contract:

1. ``set_idle(True)`` / ``set_idle(False)`` mutate ``self._is_idle``.
2. The default state is ``True`` (the app launches idle).
3. ``_run_macos`` selects ``_idle_poll_interval_s`` when idle and
   ``_active_poll_interval_s`` when not — verified by inspecting the
   source (the cadence selection is inside a ``while`` loop that's
   hard to drive deterministically without a real CoreAudio round
   trip, so the source-guard pattern mirrors
   ``test_load_active_source_contains_is_disabled_check``).
4. The ``_run_linux`` secondary poll selects ``_idle_poll_interval_s``
   when idle and ``_active_poll_interval_s`` when not — same
   source-guard pattern.
5. ``set_idle`` accepts truthy/falsy values (not just strict bool).
6. ``set_idle`` is idempotent — calling it twice with the same value
   is a no-op.
"""

from __future__ import annotations

import inspect

from voice_typer.server.microphone_watcher import MicrophoneDeviceWatcher


class TestSetIdle:
    """``set_idle(bool)`` wires up the formerly-dead idle state."""

    def test_set_idle_true_sets_is_idle_true(self):
        """``set_idle(True)`` must set ``self._is_idle = True``."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher._is_idle = False  # start from non-idle
        watcher.set_idle(True)
        assert watcher._is_idle is True, (
            "set_idle(True) must set self._is_idle = True so the macOS/Linux polling paths widen to the idle cadence."
        )

    def test_set_idle_false_sets_is_idle_false(self):
        """``set_idle(False)`` must set ``self._is_idle = False``."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher.set_idle(False)
        assert watcher._is_idle is False, (
            "set_idle(False) must set self._is_idle = False so the "
            "macOS/Linux polling paths tighten to the active cadence."
        )

    def test_default_is_idle_is_true(self):
        """The watcher must launch in the idle state (no recording active)."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert watcher._is_idle is True, (
            "the default _is_idle must be True — the app launches "
            "idle (no recording in flight). Pre-fix, the attribute existed "
            "but was never read by any code path."
        )

    def test_set_idle_accepts_truthy_falsy(self):
        """``set_idle`` must coerce truthy/falsy values to bool (not
        require a strict ``True``/``False`` argument)."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher.set_idle(0)
        assert watcher._is_idle is False
        watcher.set_idle(1)
        assert watcher._is_idle is True
        watcher.set_idle(None)
        assert watcher._is_idle is False
        watcher.set_idle("recording")
        assert watcher._is_idle is True

    def test_set_idle_is_idempotent(self):
        """Calling ``set_idle`` twice with the same value must be a
        no-op (the attribute stays the same, no error)."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        watcher.set_idle(False)
        watcher.set_idle(False)
        assert watcher._is_idle is False
        watcher.set_idle(True)
        watcher.set_idle(True)
        assert watcher._is_idle is True

    def test_idle_and_active_intervals_have_expected_defaults(self):
        """The default intervals must be 12 s (idle) and 3 s (active)
        — the values documented in the ``set_idle`` docstring."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert watcher._idle_poll_interval_s == 12.0, "the default idle poll interval must be 12.0 s."
        assert watcher._active_poll_interval_s == 3.0, "the default active poll interval must be 3.0 s."


class TestRunMacosIdleCadence:
    """``_run_macos`` must select between idle/active cadences."""

    def test_run_macos_source_references_is_idle(self):
        """``_run_macos``'s source must reference ``self._is_idle`` so
        the idle/active cadence selection actually happens (pre-fix,
        ``_is_idle`` was dead state — never read)."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_macos)
        assert "_is_idle" in src, (
            "_run_macos must read self._is_idle to select between "
            "the idle (12 s) and active (3 s) poll cadences. Pre-fix, "
            "the macOS path hardcoded 3.0 and never consulted _is_idle."
        )

    def test_run_macos_source_references_idle_poll_interval(self):
        """``_run_macos``'s source must reference
        ``_idle_poll_interval_s`` so the idle cadence is actually used."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_macos)
        assert "_idle_poll_interval_s" in src, "_run_macos must use self._idle_poll_interval_s for the idle cadence."

    def test_run_macos_source_references_active_poll_interval(self):
        """``_run_macos``'s source must reference
        ``_active_poll_interval_s`` so the active cadence is actually used."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_macos)
        assert "_active_poll_interval_s" in src, (
            "_run_macos must use self._active_poll_interval_s for the active cadence."
        )


class TestRunLinuxSecondaryPollIdleCadence:
    """the ``_run_linux`` secondary poll must select between
    idle/active cadences."""

    def test_run_linux_source_references_is_idle(self):
        """``_run_linux``'s source must reference ``self._is_idle`` so
        the secondary ``sd.query_devices()`` poll selects between the
        idle (12 s) and active (3 s) cadences."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_linux)
        assert "_is_idle" in src, (
            "_run_linux must read self._is_idle to select between "
            "the idle (12 s) and active (3 s) secondary poll cadences. "
            "Pre-fix, the secondary poll hardcoded _LINUX_SD_QUERY_INTERVAL_S "
            "(5 s) and never consulted _is_idle."
        )

    def test_run_linux_source_references_idle_poll_interval(self):
        """``_run_linux``'s source must reference
        ``_idle_poll_interval_s`` so the idle cadence is actually used."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_linux)
        assert "_idle_poll_interval_s" in src, (
            "_run_linux must use self._idle_poll_interval_s for the secondary poll idle cadence."
        )

    def test_run_linux_source_references_active_poll_interval(self):
        """``_run_linux``'s source must reference
        ``_active_poll_interval_s`` so the active cadence is actually used."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_linux)
        assert "_active_poll_interval_s" in src, (
            "_run_linux must use self._active_poll_interval_s for the secondary poll active cadence."
        )

    def test_run_linux_does_not_reference_dead_constant(self):
        """``_run_linux``'s source must NOT reference the former
        ``_LINUX_SD_QUERY_INTERVAL_S`` constant — it was replaced by the
        idle/active cadence selection. If a future refactor reintroduces
        the dead constant, this test catches it."""
        src = inspect.getsource(MicrophoneDeviceWatcher._run_linux)
        assert "_LINUX_SD_QUERY_INTERVAL_S" not in src, (
            "_run_linux must NOT reference _LINUX_SD_QUERY_INTERVAL_S "
            "— the secondary poll cadence is now selected via _is_idle "
            "(idle=12 s, active=3 s). The former 5 s constant is dead state."
        )


class TestSetIdleIsPublic:
    """``set_idle`` must be a public method (callable by
    ``RecordingController`` without underscore-prefix access)."""

    def test_set_idle_is_callable(self):
        """``set_idle`` must be a callable method on the watcher instance."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        assert callable(getattr(watcher, "set_idle", None)), (
            "set_idle must be a public method so RecordingController can call it when a recording starts/stops."
        )

    def test_set_idle_does_not_raise(self):
        """``set_idle`` must not raise on any bool input."""
        watcher = MicrophoneDeviceWatcher(on_change=lambda: None)
        # Must NOT raise.
        watcher.set_idle(True)
        watcher.set_idle(False)
        watcher.set_idle(True)
