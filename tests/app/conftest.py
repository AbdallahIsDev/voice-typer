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
    """Create a VoiceTyperApp with mocked dependencies.

    WR-2: yield-style fixture so the background ``_model_load_thread``
    is joined in teardown — previously it was leaked across test
    boundaries (the thread kept running after the test finished,
    occasionally touching the now-torn-down VoiceTyperApp and causing
    flaky failures in later tests).
    """
    monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    # Ensure esc_cancel_enabled is False for deterministic test behavior
    instance.config.esc_cancel_enabled = False
    # (revised): RecordingController.start() now enforces
    # voice_biometric_consent before capturing audio. Tests that exercise
    # the recording path must explicitly opt in (just like real users
    # must enable the toggle in Settings > Privacy before recording).
    instance.config.voice_biometric_consent = True
    # TranscriptionEngine is now created in _do_startup (background), not __init__
    # Set a mock transcriber for tests that need it.
    # The ``transcriber`` attribute is a @property whose setter delegates
    # to ``self._registry.register("whisper", ...)`` — so this assignment
    # keeps the registry in sync automatically and ensure_active_engine_loaded()
    # won't try to create a fresh TranscriptionEngine.
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    # Never duck the developer's REAL system volume. The volume
    # backend is also global-mocked (``mock_heavy_imports_session``),
    # but an inert ducker here keeps dictation tests from touching the
    # hardware even if a test re-exposes ``get_volume_backend``.
    instance._volume_ducker = MagicMock()
    instance._volume_ducker.initialize.return_value = False
    yield instance
    import contextlib

    # Close the real HistoryDB so its writer / periodic-retention /
    # reader-prune daemon threads are quiesced instead of leaking into
    # the xdist worker for the rest of the suite. ``close()`` is
    # idempotent; tests that swapped ``history_db`` for a MagicMock
    # (e.g. ``_stub_restart_environment`` in tests/test_app_restart.py)
    # make this a harmless no-op call. See the conftest
    # ``_drain_crash_recovery_workers`` fixture for the same pattern.
    with contextlib.suppress(Exception):
        if instance.history_db is not None:
            instance.history_db.close()
    # Cancel the tray elapsed-recording timer worker thread (a real
    # ``tray_elapsed_timer`` daemon thread per recording session) if the
    # test started one. Defensive — ``tray`` may be a MagicMock.
    with contextlib.suppress(Exception):
        instance.tray._cancel_elapsed_timer()
    # join the background model-load thread so it doesn't outlive
    # the test and touch a torn-down VoiceTyperApp. Best-effort — if the
    # thread is None or already finished, the join is a no-op.
    loader = getattr(instance.models, "_model_load_thread", None)
    if loader is not None and loader.is_alive():
        loader.join(timeout=2.0)
