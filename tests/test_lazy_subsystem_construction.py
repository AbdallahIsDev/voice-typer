"""Lazy subsystem construction regression tests.

Pins the god-constructor deferral work on ``VoiceTyperApp.__init__``:
non-critical subsystems must NOT be eagerly constructed on the main
thread before the tray icon appears. Two flavours of lazy property
are verified:

1. PASSIVE accessors (``_template_manager`` / ``_vocabulary_manager``):
   the property getter returns the backing as-is (``None`` initially).
   Construction happens in the lazy-fallback paths in
   ``service/template.py`` and ``dictation_pipeline.py`` (which check
   ``is None``, construct, and assign via the setter). An
   auto-constructing getter would silently bypass those callers'
   ``is None`` checks.

2. AUTO-CONSTRUCTING accessors (``undo`` / ``audio_quality`` /
   ``_duck_crash_recovery`` / ``_volume_ducker``): the property
   getter constructs on first access if the backing is ``None`` and
   caches the instance. These subsystems are always accessed via
   method calls (e.g. ``app.undo.undo_last()``), never via
   ``is None`` checks, so auto-construction is transparent.

Both flavours expose a setter so existing tests that inject mocks
via ``app.<attr> = MagicMock()`` keep working transparently.

These tests run on the Linux sandbox — they don't require
sounddevice, torch, or a display server. The autouse
``mock_heavy_imports`` fixture in ``tests/conftest.py`` stubs the
hardware-touching modules.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _patch_app_platform_helpers(monkeypatch):
    """Patch the platform helpers that ``VoiceTyperApp.__init__`` touches.

    Mirrors the helper in ``tests/test_startup_perf.py``. The helpers are
    resolved at call time from their canonical home
    ``voice_typer.server.server_platform`` (deferred imports inside
    ``startup_tasks.sync_autostart`` / ``load_microphones``), so that is
    the module to patch.
    """
    from voice_typer.server.server_platform import autostart as autostart_mod

    monkeypatch.setattr(autostart_mod, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(autostart_mod, "enable_autostart", lambda: True)
    monkeypatch.setattr(autostart_mod, "disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.server_platform.microphone_list.list_microphones", lambda: [])


# ─── PASSIVE accessors: _template_manager / _vocabulary_manager ────────


class TestPassiveManagerProperties:
    """``_template_manager`` / ``_vocabulary_manager`` are PASSIVE —
    the getter returns the backing as-is (``None`` initially) and
    never auto-constructs. Construction is the responsibility of the
    lazy-fallback paths in ``service/template.py`` /
    ``dictation_pipeline.py`` (which check ``is None``, construct,
    and assign via the setter).
    """

    def test_template_manager_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """Accessing ``app._template_manager`` immediately after
        ``__init__`` must return ``None`` — the property is passive
        and does NOT auto-construct on access.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._template_manager is None, (
            "Passive accessor: _template_manager must be None "
            "immediately after __init__ (no auto-construction on access)."
        )

    def test_vocabulary_manager_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """Accessing ``app._vocabulary_manager`` immediately after
        ``__init__`` must return ``None`` — the property is passive.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._vocabulary_manager is None, (
            "Passive accessor: _vocabulary_manager must be None "
            "immediately after __init__ (no auto-construction on access)."
        )

    def test_template_manager_setter_round_trip(self, tmp_config_dir, monkeypatch):
        """The setter stores into the backing; a subsequent getter
        call returns the stored value (no construction).
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        sentinel = MagicMock(name="fake_template_manager")
        instance._template_manager = sentinel
        assert instance._template_manager is sentinel, (
            "Setter must store into backing; getter must return the "
            "stored value (no construction on access after a set)."
        )

    def test_vocabulary_manager_setter_round_trip(self, tmp_config_dir, monkeypatch):
        """The setter stores into the backing; a subsequent getter
        call returns the stored value (no construction).
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        sentinel = MagicMock(name="fake_vocabulary_manager")
        instance._vocabulary_manager = sentinel
        assert instance._vocabulary_manager is sentinel


# ─── AUTO-CONSTRUCTING accessors: undo / audio_quality /
#     _duck_crash_recovery / _volume_ducker ────────────────────────────


class TestAutoConstructingControllerProperties:
    """``undo`` / ``audio_quality`` / ``_duck_crash_recovery`` /
    ``_volume_ducker`` are AUTO-CONSTRUCTING — the getter constructs
    on first access if the backing is ``None`` and caches the instance.

    The backings must start as ``None`` (no eager construction in
    ``__init__``). First access triggers construction. Subsequent
    accesses return the cached instance. The setter bypasses
    construction (used by tests that inject mocks).
    """

    def test_undo_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_undo_backing`` must be ``None`` after ``__init__`` —
        UndoRepasteController is NOT eagerly constructed.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._undo_backing is None, (
            "UndoRepasteController must NOT be eagerly constructed in __init__; _undo_backing should start as None."
        )

    def test_audio_quality_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_audio_quality_backing`` must be ``None`` after ``__init__`` —
        AudioQualityController is NOT eagerly constructed.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._audio_quality_backing is None, (
            "AudioQualityController must NOT be eagerly constructed in "
            "__init__; _audio_quality_backing should start as None."
        )

    def test_duck_crash_recovery_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_duck_crash_recovery_backing`` must be ``None`` after
        ``__init__`` — DuckCrashRecovery is NOT eagerly constructed.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._duck_crash_recovery_backing is None, (
            "DuckCrashRecovery must NOT be eagerly constructed in "
            "__init__; _duck_crash_recovery_backing should start as None."
        )

    def test_volume_ducker_backing_is_none_after_init(self, tmp_config_dir, monkeypatch):
        """``_volume_ducker_backing`` must be ``None`` after ``__init__`` —
        VolumeDucker is NOT eagerly constructed.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        assert instance._volume_ducker_backing is None, (
            "VolumeDucker must NOT be eagerly constructed in __init__; _volume_ducker_backing should start as None."
        )


class TestAutoConstructOnAccess:
    """First access of an auto-constructing property triggers
    construction and caches the instance in the backing.
    """

    def test_undo_constructs_on_first_access(self, tmp_config_dir, monkeypatch):
        """Accessing ``app.undo`` constructs an UndoRepasteController
        and caches it in ``_undo_backing``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.app_undo import UndoRepasteController

        instance = VoiceTyperApp()
        assert instance._undo_backing is None
        controller = instance.undo
        assert isinstance(controller, UndoRepasteController), (
            "First access to app.undo must construct an UndoRepasteController."
        )
        # Cached: a second access returns the same instance.
        assert instance.undo is controller
        assert instance._undo_backing is controller, (
            "First access must cache the constructed instance in _undo_backing."
        )

    def test_audio_quality_constructs_on_first_access(self, tmp_config_dir, monkeypatch):
        """Accessing ``app.audio_quality`` constructs an
        AudioQualityController and caches it.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.audio_quality_controller import (
            AudioQualityController,
        )

        instance = VoiceTyperApp()
        assert instance._audio_quality_backing is None
        controller = instance.audio_quality
        assert isinstance(controller, AudioQualityController), (
            "First access to app.audio_quality must construct an AudioQualityController."
        )
        assert instance.audio_quality is controller
        assert instance._audio_quality_backing is controller

    def test_duck_crash_recovery_constructs_on_first_access(self, tmp_config_dir, monkeypatch):
        """Accessing ``app._duck_crash_recovery`` constructs a
        DuckCrashRecovery and caches it.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

        instance = VoiceTyperApp()
        assert instance._duck_crash_recovery_backing is None
        recovery = instance._duck_crash_recovery
        assert isinstance(recovery, DuckCrashRecovery), (
            "First access to app._duck_crash_recovery must construct a DuckCrashRecovery."
        )
        assert instance._duck_crash_recovery is recovery
        assert instance._duck_crash_recovery_backing is recovery

    def test_volume_ducker_constructs_on_first_access(self, tmp_config_dir, monkeypatch):
        """Accessing ``app._volume_ducker`` constructs a VolumeDucker
        (wired to the lazy ``_duck_crash_recovery`` and the
        ``_on_volume_crash_restore`` callback) and caches it.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.volume_ducker import VolumeDucker

        instance = VoiceTyperApp()
        assert instance._volume_ducker_backing is None
        ducker = instance._volume_ducker
        assert isinstance(ducker, VolumeDucker), "First access to app._volume_ducker must construct a VolumeDucker."
        assert instance._volume_ducker is ducker
        assert instance._volume_ducker_backing is ducker
        # The VolumeDucker must be wired to the lazy DuckCrashRecovery
        # (accessing _duck_crash_recovery triggered construction as a
        # side effect of the VolumeDucker constructor call).
        assert instance._duck_crash_recovery_backing is not None, (
            "Constructing VolumeDucker must trigger lazy construction of "
            "DuckCrashRecovery (the ducker's crash_recovery arg)."
        )
        assert ducker._crash_recovery is instance._duck_crash_recovery

    def test_setter_bypasses_construction(self, tmp_config_dir, monkeypatch):
        """Assigning via the setter stores directly into the backing —
        a subsequent getter call returns the assigned value without
        invoking the lazy constructor. This is the contract tests
        rely on when they inject mocks via ``app.<attr> = MagicMock()``.
        """
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server.app import VoiceTyperApp

        instance = VoiceTyperApp()
        # Each property: assign a sentinel via the setter, then verify
        # the getter returns the sentinel (no construction).
        for attr, backing in (
            ("undo", "_undo_backing"),
            ("audio_quality", "_audio_quality_backing"),
            ("_duck_crash_recovery", "_duck_crash_recovery_backing"),
            ("_volume_ducker", "_volume_ducker_backing"),
        ):
            sentinel = MagicMock(name=f"fake_{attr}")
            setattr(instance, attr, sentinel)
            assert getattr(instance, attr) is sentinel, (
                f"Setter for {attr} must store into the backing; getter "
                f"must return the assigned sentinel (no construction)."
            )
            assert getattr(instance, backing) is sentinel


class TestNoEagerConstructionInInit:
    """``VoiceTyperApp.__init__`` must NOT eagerly construct the four
    deferred controllers. We verify by counting constructor calls.
    """

    def test_undo_repaste_controller_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import app_undo as app_undo_mod

        construct_count = {"n": 0}
        real_cls = app_undo_mod.UndoRepasteController

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_cls(*args, **kwargs)

        monkeypatch.setattr(app_undo_mod, "UndoRepasteController", _counting_ctor)
        from voice_typer.server.app import VoiceTyperApp

        VoiceTyperApp()
        assert construct_count["n"] == 0, (
            "VoiceTyperApp.__init__ must NOT eagerly construct "
            f"UndoRepasteController (got {construct_count['n']} call(s))."
        )

    def test_audio_quality_controller_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import audio_quality_controller as aqc_mod

        construct_count = {"n": 0}
        real_cls = aqc_mod.AudioQualityController

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_cls(*args, **kwargs)

        monkeypatch.setattr(aqc_mod, "AudioQualityController", _counting_ctor)
        from voice_typer.server.app import VoiceTyperApp

        VoiceTyperApp()
        assert construct_count["n"] == 0, (
            "VoiceTyperApp.__init__ must NOT eagerly construct "
            f"AudioQualityController (got {construct_count['n']} call(s))."
        )

    def test_duck_crash_recovery_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import duck_crash_recovery as dcr_mod

        construct_count = {"n": 0}
        real_cls = dcr_mod.DuckCrashRecovery

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_cls(*args, **kwargs)

        monkeypatch.setattr(dcr_mod, "DuckCrashRecovery", _counting_ctor)
        from voice_typer.server.app import VoiceTyperApp

        VoiceTyperApp()
        assert construct_count["n"] == 0, (
            f"VoiceTyperApp.__init__ must NOT eagerly construct DuckCrashRecovery (got {construct_count['n']} call(s))."
        )

    def test_volume_ducker_not_constructed_in_init(self, tmp_config_dir, monkeypatch):
        _patch_app_platform_helpers(monkeypatch)
        from voice_typer.server import volume_ducker as vd_mod

        construct_count = {"n": 0}
        real_cls = vd_mod.VolumeDucker

        def _counting_ctor(*args, **kwargs):
            construct_count["n"] += 1
            return real_cls(*args, **kwargs)

        monkeypatch.setattr(vd_mod, "VolumeDucker", _counting_ctor)
        from voice_typer.server.app import VoiceTyperApp

        VoiceTyperApp()
        assert construct_count["n"] == 0, (
            f"VoiceTyperApp.__init__ must NOT eagerly construct VolumeDucker (got {construct_count['n']} call(s))."
        )
