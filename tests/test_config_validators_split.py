"""W1-SA6: regression tests for the ``config_validators`` package split.

This file pins the config_validators split contract so a future refactor cannot
silently regress it:

1. **Allowlist snapshot** — :data:`IPC_CONFIG_ALLOWLIST` must contain
   the same 122 keys with the same per-field validators. The key set
   is a frozen snapshot embedded in this test; the validators are
   checked by identity against the imported ``_VALIDATOR_*``
   instances (so a future change that swaps a validator for a fresh
   instance of the same factory call is still detected — the
   ``_VALIDATOR_*`` constants are the canonical references).
2. **Re-export shim** — every public name in ``__all__`` must resolve
   on the package namespace and point at the same object that the
   new submodules expose (so old import paths keep working).
3. **Public API identity** — ``validate_config`` and
   ``validate_config_update`` must be the SAME function objects the
   new ``entry_points`` submodule defines (no shadow copies).
4. **Monkeypatch surface** — the test patches
   ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
   and expects :func:`validate_config` / :func:`validate_config_update`
   to see the patched binding at call time. This pins the
   package-namespace lookup pattern that the entry-point functions
   use to reference the cross-field helpers.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import voice_typer.server.config_validators as cv
from voice_typer.server.config_validators import (
    IPC_CONFIG_ALLOWLIST,
    allowlist as _al,
    entry_points as _ep,
    validate_config,
    validate_config_update,
)

# Frozen snapshot of the pre-split IPC_CONFIG_ALLOWLIST key set.
# Captured from voice_typer/server/config_validators/__init__.py (862 LOC)
# immediately before the config_validators split.
# Adding or removing a key here is a SECURITY-SENSITIVE change —
# see AGENTS.md §6.3 / CONTRIBUTING.md §6.3 (SEC-002).
_PRE_SPLIT_ALLOWLIST_KEYS: frozenset[str] = frozenset(
    {
        "ai_enhancement_enabled",
        "asr_backend",
        "audio_preset",
        "audio_quality_warnings",
        "auto_capitalize",
        "auto_punctuate",
        "auto_punctuation",
        "autostart",
        "beam_size",
        "best_of",
        "bubble_behavior",
        "bubble_click_to_toggle",
        "bubble_draggable",
        "bubble_mic_button",
        "bubble_position",
        "bubble_scale",
        "bubble_show_on_startup",
        "bubble_x",
        "bubble_y",
        "clipboard_restore_delay_ms",
        "clipboard_save_restore",
        "cloud_api_key",
        "cloud_api_url",
        "cloud_deepgram_consent",
        "cloud_groq_consent",
        "cloud_model",
        "cloud_openai_consent",
        "condition_on_previous_text",
        "crash_recovery_enabled",
        "custom_theme",
        "deepgram_api_key",
        "device",
        "esc_cancel_enabled",
        "fast_startup",
        "fix_grammar_basics",
        "groq_api_key",
        "history_enabled",
        "history_max_entries",
        "history_retention_count",
        "history_retention_days",
        "hotkey",
        "huggingface_consent",
        "language",
        "llm_api_key",
        "llm_api_url",
        "llm_model",
        "llm_polish",
        "llm_polish_consent",
        "llm_preset",
        "log_transcriptions",
        "max_recording_time_seconds",
        "microphone",
        "model_idle_unload_minutes",
        "model_size",
        "noise_filter_compressor",
        "noise_filter_compressor_attack_ms",
        "noise_filter_compressor_output_gain_db",
        "noise_filter_compressor_ratio",
        "noise_filter_compressor_release_ms",
        "noise_filter_compressor_threshold_db",
        "noise_filter_eq",
        "noise_filter_eq_high_db",
        "noise_filter_eq_low_db",
        "noise_filter_eq_mid_db",
        "noise_filter_gate",
        "noise_filter_gate_attack_ms",
        "noise_filter_gate_close_threshold_db",
        "noise_filter_gate_hold_ms",
        "noise_filter_gate_open_threshold_db",
        "noise_filter_gate_release_ms",
        "noise_filter_highpass",
        "noise_filter_highpass_cutoff_hz",
        "noise_filter_limiter",
        "noise_filter_limiter_ceiling_db",
        "noise_filter_limiter_release_ms",
        "noise_filter_notch",
        "noise_filter_notch_frequency_hz",
        "noise_suppression_method",
        "offline_pack_consent",
        "onboarding_completed",
        "openai_api_key",
        "paste_on_stop",
        "pre_roll_buffer_seconds",
        "recording_channels",
        "recording_mode",
        "repaste_hotkey",
        "show_notifications",
        "silence_warning_seconds",
        "sound_feedback_enabled",
        "stop_on_silence_seconds",
        "streaming_chunk_seconds",
        "streaming_left_overlap_seconds",
        "streaming_min_first_chunk_seconds",
        "streaming_right_guard_seconds",
        "streaming_silence_threshold",
        "streaming_step_seconds",
        "streaming_transcription",
        "templates_enabled",
        "test_duration_seconds",
        "text_cleanup_enabled",
        "text_size",
        "theme_mode",
        "theme_preset",
        "tray_left_click_action",
        "trusted_extra_hosts",
        "unsafe_paste_on_unknown_focus",
        "use_silero_vad",
        "vad_auto_calibrate",
        "vad_silence_threshold",
        "vad_speech_threshold",
        "vocabulary_auto_apply_threshold",
        "vocabulary_auto_confidence_threshold",
        "vocabulary_automation_enabled",
        "vocabulary_enabled",
        "voice_biometric_consent",
        "volume_duck_enabled",
        "volume_duck_fade_ms",
        "volume_duck_level",
        "volume_duck_smart_poll_interval_ms",
        "warn_elevated_paste",
        "warn_password_paste",
        "waveform_bubble",
    }
)


class TestAllowlistSnapshot:
    """SEC-002 byte-for-byte parity for ``IPC_CONFIG_ALLOWLIST``."""

    def test_allowlist_size_unchanged(self) -> None:
        """The allowlist must still contain exactly 122 keys."""
        assert len(IPC_CONFIG_ALLOWLIST) == 122, (
            f"IPC_CONFIG_ALLOWLIST size drifted: expected 122, got {len(IPC_CONFIG_ALLOWLIST)}. "
            "SEC-002 contract (AGENTS.md §6.3) — adding/removing keys is a "
            "security-sensitive change that must be reviewed explicitly."
        )

    def test_allowlist_keys_match_frozen_snapshot(self) -> None:
        """The allowlist key set must be byte-identical to the
        pre-split snapshot. Any addition/removal is a SECURITY
        regression per SEC-002 and must be a deliberate, reviewed
        change (not a side-effect of the file split)."""
        actual = frozenset(IPC_CONFIG_ALLOWLIST.keys())
        missing = _PRE_SPLIT_ALLOWLIST_KEYS - actual
        extra = actual - _PRE_SPLIT_ALLOWLIST_KEYS
        assert not missing, (
            f"IPC_CONFIG_ALLOWLIST is missing keys present in the pre-split snapshot: {sorted(missing)}. "
            "SEC-002 allowlist shrunk during the split — non-negotiable regression."
        )
        assert not extra, (
            f"IPC_CONFIG_ALLOWLIST has extra keys not present in the pre-split snapshot: {sorted(extra)}. "
            "SEC-002 allowlist grew during the split — must be reviewed explicitly."
        )

    def test_allowlist_is_same_object_as_submodule(self) -> None:
        """The package-level ``IPC_CONFIG_ALLOWLIST`` must be the SAME
        dict object the new ``allowlist`` submodule defines — the
        package shim re-exports it, never copies it."""
        assert cv.IPC_CONFIG_ALLOWLIST is _al.IPC_CONFIG_ALLOWLIST, (
            "cv.IPC_CONFIG_ALLOWLIST must be the same object as "
            "config_validators.allowlist.IPC_CONFIG_ALLOWLIST — the "
            "package shim must re-export, not copy."
        )

    def test_validators_are_same_object_as_submodule(self) -> None:
        """Each pre-built ``_VALIDATOR_*`` instance referenced in the
        allowlist must be the SAME object the new ``allowlist``
        submodule exposes."""
        for cv_name in (
            "_VALIDATOR_HOTKEY",
            "_VALIDATOR_LANGUAGE",
            "_VALIDATOR_API_KEY",
            "_VALIDATOR_API_URL",
            "_VALIDATOR_LLM_API_URL",
            "_VALIDATOR_LLM_MODEL",
            "_VALIDATOR_REPASTE_HOTKEY",
            "_VALIDATOR_MICROPHONE",
            "_VALIDATOR_PUSH_TO_TALK_HOTKEY",
            "_VALIDATOR_CLOUD_MODEL",
            "_VALIDATOR_TRUSTED_HOSTS",
        ):
            assert getattr(cv, cv_name) is getattr(_al, cv_name), (
                f"cv.{cv_name} must be the same object as allowlist.{cv_name} — "
                "the package shim must re-export, not copy."
            )

    def test_constants_are_same_object_as_submodule(self) -> None:
        """Every supporting constant re-exported through the package
        must be the SAME object the new ``allowlist`` submodule
        defines."""
        for cv_name in (
            "ALLOWED_USER_MODELS",
            "NOISE_SUPPRESSION_METHODS",
            "MAX_RECORDING_TIME_SECONDS_DEFAULT",
            "MAX_RECORDING_TIME_SECONDS_MIN",
            "MAX_RECORDING_TIME_SECONDS_MAX",
            "STREAMING_LEFT_OVERLAP_SECONDS_MIN",
            "STREAMING_RIGHT_GUARD_SECONDS_MIN",
        ):
            assert getattr(cv, cv_name) is getattr(_al, cv_name), (
                f"cv.{cv_name} must be the same object as allowlist.{cv_name} — "
                "the package shim must re-export, not copy."
            )


class TestReExportShim:
    """``__init__.py`` is now a re-export shim — every name in
    ``__all__`` must resolve on the package namespace."""

    def test_all_symbols_resolve(self) -> None:
        """Every name listed in ``cv.__all__`` must be an attribute on
        the package namespace. The pre-split monolith defined these
        directly; the post-split shim re-exports them from the focused
        submodules. A missing re-export would break existing imports
        like ``from voice_typer.server.config_validators import
        _validate_hotkey``."""
        missing = [name for name in cv.__all__ if not hasattr(cv, name)]
        assert not missing, (
            f"cv.__all__ lists symbols that are NOT re-exported by the shim: {missing}. "
            "Existing callers cannot import them — backward-compat regression."
        )

    def test_sys_alias_preserved(self) -> None:
        """``cv._sys`` must still be the ``sys`` module. Tests in
        ``tests/test_hotkey_validation.py`` mutate
        ``cv._sys.platform`` to fake the OS for the reserved-hotkey
        denylist. Removing this attribute breaks those tests."""
        import sys

        assert cv._sys is sys, (
            "cv._sys must be the sys module — tests in "
            "tests/test_hotkey_validation.py mutate cv._sys.platform to "
            "fake the OS for the reserved-hotkey denylist."
        )

    def test_log_attribute_present(self) -> None:
        """The package-level ``log`` attribute (the
        ``logging.getLogger("voice_typer.server.config_validators")``
        instance) must still be present on the package namespace,
        re-exported from ``entry_points``. ``validate_config_update``
        uses it for the unknown-key warning."""
        import logging

        assert isinstance(cv.log, logging.Logger), "cv.log must be a logging.Logger (re-exported from entry_points)."
        assert cv.log.name == "voice_typer.server.config_validators", (
            f"cv.log.name must be 'voice_typer.server.config_validators', got {cv.log.name!r}"
        )


class TestEntryPointIdentity:
    """``validate_config`` and ``validate_config_update`` must be the
    SAME function objects the new ``entry_points`` submodule defines.
    This catches a class of bug where the shim accidentally wraps or
    re-defines the entry points (which would break the test-patch
    surface for ``monkeypatch.setattr(cv, 'validate_config', ...)``)."""

    def test_validate_config_is_entry_points_function(self) -> None:
        assert cv.validate_config is _ep.validate_config
        assert validate_config is _ep.validate_config

    def test_validate_config_update_is_entry_points_function(self) -> None:
        assert cv.validate_config_update is _ep.validate_config_update
        assert validate_config_update is _ep.validate_config_update

    def test_entry_point_module_path(self) -> None:
        assert validate_config.__module__ == "voice_typer.server.config_validators.entry_points"
        assert validate_config_update.__module__ == "voice_typer.server.config_validators.entry_points"


class TestMonkeyPatchSurface:
    """The entry-point functions must look up the cross-field helpers
    via the PACKAGE namespace at call time (lazy import inside the
    function body). This pattern lets tests patch
    ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
    and have the patched binding take effect — the regression test
    suite in ``tests/test_config_validators_hotkey_nonstring.py``
    relies on it."""

    def test_validate_config_sees_patched_cross_field_helper(self) -> None:
        """A patch on the package-level
        ``_check_cross_field_hotkey_conflicts`` must be visible inside
        ``validate_config``. This pins the lazy-import pattern used in
        ``entry_points.validate_config`` (a direct ``from .cross_field
        import _check_cross_field_hotkey_conflicts`` at module top
        would bind a private local that the package-namespace patch
        wouldn't touch — and the regression test would silently
        regress)."""
        cfg = SimpleNamespace(
            hotkey="<caps_lock>",
            repaste_hotkey="<f6>",
            push_to_talk_hotkey="<f7>",
        )
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return ["PATCHED-CROSS-FIELD-ERROR"]

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            errors = validate_config(cfg)

        assert "field_values" in captured, (
            "_check_cross_field_hotkey_conflicts was not invoked via the "
            "package namespace — the entry-point function is binding the "
            "helper at module load time (lazy-import pattern is broken)."
        )
        assert "PATCHED-CROSS-FIELD-ERROR" in errors, (
            "The patched cross-field helper's return value did not propagate "
            "through validate_config — the entry-point function is bypassing "
            "the package-namespace lookup."
        )

    def test_validate_config_update_sees_patched_cross_field_helper(self) -> None:
        """Same monkeypatch surface for ``validate_config_update``."""
        captured: dict = {}

        def _capture(field_values):
            captured["field_values"] = field_values
            return ["PATCHED-CROSS-FIELD-ERROR"]

        with patch(
            "voice_typer.server.config_validators._check_cross_field_hotkey_conflicts",
            side_effect=_capture,
        ):
            _validated, errors = validate_config_update({"hotkey": "<caps_lock>"})

        assert "field_values" in captured, (
            "_check_cross_field_hotkey_conflicts was not invoked via the "
            "package namespace in validate_config_update — lazy-import "
            "pattern is broken."
        )
        assert "PATCHED-CROSS-FIELD-ERROR" in errors, (
            "The patched cross-field helper's return value did not propagate "
            "through validate_config_update — the entry-point function is "
            "bypassing the package-namespace lookup."
        )

    def test_validate_config_sees_patched_cloud_helper(self) -> None:
        """The cloud/LLM consistency check helper must also be patched
        via the package namespace — same lazy-import pattern."""
        captured: dict = {}

        def _capture(cloud_values):
            captured["cloud_values"] = cloud_values
            return ["PATCHED-CLOUD-ERROR"]

        with patch(
            "voice_typer.server.config_validators._check_cross_field_cloud_config",
            side_effect=_capture,
        ):
            errors = validate_config(SimpleNamespace())

        assert "cloud_values" in captured, (
            "_check_cross_field_cloud_config was not invoked via the package "
            "namespace in validate_config — lazy-import pattern is broken."
        )
        assert "PATCHED-CLOUD-ERROR" in errors


class TestSplitCompleteness:
    """The split must be COMPLETE (E15 — debt removal). No body of
    ``IPC_CONFIG_ALLOWLIST``, the ``_VALIDATOR_*`` instances, or the
    two entry-point functions may remain in ``__init__.py``.
    """

    def test_init_py_is_shim_only(self) -> None:
        """``__init__.py`` must NOT contain the ``IPC_CONFIG_ALLOWLIST``
        dict literal — that body has been moved to ``allowlist.py``.
        Reading the source file and checking for the literal is a
        proxy for "the body has been moved, not copied" (E15 — debt
        removal)."""
        from pathlib import Path

        init_path = Path(cv.__file__) if hasattr(cv, "__file__") and cv.__file__ else None
        assert init_path is not None and init_path.exists(), (
            "voice_typer.server.config_validators must be a real package with a discoverable __init__.py path."
        )
        source = init_path.read_text(encoding="utf-8")
        # The allowlist body is the long dict literal whose first entry
        # is the ``"hotkey": (str, _VALIDATOR_HOTKEY)`` line. After the
        # split, the shim only re-imports the name; the body lives in
        # allowlist.py. So the literal should NOT appear in __init__.py.
        assert '"hotkey": (str, _VALIDATOR_HOTKEY)' not in source, (
            "IPC_CONFIG_ALLOWLIST dict body still present in __init__.py — "
            "the split is incomplete (E15 debt removal). Move the body to "
            "voice_typer/server/config_validators/allowlist.py and replace "
            "with a re-export."
        )
        # Likewise the ``def validate_config_update`` body must NOT
        # remain in __init__.py — it now lives in entry_points.py.
        assert "def validate_config_update(" not in source, (
            "validate_config_update function body still present in __init__.py — "
            "the split is incomplete (E15 debt removal). Move the body to "
            "voice_typer/server/config_validators/entry_points.py and "
            "replace with a re-export."
        )
        assert "def validate_config(" not in source, (
            "validate_config function body still present in __init__.py — "
            "the split is incomplete (E15 debt removal). Move the body to "
            "voice_typer/server/config_validators/entry_points.py and "
            "replace with a re-export."
        )

    def test_no_circular_import(self) -> None:
        """Importing the package must succeed without raising
        ``ImportError`` (circular-import regression check). The
        submodules ``allowlist`` and ``entry_points`` must not import
        each other transitively in a way that breaks the package
        load.

        We deliberately avoid ``importlib.reload`` here: reloading a
        submodule re-runs its body and rebinds its module-level names
        to fresh objects, which would break the
        ``cv.IPC_CONFIG_ALLOWLIST is _al.IPC_CONFIG_ALLOWLIST``
        identity guarantee (the package shim still references the
        pre-reload object). The identity guarantee is already pinned
        in ``TestAllowlistSnapshot``; this test only needs to confirm
        that the package + both new submodules import cleanly.
        """
        # Re-importing the package must be a no-op (already in
        # sys.modules) — if the split had introduced a circular import,
        # the original ``import voice_typer.server.config_validators``
        # at the top of this module would already have raised.
        import importlib

        # Both new submodules must be importable on their own (no
        # ordering dependency on the package being partially loaded).
        al_fresh = importlib.import_module("voice_typer.server.config_validators.allowlist")
        ep_fresh = importlib.import_module("voice_typer.server.config_validators.entry_points")
        assert al_fresh is _al
        assert ep_fresh is _ep
        # And the package-level re-exports still point at the same
        # objects the submodules expose.
        assert cv.IPC_CONFIG_ALLOWLIST is _al.IPC_CONFIG_ALLOWLIST
        assert cv.validate_config is _ep.validate_config
        assert cv.validate_config_update is _ep.validate_config_update

    @pytest.mark.parametrize(
        "submodule",
        ["allowlist", "entry_points"],
    )
    def test_submodule_importable_directly(self, submodule: str) -> None:
        """Callers can import the new submodules directly:
        ``from voice_typer.server.config_validators.allowlist import
        IPC_CONFIG_ALLOWLIST`` must work."""
        import importlib

        mod = importlib.import_module(f"voice_typer.server.config_validators.{submodule}")
        assert mod is not None
