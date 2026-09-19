"""RW-9 regression tests for the ``VolumeController`` extraction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.branding import APP_NAME
from voice_typer.server.volume_controller import VolumeController
from voice_typer.server.volume_ducker import DEFAULT_DUCK_LEVEL


class _FakeState:
    """Minimal stand-in for ``VolumeState``, only ``.linear`` is read."""

    def __init__(self, linear: float) -> None:
        self.linear = linear


@pytest.fixture
def fake_app() -> MagicMock:
    """A MagicMock app with ``config`` / ``tray`` / ``_volume_ducker``"""
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


class TestVolumeControllerSurface:
    """``VolumeController`` must expose the three extracted methods."""

    def test_methods_exist_and_are_callable(self, controller: VolumeController):
        for name in ("_on_volume_crash_restore", "_duck_volume", "_restore_volume"):
            assert hasattr(controller, name), f"VolumeController must expose {name}"
            assert callable(getattr(controller, name)), f"{name} must be callable"

    def test_back_reference_is_app(self, fake_app: MagicMock, controller: VolumeController):
        assert controller._app is fake_app, "VolumeController._app must hold the back-reference passed to __init__"


class TestDuckVolume:
    """``_duck_volume`` configures smart-duck + polls + ducks master volume."""

    def test_duck_calls_ducker_with_config_level_and_fade(self, fake_app, controller):
        controller._duck_volume()

        # Smart duck always on ().
        fake_app._volume_ducker.set_smart_duck_enabled.assert_called_once_with(True)
        # Poll interval pulled from config.
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_called_once_with(500)
        # Backend re-initialised each time duck is requested.
        fake_app._volume_ducker.initialize.assert_called_once_with()
        fake_app._volume_ducker.duck.assert_called_once_with(
            level=0.25,
            fade_ms=200,
            per_session=False,
        )

    def test_duck_uses_defaults_when_config_attrs_missing(self, fake_app, controller):
        """If config is missing the optional duck knobs, defaults kick in"""
        del fake_app.config.volume_duck_level
        del fake_app.config.volume_duck_fade_ms
        del fake_app.config.volume_duck_smart_poll_interval_ms

        controller._duck_volume()

        fake_app._volume_ducker.duck.assert_called_once_with(
            level=DEFAULT_DUCK_LEVEL,
            fade_ms=200,
            per_session=False,
        )
        fake_app._volume_ducker.set_smart_duck_poll_interval.assert_called_once_with(500)

    def test_duck_level_fallback_single_sourced(self, fake_app, controller):
        """The missing-config duck-level fallback must be the shared"""
        assert DEFAULT_DUCK_LEVEL == 0.20
        del fake_app.config.volume_duck_level

        controller._duck_volume()

        level_arg = fake_app._volume_ducker.duck.call_args.kwargs["level"]
        assert level_arg is DEFAULT_DUCK_LEVEL, f"duck level fallback must be DEFAULT_DUCK_LEVEL, got {level_arg!r}"

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
        """A ducker exception must not propagate, dictation continues regardless."""
        fake_app._volume_ducker.duck.side_effect = RuntimeError("backend exploded")

        # Must NOT raise.
        controller._duck_volume()

        fake_app._volume_ducker.duck.assert_called_once()

    def test_duck_swallows_exception_from_set_smart_duck(self, fake_app, controller):
        """Even an early-configuration failure must not propagate."""
        fake_app._volume_ducker.set_smart_duck_enabled.side_effect = RuntimeError("nope")

        controller._duck_volume()

        fake_app._volume_ducker.set_smart_duck_enabled.assert_called_once_with(True)
        fake_app._volume_ducker.duck.assert_not_called()


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


class TestOnVolumeCrashRestore:
    """``_on_volume_crash_restore`` notifies the user about a stale"""

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
        """A tray failure must not propagate, crash recovery is best-effort."""
        fake_app.tray.notify.side_effect = RuntimeError("tray not ready")

        # Must NOT raise.
        controller._on_volume_crash_restore(_FakeState(linear=0.5))

        fake_app.tray.notify.assert_called_once()
