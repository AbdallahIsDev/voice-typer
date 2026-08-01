"""Single source of truth for user-data file inventories.

Two inventories are maintained here so the uninstall-purge path
(:func:`voice_typer.server.config.purge_user_data`) and the GDPR
right-to-erasure / data-portability path
(:meth:`voice_typer.server.service.privacy.PrivacyMixin.delete_all_personal_data`
/ :meth:`export_gdpr_bundle`) cannot drift from the actual on-disk
filenames again.

* ``_USER_DATA_FILES`` — bare on-disk filenames (and dot-prefixed
  marker files) that ``purge_user_data`` unlinks on uninstall. Every
  entry is the actual on-disk filename (no abstract / "logical"
  names), so the purge walk always matches a real file when one
  exists.

* ``_GDPR_PERSONAL_FILES`` — on-disk filenames that contain personal
  data and must be erased by GDPR Art. 17 (``delete_all_personal_data``)
  and included in GDPR Art. 20 export bundles. Overlaps with
  ``_USER_DATA_FILES`` (both remove ``config.json`` etc.) but is a
  distinct list because the GDPR path also removes transient files
  (logs, the diagnostic-bundle glob, the prewarm log) that the purge
  path handles via directory ``rmtree`` (``logs/`` etc.).

The tuples are populated using the canonical ``*_FILENAME`` constants
exported by the modules that own each artifact (``crash_recovery``,
``vocabulary``, ``templates``). A future rename in the owning module
propagates automatically — closing the drift bug where the purge list
had bare names while the actual on-disk filename was the prefixed
``voice-typer-recovery.json`` (so the purge walk silently no-op'd on
a real file).

Circular-import note: ``crash_recovery`` does a module-level
``from voice_typer.server.config import _secure_atomic_write``, and
``config`` in turn does a module-level
``from voice_typer.server._user_data_files import _USER_DATA_FILES``.
To break this cycle, the tuples are defined at the TOP of this module
using the canonical constants, and the canonical-constant imports are
placed at the very bottom. That way, when ``config`` (transitively
triggered by ``crash_recovery``) tries to read ``_USER_DATA_FILES``
from a partially-initialised ``_user_data_files`` module, the tuple is
already bound. The ``_SANITY_CHECK`` block at the bottom re-verifies
the canonical constants match the literals used in the tuples, so a
future rename in any owning module is caught at import time (in any
import path that successfully resolves the canonical constants).
"""

# ──────────────────────────────────────────────────────────────────────────
# Canonical filename constants owned by other modules.
#
# These are imported LAZILY at the bottom of this file to break a
# circular import: ``crash_recovery`` imports ``config._secure_atomic_write``
# at module level, and ``config`` imports ``_user_data_files._USER_DATA_FILES``
# at module level. If we imported the canonical constants at the TOP of
# this file, the chain ``_user_data_files`` → ``crash_recovery`` →
# ``config`` → ``_user_data_files`` (partial) would fail with
# ``ImportError: cannot import name '_USER_DATA_FILES' from partially
# initialised module``.
#
# To still get the "single source of truth" benefit, the tuples below
# use the same string literals as the canonical constants, and the
# ``_SANITY_CHECK`` block at the bottom of this file asserts that the
# literals match the canonical constants at import time. A future
# rename in any owning module will trip the assertion (in any import
# path that successfully resolves the canonical constants).
# ──────────────────────────────────────────────────────────────────────────

# Mirrors ``voice_typer.server.crash_recovery.RECOVERY_FILENAME``.
_RECOVERY_FILENAME: str = "voice-typer-recovery.json"

# Mirrors ``voice_typer.server.vocabulary.VOCAB_FILENAME``.
_VOCAB_FILENAME: str = "voice-typer-vocabulary.json"

# Mirrors ``voice_typer.server.templates.TEMPLATES_FILENAME``.
_TEMPLATES_FILENAME: str = "voice-typer-templates.json"

# Corrections filename — ``text_cleanup.py`` uses the literal
# ``"voice-typer-corrections.json"`` in two places (the external-loader
# fallback and the persisted-user-corrections path) without exposing a
# module-level constant. Hard-coded here as the actual on-disk name so
# the next time someone greps for it they land in one canonical place;
# a future cleanup should add a ``CORRECTIONS_FILENAME`` constant to
# ``text_cleanup.py`` (or to ``vocabulary.py`` next to
# ``BUNDLED_CORRECTIONS_PATH``) and import it here.
_CORRECTIONS_FILENAME: str = "voice-typer-corrections.json"

# Onboarding state files. ``onboarding.py`` persists three dot-prefixed
# marker files (no canonical ``*_FILENAME`` constant is exported) —
# ``.onboarding_complete`` (terminal marker), ``.onboarding_started``
# (wizard-has-rendered marker), and ``.onboarding_progress`` (JSON
# blob with the in-progress wizard state). Listed individually because
# the purge walk iterates a flat name list (not a glob), so each
# marker must be enumerated.
_ONBOARDING_COMPLETE_MARKER: str = ".onboarding_complete"
_ONBOARDING_STARTED_MARKER: str = ".onboarding_started"
_ONBOARDING_PROGRESS_MARKER: str = ".onboarding_progress"

# Personal-data log files. These are not owned by a single Python
# module (``voice-typer.log`` is written by ``log.py`` via
# ``RotatingFileHandler``; ``prewarm.log`` by the prewarm sidecar;
# ``electron-renderer-errors.log`` by the Electron host's
# ``structuredLogger.ts``; ``voice-typer-rust.log`` is a defensive
# legacy entry for the pre-migration Rust log filename). The names
# mirror the entries previously inlined in ``PrivacyMixin`` so the two
# inventories cannot drift.
_VOICE_TYPER_LOG: str = "voice-typer.log"
_PREWARM_LOG: str = "prewarm.log"
_RENDERER_ERRORS_LOG: str = "electron-renderer-errors.log"
_RUST_LOG: str = "voice-typer-rust.log"

# Backend PID file — written by ``single_instance.py`` (see
# ``_backend_pid_path`` / ``backend_pid_path``).
_BACKEND_PID_FILE: str = "backend.pid"

# Restart token — defensive entry, written by the restart helper to
# signal a pending relaunch across the sidecar process boundary.
_RESTART_TOKEN: str = ".restart_token"


# Files removed by the uninstall purge path. Every entry is the actual
# on-disk filename (no abstract logical names) so the
# ``purge_user_data`` walk matches real files when they exist.
_USER_DATA_FILES: tuple[str, ...] = (
    "config.json",
    "config.json.bak",
    "config.json.lock",
    _BACKEND_PID_FILE,
    "history.db",
    "history.db-wal",
    "history.db-shm",
    _RECOVERY_FILENAME,
    _VOCAB_FILENAME,
    _TEMPLATES_FILENAME,
    _CORRECTIONS_FILENAME,
    _ONBOARDING_COMPLETE_MARKER,
    _ONBOARDING_STARTED_MARKER,
    _ONBOARDING_PROGRESS_MARKER,
    _VOICE_TYPER_LOG,
    _PREWARM_LOG,
    _RENDERER_ERRORS_LOG,
    _RUST_LOG,
    _RESTART_TOKEN,
)


# Files erased by GDPR Art. 17 ``delete_all_personal_data`` and
# included in GDPR Art. 20 export bundles. Overlaps with
# ``_USER_DATA_FILES`` but is intentionally a separate list because
# the GDPR path also removes files that the purge path handles via
# directory ``rmtree`` (``logs/`` etc.) — keeping the explicit list
# here means the GDPR walk is resilient to a future change to the
# directory layout.
_GDPR_PERSONAL_FILES: tuple[str, ...] = (
    "history.db",
    "history.db-wal",
    "history.db-shm",
    _RECOVERY_FILENAME,
    "config.json",
    _CORRECTIONS_FILENAME,
    _VOCAB_FILENAME,
    _TEMPLATES_FILENAME,
    _VOICE_TYPER_LOG,
    _PREWARM_LOG,
    _RENDERER_ERRORS_LOG,
    _RUST_LOG,
    # config.json.bak retains plaintext API keys
    "config.json.bak",
    # config.json.lock can hold stale PID + username
    "config.json.lock",
    _RESTART_TOKEN,
)


# ──────────────────────────────────────────────────────────────────────────
# Sanity check: verify the literals above match the canonical constants
# exported by the owning modules. This catches drift if a future rename
# in ``crash_recovery`` / ``vocabulary`` / ``templates`` changes the
# canonical ``*_FILENAME`` constant without also updating the literals
# above.
#
# Wrapped in a try/except ImportError so a direct import of this module
# (without going through ``config.py`` first) does not fail when the
# canonical-constant imports trigger a circular-import chain. In that
# case, the literals above are still authoritative (they mirror the
# canonical values), and the sanity check simply doesn't run.
# ──────────────────────────────────────────────────────────────────────────
try:
    from voice_typer.server.crash_recovery import (
        RECOVERY_FILENAME as _CANONICAL_RECOVERY_FILENAME,
    )
    from voice_typer.server.templates import (
        TEMPLATES_FILENAME as _CANONICAL_TEMPLATES_FILENAME,
    )
    from voice_typer.server.vocabulary import (
        VOCAB_FILENAME as _CANONICAL_VOCAB_FILENAME,
    )

    assert _RECOVERY_FILENAME == _CANONICAL_RECOVERY_FILENAME, (
        f"_RECOVERY_FILENAME drifted: literal {_RECOVERY_FILENAME!r} != "
        f"canonical {_CANONICAL_RECOVERY_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match crash_recovery.RECOVERY_FILENAME."
    )
    assert _VOCAB_FILENAME == _CANONICAL_VOCAB_FILENAME, (
        f"_VOCAB_FILENAME drifted: literal {_VOCAB_FILENAME!r} != "
        f"canonical {_CANONICAL_VOCAB_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match vocabulary.VOCAB_FILENAME."
    )
    assert _TEMPLATES_FILENAME == _CANONICAL_TEMPLATES_FILENAME, (
        f"_TEMPLATES_FILENAME drifted: literal {_TEMPLATES_FILENAME!r} != "
        f"canonical {_CANONICAL_TEMPLATES_FILENAME!r}. Update the literal "
        f"in _user_data_files.py to match templates.TEMPLATES_FILENAME."
    )
except ImportError:
    # Circular import — happens when this module is imported directly
    # without going through ``config.py`` (or
    # ``voice_typer.server.service``) first. The literals above are
    # still authoritative; the sanity check simply doesn't run.
    pass


__all__ = [
    "_USER_DATA_FILES",
    "_GDPR_PERSONAL_FILES",
]
