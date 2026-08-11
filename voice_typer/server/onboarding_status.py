"""Single-file onboarding status persistence.

Onboarding state used to be spread across THREE dotfile markers in the
config dir, all describing the same thing (the wizard's lifecycle):

* ``.onboarding_complete``  — ``{"completed": bool, "version": int}``
  terminal marker: the wizard finished successfully.
* ``.onboarding_started``   — ``{"started": bool, "version": int}``
  wizard-has-rendered marker (distinct from ``completed`` — a user can
  be mid-wizard: started without being completed).
* ``.onboarding_fail_count``— ``{"count": int, "last_fail_ts": float}``
  the startup auto-heal circuit breaker's fail counter (no ``.json``
  extension, which made the file format ambiguous).

This module consolidates them into ONE JSON document,
``.onboarding_status.json``::

    {"version": 1, "started": bool, "completed": bool,
     "fail_count": int, "last_fail_ts": float}

The ``started`` and ``completed`` fields are intentionally kept
separate — they are NOT redundant: ``startup_sequence.py``'s auto-heal
checks ``started`` to distinguish a genuine mid-wizard crash from a
stale lost-marker state, and ``OnboardingController.is_first_run``
checks ``completed``. Merging them into one flag would break the
auto-heal logic.

Migration: the first time the status file is read (or written), any
legacy markers present are merged into the status document, the status
file is written, and the legacy markers are deleted — so upgrading
users end up with exactly one file, created by the app itself.

Write-error policy: :func:`write_status` raises on disk failure so
callers with a re-raise contract (``OnboardingController.mark_complete``)
can surface the error to the IPC layer. Best-effort callers (the
startup fail counter) wrap it themselves. :func:`read_status` never
raises — it falls back to validated defaults.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Canonical merged status filename (lives in the config dir).
ONBOARDING_STATUS_FILENAME: str = ".onboarding_status.json"

# Legacy marker filenames (pre-merge). Retained so the one-time
# migration can read them; they are deleted after a successful merge.
_LEGACY_COMPLETE_MARKER: str = ".onboarding_complete"
_LEGACY_STARTED_MARKER: str = ".onboarding_started"
_LEGACY_FAIL_COUNT_MARKER: str = ".onboarding_fail_count"

_STATUS_VERSION: int = 1

# Keys the schema has since renamed. They are consumed during
# ``_coerce`` (mapped onto their successor) rather than carried
# verbatim as unknown future fields, so a read-modify-write migrates
# old documents to the canonical keys instead of duplicating them.
_LEGACY_KEYS: frozenset[str] = frozenset({"count"})


def _defaults() -> dict:
    """Schema defaults — the safe baseline for every missing field."""
    return {
        "version": _STATUS_VERSION,
        "started": False,
        "completed": False,
        "fail_count": 0,
        "last_fail_ts": 0.0,
    }


def status_path(config_dir: "Path | str") -> Path:
    """Absolute path to the merged onboarding-status file."""
    return Path(config_dir) / ONBOARDING_STATUS_FILENAME


def read_status(config_dir: "Path | str") -> dict:
    """Read the merged onboarding-status document (best-effort).

    If the status file is missing, migrates the legacy markers in place
    (merging their values, writing the status file, deleting the legacy
    files). If no legacy markers exist either, returns the defaults
    WITHOUT writing — a fresh install has no onboarding activity yet,
    so we don't create the file until the app actually records state.

    Never raises: every failure mode (missing/corrupt file, read
    errors) falls back to validated defaults so callers like
    ``OnboardingController.is_first_run`` / the startup auto-heal keep
    working.
    """
    data = _defaults()
    path = status_path(config_dir)
    try:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data.update(_coerce(parsed))
            # Legacy markers alongside a status file are leftovers from
            # an interrupted migration — clean them up best-effort.
            _delete_legacy(config_dir)
            return data
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    # Status file missing (or unreadable): migrate any legacy markers.
    legacy = _read_legacy(config_dir)
    if legacy is not None:
        data.update(legacy)
        try:
            _write(data, config_dir)
            _delete_legacy(config_dir)
        except Exception as exc:  # best-effort migration
            log.debug("[ONBOARDING] Could not migrate legacy markers: %s", exc)
    return data


def write_status(
    config_dir: "Path | str",
    *,
    durability: bool = True,
    **updates: object,
) -> dict:
    """Merge ``updates`` into the current status and persist it.

    Returns the merged document. Raises on write failure so callers
    with a re-raise contract (``OnboardingController.mark_complete``)
    can surface disk errors; best-effort callers (the startup fail
    counter) wrap this themselves. ``durability=False`` matches the
    fast atomic-write pattern used by the autostart/prewarm paths.
    """
    data = read_status(config_dir)
    data.update(updates)
    data = _coerce(data)
    _write(data, config_dir, durability=durability)
    return data


def reset_status(config_dir: "Path | str") -> bool:
    """Delete the status file (and any legacy markers).

    Returns ``True`` when the status document is gone (deleted, or
    already absent), ``False`` if it could not be deleted (a deletion
    error is logged at DEBUG). Legacy-marker cleanup is best-effort and
    does not affect the result — once the status document is gone the
    wizard will re-run regardless. Callers with an error contract
    (``reset_onboarding_complete``) check the return value; best-effort
    callers (``OnboardingController.reset``) may ignore it.
    """
    _delete_legacy(config_dir)
    try:
        status_path(config_dir).unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.debug("[ONBOARDING] Could not delete onboarding status file: %s", exc)
        return False


# ── internals ──────────────────────────────────────────────────────────


def _write(data: dict, config_dir: "Path | str", *, durability: bool = True) -> None:
    # Imported at call time (module attribute lookup) so tests that
    # monkeypatch ``secure_file_io._secure_atomic_write`` intercept the
    # write, and so this module stays import-safe in any order.
    import voice_typer.server.secure_file_io as sio

    Path(config_dir).mkdir(parents=True, exist_ok=True)
    sio._secure_atomic_write(
        status_path(config_dir),
        json.dumps(data),
        durability=durability,
    )


def _coerce(data: dict) -> dict:
    """Validate + coerce a parsed status dict against the schema."""
    out = _defaults()
    # Forward-compat: carry unknown keys verbatim so a downgrade
    # read-modify-write doesn't silently drop fields written by a
    # newer app version. The pre-rename ``count`` key is a KNOWN
    # legacy key (not an unknown future field): it is consumed below
    # and mapped onto ``fail_count``, so a read-modify-write migrates
    # old documents to the canonical key instead of carrying both.
    for key, value in data.items():
        if key not in out and key not in _LEGACY_KEYS:
            out[key] = value
    version = data.get("version", _STATUS_VERSION)
    if isinstance(version, int) and version > 0:
        out["version"] = version
    for key in ("started", "completed"):
        value = data.get(key, False)
        if isinstance(value, bool):
            out[key] = value
    # ``fail_count`` is the canonical key for the onboarding fail
    # counter (renamed from the ambiguous ``count``). ``count`` is
    # still accepted for back-compat with documents written before the
    # rename (and with the legacy marker format).
    count = data.get("fail_count", data.get("count", 0))
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        # Invalid/missing canonical value — fall back to the legacy
        # ``count`` if it carries a valid value. Defensive: no writer
        # has produced both keys, but a corrupt file shouldn't lose a
        # valid counter.
        count = data.get("count", 0)
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        out["fail_count"] = count
    last_fail_ts = data.get("last_fail_ts", 0.0)
    if isinstance(last_fail_ts, (int, float)) and not isinstance(last_fail_ts, bool):
        out["last_fail_ts"] = float(last_fail_ts)
    return out


def _read_legacy(config_dir: "Path | str") -> dict | None:
    """Merge the three legacy marker files into one dict.

    Returns ``None`` when none of the legacy markers exist. Corrupt or
    unreadable individual markers are skipped (their fields keep the
    schema defaults). The ``version`` from the first legacy marker that
    carries one wins; ``started`` / ``completed`` / ``fail_count`` /
    ``last_fail_ts`` are each taken from their own marker.
    """
    out: dict = {}
    any_found = False
    cfg = Path(config_dir)

    # Terminal marker: {"completed": bool, "version": int}
    try:
        path = cfg / _LEGACY_COMPLETE_MARKER
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if isinstance(data.get("completed"), bool):
                    out["completed"] = data["completed"]
                if isinstance(data.get("version"), int):
                    out["version"] = data["version"]
                any_found = True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    # Wizard-has-rendered marker: {"started": bool, "version": int}
    try:
        path = cfg / _LEGACY_STARTED_MARKER
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if isinstance(data.get("started"), bool):
                    out["started"] = data["started"]
                if isinstance(data.get("version"), int):
                    out["version"] = data["version"]
                any_found = True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    # Fail counter: {"count": int, "last_fail_ts": float} (the legacy
    # marker format; the merged document uses ``fail_count``).
    try:
        path = cfg / _LEGACY_FAIL_COUNT_MARKER
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                count = data.get("count", 0)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    out["fail_count"] = count
                last_fail_ts = data.get("last_fail_ts", 0.0)
                if isinstance(last_fail_ts, (int, float)) and not isinstance(last_fail_ts, bool):
                    out["last_fail_ts"] = float(last_fail_ts)
                any_found = True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    return out if any_found else None


def _delete_legacy(config_dir: "Path | str") -> None:
    """Best-effort removal of the three legacy marker files."""
    cfg = Path(config_dir)
    for name in (
        _LEGACY_COMPLETE_MARKER,
        _LEGACY_STARTED_MARKER,
        _LEGACY_FAIL_COUNT_MARKER,
    ):
        try:
            (cfg / name).unlink(missing_ok=True)
        except OSError as exc:
            log.debug("[ONBOARDING] Could not remove legacy marker %s: %s", name, exc)
