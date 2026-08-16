"""Pure-dict coercion + path-validation helpers extracted from ``config.py``.

This module holds the 6 load-time data-dict transforms that
``Config.load()`` applies between schema migration and dataclass
construction:

* :func:`_coerce_streaming_fields`
* :func:`_coerce_max_recording_time`
* :func:`_validate_model_path`
* :func:`_validate_qwen_model_path`
* :func:`_validate_corrections_path`
* :func:`_validate_privacy_consents`

Each takes the loaded ``data`` dict (mutated in place) and resets
out-of-range / invalid values to their defaults, logging a WARNING and
appending a human-readable notice to ``data["_load_warnings"]`` so the
renderer can surface "your config was corrected" messages via
``Config.last_load_warnings``.

These helpers are intentionally pure-dict transforms — they take a
``dict[str, Any]`` and return ``None`` (mutating in place). They do NOT
import the :class:`Config` dataclass, so there is no circular
dependency between this module and ``config/__init__.py``. The
``Config`` classmethods of the same names are thin delegators that
forward to the functions here (preserving the existing
``Config._coerce_streaming_fields(data)`` public API).
"""

import logging
import os
from pathlib import Path
from typing import Any

from voice_typer.server.config_path_safety import _is_path_within
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
    """Lazy lookup of ``_config_dir`` via the parent ``config`` module.

    Tests monkeypatch ``voice_typer.server.config._config_dir`` to
    redirect config-dir lookups to a ``tmp_path`` fixture (see e.g.
    ``tests/test_config.py::TestConfigPathValidation``). After the
    helper extraction, this module's direct import of ``_config_dir``
    from ``config_internals.paths`` would bypass that monkeypatch
    (the symbol in THIS module's namespace wouldn't be touched by
    ``monkeypatch.setattr("voice_typer.server.config._config_dir", ...)``).
    Looking it up via the parent module at call time ensures the
    monkeypatch still takes effect.
    """
    from voice_typer.server import config as _cfg

    return _cfg._config_dir()


def _coerce_streaming_fields(data: dict[str, Any]) -> None:
    """Coerce streaming_* fields with min/max clamping + invariant checks.

    Extracted verbatim from ``load()``. Config fields were
    renamed (no migration needed): VALID-1 (MED-K) — each inline
    ``float()``/``int()`` coercion is wrapped in its own
    ``try/except`` so a SINGLE bad value resets ONLY that field to
    its default rather than aborting the entire load (which would
    discard every other valid field too).

    enforce streaming config invariants so the
    AudioWindowPlanner doesn't run forever or produce overlapping
    windows that never advance:

    * ``step < chunk``: otherwise the planner skips untranscribed
      audio between windows.
    * ``left_overlap < chunk``: otherwise every window is a
      duplicate of the previous one.
    """
    # Config fields were renamed (no migration needed):
    # VALID-1 (MED-K): each inline float()/int() coercion is
    # wrapped in its own try/except so a SINGLE bad value
    # resets ONLY that field to its default rather than
    # aborting the entire load (which would discard every
    # other valid field too).
    #
    # pre-fix the clamp used ``max(value, 3.0)`` /
    # ``max(value, 1.5)`` which SILENTLY raised sub-minimum values
    # to the floor. That created a split-brain with the IPC
    # validator (``config_validators.py:1191-1192`` — F1's
    # territory, which at the time of writing still uses
    # ``lo=0.0``): a user could ``set_config`` a 0.5-second
    # overlap, the IPC validator would accept it, ``Config.save()``
    # would persist it to disk, and on the next ``Config.load()``
    # the clamp would silently bump it to 3.0 — desyncing the
    # renderer's in-memory state from the on-disk config.json.
    #
    # The fix: stop silently clamping. If the on-disk value is
    # below the floor, RESET to the floor explicitly AND log a
    # WARNING + add to ``_load_warnings`` so the renderer can
    # surface "your config was corrected" notice. The IPC
    # validator side (F1's territory) needs a parallel update
    # to ``lo=STREAMING_*_SECONDS_MIN``; until that lands,
    # the load-time reset on THIS side at least surfaces the
    # correction to the user instead of silently changing it.
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
    # AudioWindowPlanner doesn't run forever or produce
    # overlapping windows that never advance.
    # - step < chunk: otherwise the planner skips untranscribed
    #   audio between windows.
    # - left_overlap < chunk: otherwise every window is a
    #   duplicate of the previous one.
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
    # ``streaming_left_overlap_seconds`` above (lines 110-141), so the
    # key is guaranteed present and numeric here — no default or
    # try/except needed. The previous re-read used a bare ``3.0``
    # default that could drift from ``STREAMING_LEFT_OVERLAP_SECONDS_MIN``
    # (see module docstring on the parallel ``max_recording_time_seconds``
    # drift hazard).
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
    """SIMPLIFY-001: clamp ``max_recording_time_seconds`` to valid range [300, 3600].

    Extracted verbatim from ``load()``. Handles old config
    files that had ``0 = auto-select`` (which is now invalid).
    VALID-1 (MED-K): also wraps the ``int()`` coercion so a
    non-numeric value resets only this field, not the whole config.

    the bounds + default are now sourced from the module-level
    constants ``MAX_RECORDING_TIME_SECONDS_MIN`` / ``_MAX`` /
    ``_DEFAULT`` so the IPC validator (``config_validators.py``) and
    this clamp share a single source of truth.
    """
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
    """Validate ``model_size`` against :data:`ALLOWED_USER_MODELS`.

    Extracted verbatim from ``load()``. If the on-disk
    ``model_size`` is not in the allowlist (e.g. a stale entry from
    a previous build, or a model that was removed from the catalog),
    reset to ``DEFAULT_MODEL_SIZE`` (the canonical default — see
    ``voice_typer/server/model_registry.py``; change the default in
    that ONE place).

    ``NO_MODEL_SIZE`` (the empty string) is a REAL value, not a
    correction: it means the user has genuinely not selected a model
    (see ``model_registry.NO_MODEL_SIZE``). It is preserved as-is —
    the app reports "No model selected" and waits for the user to pick
    one instead of silently resetting to the default.

    the reset is now logged at WARNING and appended to
    ``data["_load_warnings"]`` so the renderer can surface a
    "your config was corrected" notice via
    ``instance.last_load_warnings``. Pre-fix, the reset was silent
    — the user's ``model_size`` was changed without any signal.

    Note: only warn when ``model_size`` is EXPLICITLY present in
    ``data`` (i.e. on-disk) with an invalid value. If the key is
    missing entirely (a partial config.json from a fresh install),
    the dataclass default applies silently — the user has no
    "correction" to be notified about.
    """
    # ``model_size`` missing from on-disk config → dataclass default
    # applies silently (no correction to surface).
    if "model_size" not in data:
        return
    _model_size = data.get("model_size")
    if _model_size == NO_MODEL_SIZE:
        # Genuine "no model selected" state — valid, nothing to reset.
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
    """Validate ``qwen_model_path``: must be an existing directory if set.

    Extracted verbatim from ``load()``. SEC-audit-007:
    validate ``qwen_model_path`` is in a safe location (the config
    dir or ``$HF_HOME``). Resets to ``None`` if the path doesn't
    exist, isn't a directory, or escapes the safe dirs.

    pre-fix, a non-``str`` value (e.g.
    ``qwen_model_path: 123`` or ``qwen_model_path: ["/tmp"]`` in a
    hand-edited config.json) crashed ``Path(qwen_path)`` with
    ``TypeError`` — which propagated up through ``Config.load()``'s
    outer ``except`` (catches ``TypeError``), reset the ENTIRE
    config to defaults, and moved config.json aside as corrupt
    (even though only one field was bad). The fix adds an
    ``isinstance(qwen_path, str)`` guard at the top: non-str
    values are reset to ``None`` with a logged WARNING + a
    ``_load_warnings`` entry, and the rest of the load proceeds.

    every reset site (non-str, missing dir, unsafe dir)
    now appends to ``data["_load_warnings"]`` so the renderer
    surfaces a "your config was corrected" notice via
    ``instance.last_load_warnings``.
    """
    # Validate qwen_model_path: must be an existing directory if set
    qwen_path = data.get("qwen_model_path")
    if qwen_path is not None:
        # guard against non-str values that would crash
        # ``Path(qwen_path)`` and reset the ENTIRE config.
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
    """Validate ``corrections_path``: must be an existing file if set.

    Extracted verbatim from ``load()``. SEC-audit-006 (Round
    0 forward-port — M6): defense-in-depth path-traversal check.
    ``corrections_path`` is NOT in the IPC allowlist (can only be
    set via direct ``config.json`` edit), but a user who manually
    edits the config could point it at an arbitrary file.  The
    :mod:`text_cleanup` module reads + applies corrections from
    this file, so a malicious or accidentally-chosen path could
    expose sensitive data (e.g. log transcription text being
    matched against ``/etc/passwd`` contents).  Restrict the path
    to the user's home directory or the config directory — both are
    user-writable locations where the user has explicitly chosen to
    store data.

    pre-fix, a non-``str`` value (e.g.
    ``corrections_path: 42``) crashed ``Path(corrections)`` with
    ``TypeError`` — propagated up through ``Config.load()``'s outer
    ``except`` (catches ``TypeError``), reset the ENTIRE config to
    defaults, and moved config.json aside as corrupt. The fix adds
    an ``isinstance(corrections, str)`` guard at the top.

    every reset site (non-str, missing file, unsafe dir)
    now appends to ``data["_load_warnings"]`` so the renderer
    surfaces the correction via ``instance.last_load_warnings``.
    """
    # Validate corrections_path: must be an existing file if set
    corrections = data.get("corrections_path")
    if corrections is not None:
        # guard against non-str values that would crash
        # ``Path(corrections)`` and reset the ENTIRE config.
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
    """warn the user about privacy implications when ``log_transcriptions`` is enabled.

    Extracted verbatim from ``load()``. Transcription text
    may contain sensitive personal information (names, addresses,
    medical details, etc.) that gets written to log files on disk.
    The warning is emitted once per config load so it appears in
    the log on every startup if the flag is active.
    """
    if data.get("log_transcriptions"):
        log.warning(
            "[CONFIG] log_transcriptions is enabled — transcription text "
            "(potentially containing PII) will be written to log files. "
            "Disable this setting if you do not want speech content persisted "
            "to disk."
        )
