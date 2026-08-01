"""None-guard regression tests for lazy-init delegate methods.

The ``undo`` (UndoRepasteController) and ``audio_quality``
(AudioQualityController) properties in ``voice_typer/server/app.py``
are auto-constructing lazy properties: the first access triggers
construction and caches the instance. If construction raises (missing
optional dep, broken state, monkeypatched constructor that raises for
a test), the property logs a warning and returns ``None``.

Five delegate methods in ``VoiceTyperApp`` previously dereferenced the
property's return value directly:

  * ``_on_audio_quality_chunk``      -> ``self.audio_quality._on_audio_quality_chunk(...)``
  * ``_rebuild_audio_processor``     -> ``self.audio_quality._rebuild_audio_processor(...)``
  * ``_finalize_audio_quality_report`` -> ``self.audio_quality._finalize_audio_quality_report(...)``
  * ``repaste_last``                 -> ``self.undo.repaste_last()``
  * ``undo_last``                    -> ``self.undo.undo_last()``

When the lazy property returned ``None`` (because lazy-init failed),
each delegate crashed with ``AttributeError: 'NoneType' object has no
attribute 'X'`` — taking down the audio callback thread (for the
audio_quality delegates) or the hotkey / tray-menu handler (for the
undo delegates).

These tests construct a real ``VoiceTyperApp`` (with the heavy
imports mocked by the autouse ``mock_heavy_imports`` fixture in
``tests/conftest.py``), then monkeypatch the controller constructors
to raise, then call each delegate and assert:

  (a) no ``AttributeError`` is raised,
  (b) the delegate returns ``None`` (matching the existing
      ``log.warning`` + ``return None`` style used by the lazy-init
      properties themselves),
  (c) a warning is emitted on the ``voice_typer.server.app`` logger
      so the silent failure is at least visible in logs.

The tests are intentionally minimal: they cover only the lazy-init
None path (the regression). The happy path (delegate forwards to the
real controller) is already covered by ``tests/app/test_undo_repaste.py``
and ``tests/test_audio_quality_controller.py``.
"""

import logging
from unittest.mock import MagicMock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────
#
# Mirror the fixture style in tests/test_app_cleanup.py so this file
# can run independently. The autouse ``mock_heavy_imports`` fixture
# from tests/conftest.py applies, mocking sounddevice / faster_whisper /
# pynput / pystray / PIL / pyperclip so the tests run headless.


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Point config to a temp directory (so PID file writes are isolated)."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies for None-guard tests.

    Minimal setup — we only need the app instance so we can force the
    lazy-init of ``undo`` / ``audio_quality`` to fail by monkeypatching
    the controller constructors to raise.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    return instance


# ── Helpers ─────────────────────────────────────────────────────────────


def _force_undo_lazy_init_failure(monkeypatch):
    """Monkeypatch ``UndoRepasteController.__init__`` to raise.

    The lazy property ``VoiceTyperApp.undo`` calls
    ``UndoRepasteController(self)`` inside a ``try/except Exception``
    block, so any exception from the constructor causes the property
    to log a warning and return ``None`` — which is the path we want
    to exercise.
    """

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated UndoRepasteController lazy-init failure")

    monkeypatch.setattr("voice_typer.server.app_undo.UndoRepasteController.__init__", _boom)


def _force_audio_quality_lazy_init_failure(monkeypatch):
    """Monkeypatch ``AudioQualityController.__init__`` to raise."""

    def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated AudioQualityController lazy-init failure")

    monkeypatch.setattr(
        "voice_typer.server.audio_quality_controller.AudioQualityController.__init__",
        _boom,
    )


# ── undo / repaste None-guard ──────────────────────────────────────────


class TestUndoNoneGuard:
    """``app.undo_last()`` / ``app.repaste_last()`` must not crash when
    ``self.undo`` returns ``None`` (lazy-init failed).
    """

    def test_undo_last_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``UndoRepasteController(self)`` raises, ``app.undo_last()``
        must return ``None`` and emit a warning instead of crashing with
        ``AttributeError: 'NoneType' object has no attribute 'undo_last'``.
        """
        _force_undo_lazy_init_failure(monkeypatch)

        # Belt-and-suspenders: assert the property really does return None
        # under the monkeypatch — guards against a future refactor that
        # changes the lazy-init contract.
        assert app.undo is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app.undo_last()

        assert result is None
        assert any("undo controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "undo_last() must log a warning when the controller is unavailable"
        )

    def test_repaste_last_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``UndoRepasteController(self)`` raises,
        ``app.repaste_last()`` must return ``None`` and emit a warning
        instead of crashing.
        """
        _force_undo_lazy_init_failure(monkeypatch)
        assert app.undo is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app.repaste_last()

        assert result is None
        assert any("undo controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "repaste_last() must log a warning when the controller is unavailable"
        )

    def test_undo_last_does_not_raise_attribute_error(self, app, monkeypatch):
        """Regression: previously ``app.undo_last()`` raised
        ``AttributeError: 'NoneType' object has no attribute 'undo_last'``
        when ``self.undo`` returned ``None``. This test is the explicit
        assertion form so the regression is named in the test report.
        """
        _force_undo_lazy_init_failure(monkeypatch)

        try:
            app.undo_last()
        except AttributeError as exc:
            pytest.fail(f"undo_last() must not raise AttributeError when the controller is unavailable; got: {exc!r}")
        except Exception as exc:  # pragma: no cover - defensive
            # The implementation may legitimately re-raise other
            # exceptions (e.g. RuntimeError) per the task spec, but
            # AttributeError is the documented regression and MUST NOT
            # occur. Surface any other exception type as a test failure
            # too so we catch silent style drift.
            pytest.fail(
                f"undo_last() raised an unexpected exception type when "
                f"the controller was unavailable; expected None return, "
                f"got {type(exc).__name__}: {exc!r}"
            )


# ── audio_quality None-guard ───────────────────────────────────────────


class TestAudioQualityNoneGuard:
    """``app._on_audio_quality_chunk()`` / ``_rebuild_audio_processor()`` /
    ``_finalize_audio_quality_report()`` must not crash when
    ``self.audio_quality`` returns ``None`` (lazy-init failed).
    """

    def test_on_audio_quality_chunk_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,
        ``app._on_audio_quality_chunk(rms, peak)`` must return ``None``
        and emit a warning instead of crashing.

        This is the most important guard of the five: the delegate is
        called from the real-time audio callback thread, where an
        unhandled ``AttributeError`` would kill the callback thread
        mid-stream and silently halt audio quality monitoring for the
        rest of the session.
        """
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._on_audio_quality_chunk(0.01, 0.05)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records), (
            "_on_audio_quality_chunk() must log a warning when the controller is unavailable"
        )

    def test_rebuild_audio_processor_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,
        ``app._rebuild_audio_processor()`` must return ``None`` and emit
        a warning instead of crashing.
        """
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._rebuild_audio_processor(force_sr=16000)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records)

    def test_finalize_audio_quality_report_returns_none_when_lazy_init_fails(self, app, monkeypatch, caplog):
        """When ``AudioQualityController(self)`` raises,
        ``app._finalize_audio_quality_report(audio)`` must return
        ``None`` and emit a warning instead of crashing.
        """
        _force_audio_quality_lazy_init_failure(monkeypatch)
        assert app.audio_quality is None

        # The ``audio`` arg is annotated ``Any`` precisely so this test
        # can pass a MagicMock without needing numpy installed.
        fake_audio = MagicMock(name="audio_array")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"):
            result = app._finalize_audio_quality_report(fake_audio)

        assert result is None
        assert any("audio_quality controller unavailable" in rec.getMessage() for rec in caplog.records)

    def test_on_audio_quality_chunk_does_not_raise_attribute_error(self, app, monkeypatch):
        """Regression: previously ``app._on_audio_quality_chunk()``
        raised ``AttributeError: 'NoneType' object has no attribute
        '_on_audio_quality_chunk'`` when ``self.audio_quality``
        returned ``None``.
        """
        _force_audio_quality_lazy_init_failure(monkeypatch)

        try:
            app._on_audio_quality_chunk(0.01, 0.05)
        except AttributeError as exc:
            pytest.fail(
                f"_on_audio_quality_chunk() must not raise AttributeError "
                f"when the controller is unavailable; got: {exc!r}"
            )
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(
                f"_on_audio_quality_chunk() raised an unexpected exception "
                f"type when the controller was unavailable; expected None "
                f"return, got {type(exc).__name__}: {exc!r}"
            )


# ── Happy-path sanity check (controller present → delegates forward) ──


class TestHappyPathForwarding:
    """Sanity check: when the lazy property returns a real controller,
    the delegate must still forward the call (i.e. the None-guard did
    not accidentally short-circuit the happy path).

    Uses MagicMock collaborators injected via the property setter (which
    bypasses lazy construction — see the property docstring).
    """

    def test_undo_last_forwards_to_controller(self, app):
        """``app.undo_last()`` must call ``self.undo.undo_last()`` when
        the controller is present.
        """
        fake_undo = MagicMock(name="UndoRepasteController")
        app.undo = fake_undo

        app.undo_last()

        fake_undo.undo_last.assert_called_once_with()

    def test_repaste_last_forwards_to_controller(self, app):
        """``app.repaste_last()`` must call ``self.undo.repaste_last()``
        when the controller is present.
        """
        fake_undo = MagicMock(name="UndoRepasteController")
        app.undo = fake_undo

        app.repaste_last()

        fake_undo.repaste_last.assert_called_once_with()

    def test_on_audio_quality_chunk_forwards_to_controller(self, app):
        """``app._on_audio_quality_chunk(rms, peak)`` must call
        ``self.audio_quality._on_audio_quality_chunk(rms, peak)`` when
        the controller is present.
        """
        fake_aq = MagicMock(name="AudioQualityController")
        app.audio_quality = fake_aq

        app._on_audio_quality_chunk(0.02, 0.08)

        fake_aq._on_audio_quality_chunk.assert_called_once_with(0.02, 0.08)
