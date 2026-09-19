"""Pure-dict coercion + path-validation helpers extracted from ``config.py``."""

import logging
import os
from pathlib import Path
from typing import Any

from voice_typer.server.config_internals.paths import _is_path_within
from voice_typer.server.config_validators import (
    ALLOWED_USER_MODELS,
    MAX_RECORDING_TIME_SECONDS_DEFAULT,
    MAX_RECORDING_TIME_SECONDS_MAX,
    MAX_RECORDING_TIME_SECONDS_MIN,
    STREAMING_LEFT_OVERLAP_SECONDS_MIN,
    STREAMING_RIGHT_GUARD_SECONDS_MIN,
)
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE, NO_MODEL_SIZE

log = logging.getLogger("voice_typer.server.config")


def _get_config_dir() -> Path:
    """Lazy lookup of ``_config_dir`` via the parent ``config`` module."""
    from voice_typer.server import config as _cfg

    return _cfg._config_dir()


def _coerce_streaming_fields(data: dict[str, Any]) -> None:
    """AudioWindowPlanner doesn't run forever or produce overlapping"""
    # Config fields were renamed (no migration needed):
    try:
        _left_overlap_raw = float(data.get("streaming_left_overlap_seconds", STREAMING_LEFT_OVERLAP_SECONDS_MIN))
    except (TypeError, ValueError):
        _left_overlap_invalid = data.get("streaming_left_overlap_seconds")
        log.warning(
            "[CONFIG] invalid streaming_left_overlap_seconds value %r; resetting to default %.1f",
            _left_overlap_invalid,
            STREAMING_LEFT_OVERLAP_SECONDS_MIN,
        )
        data["streaming_left_overlap_seconds"] = STREAMING_LEFT_OVERLAP_SECONDS_MIN
        data.setdefault("_load_warnings", []).append(
            f"streaming_left_overlap_seconds had non-numeric value "
            f"{_left_overlap_invalid!r}, reset to "
            f"{STREAMING_LEFT_OVERLAP_SECONDS_MIN}"
        )
        _left_overlap_raw = STREAMING_LEFT_OVERLAP_SECONDS_MIN
    else:
        if _left_overlap_raw < STREAMING_LEFT_OVERLAP_SECONDS_MIN:
            log.warning(
                "[CONFIG] streaming_left_overlap_seconds=%.3f below minimum %.1f; "
                "resetting to %.1f (was silently clamped pre-fix)",
                _left_overlap_raw,
                STREAMING_LEFT_OVERLAP_SECONDS_MIN,
                STREAMING_LEFT_OVERLAP_SECONDS_MIN,
            )
            data.setdefault("_load_warnings", []).append(
                f"streaming_left_overlap_seconds={_left_overlap_raw} below minimum "
                f"{STREAMING_LEFT_OVERLAP_SECONDS_MIN}, reset to "
                f"{STREAMING_LEFT_OVERLAP_SECONDS_MIN}"
            )
            _left_overlap_raw = STREAMING_LEFT_OVERLAP_SECONDS_MIN
        data["streaming_left_overlap_seconds"] = _left_overlap_raw
    try:
        _right_guard_raw = float(data.get("streaming_right_guard_seconds", STREAMING_RIGHT_GUARD_SECONDS_MIN))
    except (TypeError, ValueError):
        _right_guard_invalid = data.get("streaming_right_guard_seconds")
        log.warning(
            "[CONFIG] invalid streaming_right_guard_seconds value %r; resetting to default %.1f",
            _right_guard_invalid,
            STREAMING_RIGHT_GUARD_SECONDS_MIN,
        )
        data["streaming_right_guard_seconds"] = STREAMING_RIGHT_GUARD_SECONDS_MIN
        data.setdefault("_load_warnings", []).append(
            f"streaming_right_guard_seconds had non-numeric value "
            f"{_right_guard_invalid!r}, reset to "
            f"{STREAMING_RIGHT_GUARD_SECONDS_MIN}"
        )
        _right_guard_raw = STREAMING_RIGHT_GUARD_SECONDS_MIN
    else:
        if _right_guard_raw < STREAMING_RIGHT_GUARD_SECONDS_MIN:
            log.warning(
                "[CONFIG] streaming_right_guard_seconds=%.3f below minimum %.1f; "
                "resetting to %.1f (was silently clamped pre-fix)",
                _right_guard_raw,
                STREAMING_RIGHT_GUARD_SECONDS_MIN,
                STREAMING_RIGHT_GUARD_SECONDS_MIN,
            )
            data.setdefault("_load_warnings", []).append(
                f"streaming_right_guard_seconds={_right_guard_raw} below minimum "
                f"{STREAMING_RIGHT_GUARD_SECONDS_MIN}, reset to "
                f"{STREAMING_RIGHT_GUARD_SECONDS_MIN}"
            )
            _right_guard_raw = STREAMING_RIGHT_GUARD_SECONDS_MIN
        data["streaming_right_guard_seconds"] = _right_guard_raw
    # enforce streaming config invariants so the
    try:
        chunk = float(data.get("streaming_chunk_seconds", 12.0))
    except (TypeError, ValueError):
        log.warning(
            "[CONFIG] invalid streaming_chunk_seconds value %r; resetting to default 12.0",
            data.get("streaming_chunk_seconds"),
        )
        chunk = 12.0
        data["streaming_chunk_seconds"] = 12.0
    try:
        step = float(data.get("streaming_step_seconds", 5.0))
    except (TypeError, ValueError):
        log.warning(
            "[CONFIG] invalid streaming_step_seconds value %r; resetting to default 5.0",
            data.get("streaming_step_seconds"),
        )
        step = 5.0
        data["streaming_step_seconds"] = 5.0
    # Block 1 already validated, clamped, and stored
    left_overlap = float(data["streaming_left_overlap_seconds"])
    if step >= chunk:
        log.warning(
            "[CONFIG] streaming_step_seconds (%.1f) >= streaming_chunk_seconds (%.1f); clamping step to chunk/2",
            step,
            chunk,
        )
        data["streaming_step_seconds"] = chunk / 2.0
    if left_overlap >= chunk:
        log.warning(
            "[CONFIG] streaming_left_overlap_seconds (%.1f) >= streaming_chunk_seconds "
            "(%.1f); clamping overlap to chunk/3",
            left_overlap,
            chunk,
        )
        data["streaming_left_overlap_seconds"] = chunk / 3.0


def _coerce_max_recording_time(data: dict[str, Any]) -> None:
    """Clamp ``max_recording_time_seconds`` to valid range [300, 3600]."""
    try:
        max_rec = int(data.get("max_recording_time_seconds", MAX_RECORDING_TIME_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        log.warning(
            "[CONFIG] invalid max_recording_time_seconds value %r; resetting to default %d",
            data.get("max_recording_time_seconds"),
            MAX_RECORDING_TIME_SECONDS_DEFAULT,
        )
        max_rec = MAX_RECORDING_TIME_SECONDS_DEFAULT
        data["max_recording_time_seconds"] = MAX_RECORDING_TIME_SECONDS_DEFAULT
    if max_rec < MAX_RECORDING_TIME_SECONDS_MIN or max_rec > MAX_RECORDING_TIME_SECONDS_MAX:
        log.warning(
            "[CONFIG] max_recording_time_seconds=%d outside valid range [%d, %d], resetting to %d",
            max_rec,
            MAX_RECORDING_TIME_SECONDS_MIN,
            MAX_RECORDING_TIME_SECONDS_MAX,
            MAX_RECORDING_TIME_SECONDS_DEFAULT,
        )
        data["max_recording_time_seconds"] = MAX_RECORDING_TIME_SECONDS_DEFAULT


def _validate_model_path(data: dict[str, Any]) -> None:
    """Validate ``model_size`` against :data:`ALLOWED_USER_MODELS`."""
    # ``model_size`` missing from on-disk config → dataclass default
    if "model_size" not in data:
        return
    _model_size = data.get("model_size")
    if _model_size == NO_MODEL_SIZE:
        # Genuine "no model selected" state, valid, nothing to reset.
        return
    if _model_size not in ALLOWED_USER_MODELS:
        log.warning(
            "[CONFIG] model_size=%r not in allowlist %s; resetting to default %r",
            _model_size,
            sorted(ALLOWED_USER_MODELS),
            DEFAULT_MODEL_SIZE,
        )
        data["model_size"] = DEFAULT_MODEL_SIZE
        data.setdefault("_load_warnings", []).append(
            f"model_size={_model_size!r} not in allowlist, reset to {DEFAULT_MODEL_SIZE!r}"
        )


def _validate_qwen_model_path(data: dict[str, Any]) -> None:
    """Validate ``qwen_model_path``: must be an existing directory if set."""
    # Validate qwen_model_path: must be an existing directory if set
    qwen_path = data.get("qwen_model_path")
    if qwen_path is not None:
        # guard against non-str values that would crash
        if not isinstance(qwen_path, str):
            log.warning(
                "[CONFIG] Config qwen_model_path has non-str value %r (type=%s); resetting to None",
                qwen_path,
                type(qwen_path).__name__,
            )
            data["qwen_model_path"] = None
            data.setdefault("_load_warnings", []).append(
                f"qwen_model_path had non-str value {qwen_path!r} (type={type(qwen_path).__name__}), reset to None"
            )
            return
        p = Path(qwen_path)
        if not p.exists() or not p.is_dir():
            log.warning(
                "[CONFIG] Config qwen_model_path=%s does not exist or is not a directory, resetting to None",
                qwen_path,
            )
            data["qwen_model_path"] = None
            data.setdefault("_load_warnings", []).append(
                f"qwen_model_path={qwen_path!r} does not exist or is not a directory, reset to None"
            )
        else:
            # SEC-audit-007: Validate qwen_model_path is in a safe location
            qwen_resolved = p.resolve()
            safe_dirs = [_get_config_dir().resolve()]
            hf_home = os.environ.get("HF_HOME")
            if hf_home:
                safe_dirs.append(Path(hf_home).resolve())
            if not any(_is_path_within(qwen_resolved, d) for d in safe_dirs):
                log.warning(
                    "[CONFIG] qwen_model_path outside safe directories: %s, resetting to None",
                    qwen_path,
                )
                data["qwen_model_path"] = None
                data.setdefault("_load_warnings", []).append(
                    f"qwen_model_path={qwen_path!r} outside safe directories, reset to None"
                )


def _validate_corrections_path(data: dict[str, Any]) -> None:
    """Validate ``corrections_path``: must be an existing file if set."""
    # Validate corrections_path: must be an existing file if set
    corrections = data.get("corrections_path")
    if corrections is not None:
        # guard against non-str values that would crash
        if not isinstance(corrections, str):
            log.warning(
                "[CONFIG] Config corrections_path has non-str value %r (type=%s); resetting to None",
                corrections,
                type(corrections).__name__,
            )
            data["corrections_path"] = None
            data.setdefault("_load_warnings", []).append(
                f"corrections_path had non-str value {corrections!r} (type={type(corrections).__name__}), reset to None"
            )
            return
        cp = Path(corrections)
        if not cp.exists() or not cp.is_file():
            log.warning(
                "[CONFIG] Config corrections_path=%s does not exist or is not a file, resetting to None",
                corrections,
            )
            data["corrections_path"] = None
            data.setdefault("_load_warnings", []).append(
                f"corrections_path={corrections!r} does not exist or is not a file, reset to None"
            )
        else:
            try:
                cp_resolved = cp.resolve()
                allowed_roots = [
                    Path.home().resolve(),
                    _get_config_dir().resolve(),
                ]
                if not any(_is_path_within(cp_resolved, root) for root in allowed_roots):
                    raise ValueError("corrections_path must be within the user home or config directory")
            except ValueError as exc:
                log.warning(
                    "[CONFIG] Config corrections_path=%s rejected: %s, resetting to None",
                    corrections,
                    exc,
                )
                data["corrections_path"] = None
                data.setdefault("_load_warnings", []).append(
                    f"corrections_path={corrections!r} rejected: {exc}, reset to None"
                )


def _validate_privacy_consents(data: dict[str, Any]) -> None:
    """warn the user about privacy implications when ``log_transcriptions`` is enabled."""
    if data.get("log_transcriptions"):
        log.warning(
            "[CONFIG] log_transcriptions is enabled, transcription text "
            "(potentially containing PII) will be written to log files. "
            "Disable this setting if you do not want speech content persisted "
            "to disk."
        )
