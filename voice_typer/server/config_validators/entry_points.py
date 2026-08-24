"""IPC ``set_config`` entry points — :func:`validate_config_update`
and :func:`validate_config`.

This submodule was split out of the original monolithic
``config_validators/__init__.py`` so the two IPC / load-time entry
points have their own focused home.  It owns:

* :func:`validate_config_update` — the IPC ``set_config`` payload
  validator.  Filters caller-supplied updates against
  :data:`IPC_CONFIG_ALLOWLIST` (silently dropping unknown keys with a
  single ``log.warning`` side effect) and runs every per-field
  validator, accumulating ALL errors rather than short-circuiting
  on the first.
* :func:`validate_config` — the load-time / whole-config choke-point.
  Re-validates an already-loaded :class:`Config` instance against the
  SAME validators the IPC path uses (so the two paths cannot drift),
  plus the cross-field hotkey / cloud-consistency checks that the
  delta-only IPC path can't fully cover.

Both functions are pure (apart from the single ``log.warning`` call
inside :func:`validate_config_update` when an unknown field is
silently dropped — matching the original behaviour in ``config.py``).

Cross-field helper lookup
--------------------------

The cross-field helpers (:func:`_check_cross_field_hotkey_conflicts`
and :func:`_check_cross_field_cloud_config`) are looked up via the
package namespace at **call time** (lazy import inside each function
body), not bound at module import time.  This is essential because the
regression tests in ``tests/test_config_validators_hotkey_nonstring.py``
``monkeypatch`` /
``unittest.mock.patch`` ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
and expect :func:`validate_config` / :func:`validate_config_update`
to see the patched binding.  If these names were bound via a direct
``from .cross_field import …`` at the top of this module, the patch on
the package namespace would be invisible to the function bodies (each
module's globals are an independent dict).  The lazy-import pattern
mirrors the existing call-time lookup in ``config/loader.py``.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.config_validators.allowlist import IPC_CONFIG_ALLOWLIST
from voice_typer.server.config_validators.cross_field import (
    _CLOUD_CONSENT_FIELD_NAMES,
    _HOTKEY_FIELD_NAMES,
)

log = logging.getLogger("voice_typer.server.config_validators")


def validate_config_update(data: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Validate a caller-supplied config update payload.

        Parameters
        ----------
        data : dict
            The raw ``data`` field from an IPC ``set_config`` command.  Must
            be a dict — callers should check before invoking.

        Returns
        -------
        (validated, errors) : (dict, list[str])
            ``validated`` is the subset of ``data`` whose keys are in
            :data:`IPC_CONFIG_ALLOWLIST` and whose values passed their
            validators.  ``errors`` is a list of human-readable error
    strings for ALL invalid fields encountered (: the function
            accumulates all errors rather than stopping at the first — the
            dispatcher treats the entire payload atomically, see
            ``ipc_server.set_config``).

            Unknown keys are silently dropped (no error, no log entry beyond
            a debug-level message) to preserve the existing
            "test_ignores_unknown_fields_without_crashing" contract.

        Notes
        -----
        The function is pure: it does not touch the Config object or perform
        any I/O.  This makes it trivially testable.
    """
    validated: dict[str, object] = {}
    errors: list[str] = []
    for k, v in data.items():
        spec = IPC_CONFIG_ALLOWLIST.get(k)
        if spec is None:
            # Unknown key — silently drop.  : promoted to
            # WARNING (was DEBUG) to match ``Config._filter_unknown_keys``
            # in ``config.py``. Previously the two paths diverged:
            # on-disk load logged WARNING for unknown keys while the
            # IPC ``set_config`` path logged DEBUG, so a user editing
            # settings via the UI saw no signal when a stale client
            # sent a field the server's allowlist didn't recognize.
            # Field-name existence is not sensitive (the allowlist is
            # public source), and WARNING is gated by the same logging
            # config as the load path.
            log.warning("[CONFIG] set_config dropped unknown key %r", k)
            continue
        expected_type, validator = spec
        # Type-check first (cheap), then run the field-specific validator
        # (which may do range/enum checks).  The expected_type is a
        # redundant guard against the validator being too lenient —
        # defense in depth.
        #
        # expected_type may be a single type (``str``, ``int``, ``bool``,
        # ``float``) or a tuple of types (e.g. ``(str, type(None))`` for
        # Optional[str] fields like ``microphone``).
        type_ok: bool
        if isinstance(expected_type, tuple):
            type_ok = isinstance(v, expected_type)
        elif expected_type is bool:
            type_ok = isinstance(v, bool)
        elif expected_type is int:
            type_ok = isinstance(v, int) and not isinstance(v, bool)
        elif expected_type is float:
            type_ok = isinstance(v, int | float) and not isinstance(v, bool)
        elif expected_type is str:
            type_ok = isinstance(v, str)
        else:
            # Should never happen for the current allowlist.
            type_ok = isinstance(v, expected_type)
        if not type_ok:
            type_name = (
                " or ".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            errors.append(f"field {k!r} must be {type_name}, got {type(v).__name__}")
            # accumulate ALL errors, do not break on first.
            continue
        err = validator(v)
        if err is not None:
            errors.append(f"field {k!r} {err}")
            # accumulate ALL errors, do not break on first.
            continue
        validated[k] = v
    # cross-field hotkey conflict check.  Only fields that
    # passed their per-field validator are in ``validated`` — invalid
    # hotkeys don't participate in the cross-field check (they already
    # produced their own per-field error and would just add noise).
    # The only hotkey fields on the wire are ``hotkey`` and
    # ``repaste_hotkey`` (``push_to_talk_hotkey`` was fully removed —
    # PTT uses the main ``hotkey``).
    #
    # apply the same isinstance narrowing as  so the
    # ``hotkey_values`` dict (typed ``dict[str, str | None]``) actually
    # matches its annotation. ``validated[name]`` is ``object`` (the
    # ``validated`` dict's value type), so without the narrow the dict
    # comprehension would produce ``dict[str, object | None]`` and
    # pyrefly would flag the assignment. The narrow is a no-op at
    # runtime because ``_check_cross_field_hotkey_conflicts`` skips
    # non-string values anyway.
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        if name in validated:
            raw = validated[name]
            hotkey_values[name] = raw if isinstance(raw, str) else None
        else:
            hotkey_values[name] = None
    # Lazy-import the cross-field helpers from the package namespace so
    # tests that ``monkeypatch`` /
    # ``unittest.mock.patch`` ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
    # (see ``tests/test_config_validators_hotkey_nonstring.py``) see
    # the patched binding at call time. A direct ``from .cross_field
    # import …`` at module top would bind a private local name that
    # the package-namespace patch wouldn't touch.
    from voice_typer.server.config_validators import (
        _check_cross_field_cloud_config,
        _check_cross_field_hotkey_conflicts,
    )

    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # cross-field cloud/LLM config consistency check.
    # Only fields that passed their per-field validator are in
    # ``validated`` — invalid cloud/LLM fields don't participate in
    # the cross-field check (they already produced their own per-field
    # error and would just add noise).
    cloud_field_values: dict[str, object] = {}
    for cloud_name in (
        "cloud_api_url",
        "cloud_api_key",
        "llm_polish",
        "llm_api_key",
        "llm_polish_consent",
        *_CLOUD_CONSENT_FIELD_NAMES,
    ):
        if cloud_name in validated:
            cloud_field_values[cloud_name] = validated[cloud_name]
    errors.extend(_check_cross_field_cloud_config(cloud_field_values))
    return validated, errors


def validate_config(cfg: object) -> list[str]:
    """Validate an already-loaded :class:`Config` instance against
        :data:`IPC_CONFIG_ALLOWLIST`.

    (Task 2-x): the IPC ``set_config`` validator
        (:func:`validate_config_update`) only sees the *delta* a renderer
        pushes; it never re-checks the *whole* config that lives on disk
        after migration / manual edits / scripted writes. A migrated
        config can therefore hold values that the IPC validator would
        reject (e.g. a ``noise_suppression_method`` value of ``"speex"``
        left over from a hand-edited file before the enum was tightened,
        or a future ``audio_preset`` legacy alias surviving a botched
        migration). Until now there was no single choke-point that
        cross-checked the loaded config against the same rules the IPC
        layer enforces.

        This function is that choke-point. Agent 2-a is coordinated (via
        the worklog) to call it at the end of ``Config.load()`` and append
        any returned error strings to ``Config.last_load_warnings`` so the
        UI can surface "your config has invalid values" instead of
        silently running with a malformed state.

        Parameters
        ----------
        cfg
            A :class:`Config` dataclass instance (duck-typed — only
            ``getattr`` is used, so any object exposing the allowlisted
            fields as attributes works for testing).

        Returns
        -------
        list[str]
            A list of human-readable error strings, one per invalid
            field. Empty list means the config is valid. Each entry is
            formatted as ``"<field_name>: <error>"`` so the caller can
            display them line-by-line.

        Notes
        -----
        - Fields absent from ``cfg`` (``getattr`` returns ``None`` or
          raises ``AttributeError``) are SKIPPED — this function does not
          require every allowlisted field to be present on the object.
          This matches the IPC semantics where the renderer may push a
          partial update.
        - The validators are the SAME ones used by
          :func:`validate_config_update`, so the two paths can't drift.
    """
    errors: list[str] = []
    for key, (_field_type, validator) in IPC_CONFIG_ALLOWLIST.items():
        try:
            value = getattr(cfg, key)
        except AttributeError:
            # Field isn't present on the object — treat as "not set"
            # and skip (mirrors the IPC validator's None handling).
            continue
        if value is None:
            continue
        err = validator(value)
        if err:
            errors.append(f"{key}: {err}")
    # cross-field hotkey conflict check on the FULL config.
    # Unlike :func:`validate_config_update` (which can only see fields
    # the renderer pushed), this function sees ALL hotkey fields via
    # getattr — so it catches conflicts in a hand-edited config.json
    # that the IPC path alone could not surface.
    hotkey_values: dict[str, str | None] = {}
    for name in _HOTKEY_FIELD_NAMES:
        try:
            # narrow the ``getattr`` result explicitly so the
            # type-checker sees ``str | None`` (matching ``hotkey_values``'s
            # value type) instead of ``Any`` from the dynamic-name lookup.
            raw = getattr(cfg, name)
            hotkey_values[name] = raw if isinstance(raw, str) else None
        except AttributeError:
            hotkey_values[name] = None
    # Lazy-import the cross-field helpers from the package namespace so
    # tests that ``monkeypatch`` /
    # ``unittest.mock.patch`` ``voice_typer.server.config_validators._check_cross_field_hotkey_conflicts``
    # (see ``tests/test_config_validators_hotkey_nonstring.py``) see
    # the patched binding at call time. A direct ``from .cross_field
    # import …`` at module top would bind a private local name that
    # the package-namespace patch wouldn't touch.
    from voice_typer.server.config_validators import (
        _check_cross_field_cloud_config,
        _check_cross_field_hotkey_conflicts,
    )

    errors.extend(_check_cross_field_hotkey_conflicts(hotkey_values))
    # cross-field cloud/LLM config consistency check
    # on the FULL config. Unlike :func:`validate_config_update` (which
    # only sees fields the renderer pushed), this function sees ALL
    # cloud/LLM fields via getattr — so it catches inconsistencies
    # introduced by hand-edited config.json files.
    cloud_field_values: dict[str, object] = {}
    for cloud_name in (
        "cloud_api_url",
        "cloud_api_key",
        "llm_polish",
        "llm_api_key",
        "llm_polish_consent",
        *_CLOUD_CONSENT_FIELD_NAMES,
    ):
        # Field isn't present on the object — treat as "not set"
        # and skip (mirrors the IPC validator's None handling).
        with contextlib.suppress(AttributeError):
            cloud_field_values[cloud_name] = getattr(cfg, cloud_name)
    errors.extend(_check_cross_field_cloud_config(cloud_field_values))
    return errors
