"""branch has been hoisted into the project-wide ``mock_heavy_imports``"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp with mocked dependencies."""
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.autostart.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])
    # Mirror tests/fixtures/app_helpers.make_voice_typer_app: on Windows
    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        lambda *a, **k: None,
    )

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # (revised): RecordingController.start() now enforces
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is now created in _do_startup (background), not __init__
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Never duck the developer's REAL system volume. The volume
    instance._volume_ducker = MagicMock()
    instance._volume_ducker.initialize.return_value = False
    yield instance
    import contextlib

    # Close the real HistoryDB so its writer / periodic-retention /
    with contextlib.suppress(Exception):
        if instance.history_db is not None:
            instance.history_db.close()
    # Cancel the tray elapsed-recording timer worker thread (a real
    with contextlib.suppress(Exception):
        instance.tray._cancel_elapsed_timer()
    loader = getattr(instance.models, "_model_load_thread", None)
    if loader is not None and loader.is_alive():
        loader.join(timeout=2.0)
