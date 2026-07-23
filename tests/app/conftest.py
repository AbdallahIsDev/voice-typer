"""CR-25 / CR-60: shared fixtures for tests/app/* (split from test_app.py).

The local ``mock_heavy_imports`` autouse fixture that used to live in
``tests/test_app.py`` was DELETED — its ``force_pynput_hotkey_backend``
branch has been hoisted into the project-wide ``mock_heavy_imports``
fixture in ``tests/conftest.py`` (CR-60). Tests in this directory
inherit that project-wide mock automatically.

XS-92: the local ``tmp_config_dir`` fixture override that previously
lived here was DELETED because it was byte-for-byte identical to the
project-wide ``tmp_config_dir`` fixture in ``tests/conftest.py``. The
project-wide fixture is now picked up automatically. A future change
to the project-wide fixture will propagate to ``tests/app/`` tests
without needing a manual mirror update here.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies."""
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # NEW-PRIV-009 (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings > Privacy before recording).
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is now created in _do_startup (background), not __init__
    # Set a mock transcriber for tests that need it.
    # ARCH-REFAC-003: with the @property delegate removed, assigning to
    # instance.models.transcriber no longer auto-syncs the registry —
    # call _sync_registry_from_fields() so the registry knows about the
    # mock and _start_dictation's ensure_active_engine_loaded() doesn't
    # try to create a fresh TranscriptionEngine.
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    instance.models._sync_registry_from_fields()
    return instance
