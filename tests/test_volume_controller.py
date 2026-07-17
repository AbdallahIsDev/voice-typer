"""RW-9 regression tests for the ``VolumeController`` extraction.

The three volume-side-effect methods (``_on_volume_crash_restore``,
``_duck_volume``, ``_restore_volume``) were extracted from
``VoiceTyperApp`` to ``voice_typer/server/volume_controller.py``.
``VoiceTyperApp`` keeps thin delegate methods so callers
(``RecordingController`` → ``app._duck_volume()``, ``app._do_cleanup``
→ ``self._restore_volume(fade_ms=0)``, and the ``VolumeDucker``
crash-restore callback wired in ``__init__``) keep working unchanged.

These tests pin the contract of the extraction:

1. ``VolumeController`` exposes the three methods and is callable.
2. ``_duck_volume`` configures the ducker (smart-duck + poll interval),
   then calls ``duck(level=…, fade_ms=…, per_session=False)`` using the
   config-provided level / fade_ms.
3. ``_duck_volume`` early-returns when ``config.volume_duck_enabled`` is
   False (no duck call, no smart-duck setup).
4. ``_duck_volume`` does NOT call ``duck()`` when ``initialize()`` returns
   False (backend missing / failed).
5. ``_duck_volume`` swallows exceptions from the ducker (never re-raises
   — dictation must continue even if volume control fails).
6. ``_restore_volume`` calls ``restore(fade_ms=…, per_session=False)``.
7. ``_restore_volume`` uses the configured fade when ``fade_ms`` is None.
8. ``_restore_volume`` passes an explicit ``fade_ms`` straight through
   (used by quit/restart with ``fade_ms=0``).
9. ``_restore_volume`` early-returns when ducking is disabled.
10. ``_restore_volume`` swallows exceptions.
11. ``_on_volume_crash_restore`` notifies the tray with the restored
    percentage.
12. ``_on_volume_crash_restore`` swallows exceptions from ``tray.notify``
    (a notification failure must not crash the app on startup).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.branding import APP_NAME
from voice_typer.server.volume_controller import VolumeController

# ── Helpers ────────────────────────────────────────────────────────────────


class _FakeState:
    """Minimal stand-in for ``VolumeState`` — only ``.linear`` is read."""

    def __init__(self, linear: float) -> None:
        self.linear = linear


@pytest.fixture
def fake_app() -> MagicMock:
    """A MagicMock app with ``config`` / ``tray`` / ``_volume_ducker``
    wired as separate MagicMocks so tests can assert on call_args."""
    app = MagicMock(name="VoiceTyperApp")
    # Default config: ducking enabled, sensible levels.
    app.config.volume_duck_enabled = True
    app.config.volume_duck_level = 0.25
    app.config.volume_duck_fade_ms = 200
    app.config.volume_duck_smart_poll_interval_ms = 500
    # The ducker's initialize() must return True for duck() to fire.
    app._volume_ducker.initialize.return_value = True
    return app


@pytest.fixture
def controller(fake_app: MagicMock) -> VolumeController:
    return VolumeController(fake_app)


# ── 1. Method surface ──────────────────────────────────────────────────────


class TestVolumeControllerSurface:
    """``VolumeController`` must expose the three extracted methods."""

    def test_methods_exist_and_are_callable(self, controller: VolumeController):
        for name in ("_on_volume_crash_restore", "_duck_volume", "_restore_volume"):
            assert hasattr(controller, name), f"VolumeController must expose {name}"
            assert callable(getattr(controller, name)), f"{name} must be callable"

    def test_back_reference_is_app(self, fake_app: MagicMock, controller: VolumeController):
        assert controller._app is fake_app, "VolumeController._app must hold the back-reference passed to __init__"


# ── 2-5. _duck_volume ─────────────────────────────────────────────────────


class TestDuckVolume:
    """``_duck_volume`` configures smart-duck + polls + ducks master volume."""

    def test_duck_calls_ducker_with_config_level_and_fade(self, fake_app, controller):
        controller._duck_volume()

        # Smart duck always on (UX-2).
        fake_app._volume_ducker.set_smart_duck_enabled.assert_called_once_with(True)
        # Poll interval pulled from config.
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_called_once_with(500)
        # Backend re-initialised each time duck is requested.
        fake_app._volume_ducker.initialize.assert_called_once_with()
        # duck() called with the configured level / fade, never per-session.
        fake_app._volume_ducker.duck.assert_called_once_with(
            level=0.25,
            fade_ms=200,
            per_session=False,
        )

    def test_duck_uses_defaults_when_config_attrs_missing(self, fake_app, controller):
        """If config is missing the optional duck knobs, defaults kick in
        (level=0.20, fade_ms=200, poll_interval=500)."""
        del fake_app.config.volume_duck_level
        del fake_app.config.volume_duck_fade_ms
        del fake_app.config.volume_duck_smart_poll_interval_ms

        controller._duck_volume()

        fake_app._volume_ducker.duck.assert_called_once_with(
            level=0.20,
            fade_ms=200,
            per_session=False,
        )
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_called_once_with(500)

    def test_duck_skipped_when_disabled(self, fake_app, controller):
        """``volume_duck_enabled=False`` short-circuits before touching the ducker."""
        fake_app.config.volume_duck_enabled = False

        controller._duck_volume()

        fake_app._volume_ducker.set_smart_duck_enabled.assert_not_called()
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_not_called()
        fake_app._volume_ducker.initialize.assert_not_called()
        fake_app._volume_ducker.duck.assert_not_called()

    def test_duck_skipped_when_initialize_returns_false(self, fake_app, controller):
        """If the backend fails to initialize, ``duck()`` must NOT be called."""
        fake_app._volume_ducker.initialize.return_value = False

        controller._duck_volume()

        # Smart-duck config is still applied (it's a no-op flag on the ducker).
        fake_app._volume_ducker.set_smart_duck_enabled.assert_called_once_with(True)
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_called_once_with(500)
        # But duck() is skipped because initialize() returned False.
        fake_app._volume_ducker.duck.assert_not_called()

    def test_duck_swallows_exceptions(self, fake_app, controller):
        """A ducker exception must not propagate — dictation continues regardless."""
        fake_app._volume_ducker.duck.side_effect = RuntimeError("backend exploded")

        # Must NOT raise.
        controller._duck_volume()

        fake_app._volume_ducker.duck.assert_called_once()

    def test_duck_swallows_exception_from_set_smart_duck(self, fake_app, controller):
        """Even an early-configuration failure must not propagate."""
        fake_app._volume_ducker.set_smart_duck_enabled.side_effect = RuntimeError("nope")

        controller._duck_volume()

        fake_app._volume_ducker.set_smart_duck_enabled.assert_called_once_with(True)
        # initialize() / duck() never reached because the exception short-circuited.
        fake_app._volume_ducker.duck.assert_not_called()


# ── 6-10. _restore_volume ──────────────────────────────────────────────────


class TestRestoreVolume:
    """``_restore_volume`` restores master volume with the right fade."""

    def test_restore_uses_config_fade_when_none(self, fake_app, controller):
        controller._restore_volume()

        fake_app._volume_ducker.restore.assert_called_once_with(
            fade_ms=200,
            per_session=False,
        )

    def test_restore_passes_explicit_fade_ms(self, fake_app, controller):
        """``fade_ms=0`` (quit/restart) must reach the ducker verbatim."""
        controller._restore_volume(fade_ms=0)

        fake_app._volume_ducker.restore.assert_called_once_with(
            fade_ms=0,
            per_session=False,
        )

    def test_restore_uses_default_fade_when_config_attr_missing(self, fake_app, controller):
        del fake_app.config.volume_duck_fade_ms

        controller._restore_volume()

        fake_app._volume_ducker.restore.assert_called_once_with(
            fade_ms=200,
            per_session=False,
        )

    def test_restore_skipped_when_disabled(self, fake_app, controller):
        fake_app.config.volume_duck_enabled = False

        controller._restore_volume(fade_ms=0)

        fake_app._volume_ducker.restore.assert_not_called()

    def test_restore_swallows_exceptions(self, fake_app, controller):
        """A restore failure must not crash the cleanup path."""
        fake_app._volume_ducker.restore.side_effect = RuntimeError("backend gone")

        # Must NOT raise.
        controller._restore_volume(fade_ms=0)

        fake_app._volume_ducker.restore.assert_called_once()


# ── 11-12. _on_volume_crash_restore ────────────────────────────────────────


class TestOnVolumeCrashRestore:
    """``_on_volume_crash_restore`` notifies the user about a stale
    crash-recovery file."""

    def test_notifies_tray_with_percent(self, fake_app, controller):
        state = _FakeState(linear=0.42)

        controller._on_volume_crash_restore(state)

        fake_app.tray.notify.assert_called_once()
        args = fake_app.tray.notify.call_args.args
        kwargs = fake_app.tray.notify.call_args.kwargs
        # First positional arg is APP_NAME (the title).
        assert args[0] == APP_NAME, "tray.notify title must be APP_NAME"
        # Message includes the restored percentage as an int.
        message = args[1] if len(args) > 1 else kwargs.get("message", "")
        assert "42%" in message, f"crash-restore notification must mention the percent; got {message!r}"

    def test_swallows_tray_notify_exception(self, fake_app, controller):
        """A tray failure must not propagate — crash recovery is best-effort."""
        fake_app.tray.notify.side_effect = RuntimeError("tray not ready")

        # Must NOT raise.
        controller._on_volume_crash_restore(_FakeState(linear=0.5))

        fake_app.tray.notify.assert_called_once()
