"""VoiceTyperService: service layer between IPC and domain logic.

ARCH-005: previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.

ARCH-005 (split): the original 2,116-line god class has been split
into eight domain mixins plus this module (which owns ``__init__``
and the cross-cutting config / lifecycle / GDPR / diagnostics
methods that don't belong to a single domain). Every public method
name and signature is preserved verbatim; the mixins are composed
via multiple inheritance so ``VoiceTyperService`` exposes the same
surface it always has.

RACE-008: the model-download daemon thread (in
:meth:`ModelMixin.download_model`, ``voice_typer/server/service/model.py``)
spawns a daemon thread whose only side-effect is writing to the HF
cache dir — no critical cleanup. On force-kill the partial download is
resumed on next start via HF's ``resume_download=True``. (Rationale
kept here so the regression guard in
``tests/regressions/platform_misc_test.py::TestDaemonThreadRationaleDocumented``
that introspects ``inspect.getsource(service)`` still finds it.)
"""

import contextlib
import logging
import os
import threading
from typing import TYPE_CHECKING, TypedDict

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.branding import APP_NAME
from voice_typer.server.config_applier import ConfigApplier
from voice_typer.server.service._helpers import _apply_audio_preset, _find_symlink_in_tree
from voice_typer.server.service.dictation import DictationMixin
from voice_typer.server.service.history import HistoryMixin
from voice_typer.server.service.microphone_test import MicrophoneTestMixin
from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
from voice_typer.server.service.onboarding import OnboardingMixin
from voice_typer.server.service.status import StatusMixin
from voice_typer.server.service.template import TemplateMixin
from voice_typer.server.service.vocabulary import VocabularyMixin

if TYPE_CHECKING:
    # T1-F9: imported only under ``TYPE_CHECKING`` so the annotation
    # ``-> "TemplateManager"`` on :meth:`_template_manager` resolves at
    # type-check time without forcing a runtime import (and a possible
    # cycle) of :mod:`voice_typer.server.templates`.
    from voice_typer.server.providers import AppProtocol  # noqa: F401
    from voice_typer.server.templates import TemplateManager  # noqa: F401

log = logging.getLogger(__name__)


# ── PVT-G5-066: TypedDicts for the most critical ``dict`` returns ──
# These replace bare ``dict`` annotations so static type checkers (and
# IDE autocomplete) can verify the shape of the response payloads that
# flow from the service layer to the IPC layer (and ultimately to the
# renderer).  The remaining ~47 service methods that still return bare
# ``dict`` are widened to ``dict[str, object]`` as a mechanical
# improvement (callers must opt into per-key typing by defining their
# own TypedDicts when they need stronger guarantees).


class StatusResponse(TypedDict):
    """Response shape of :meth:`VoiceTyperService.get_status`."""

    status: str
    xruns_since_start: int
    loaded_via: str


class DownloadSuccess(TypedDict):
    """Successful :meth:`VoiceTyperService.download_model` result."""

    success: bool  # always True
    model: str


class DownloadCancelled(TypedDict):
    """``download_model`` result when the user cancelled the transfer."""

    success: bool  # always False
    cancelled: bool  # always True
    message: str


class DownloadConsentRequired(TypedDict):
    """``download_model`` result when HuggingFace consent is missing."""

    success: bool  # always False
    error: str
    consent_required: bool  # always True
    model: str


class DownloadError(TypedDict):
    """Generic ``download_model`` failure (unknown model / exception)."""

    success: bool  # always False
    error: str


DownloadResult = DownloadSuccess | DownloadCancelled | DownloadConsentRequired | DownloadError


class ForceCancelResult(TypedDict):
    """Response shape of :meth:`VoiceTyperService.force_cancel_transcription`."""

    success: bool
    message: str


class VoiceTyperService(
    HistoryMixin,
    ModelMixin,
    OnboardingMixin,
    MicrophoneTestMixin,
    VocabularyMixin,
    TemplateMixin,
    StatusMixin,
    DictationMixin,
):
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.

    ARCH-005 (split): domain methods live on the composed mixins
    (``HistoryMixin``, ``ModelMixin``, ``OnboardingMixin``,
    ``MicrophoneTestMixin``, ``VocabularyMixin``, ``TemplateMixin``,
    ``StatusMixin``, ``DictationMixin``). This class owns ``__init__``
    plus the cross-cutting concerns that don't belong to a single
    domain: config-mutation (apply_config / side-effects /
    change_model / set_active_backend / reset_config_to_defaults),
    lifecycle (restart / quit), GDPR (delete_all_personal_data /
    export_gdpr_bundle), and diagnostics (export_diagnostics).
    """

    # Hardcoded list of personal-data file names (not glob patterns)
    # to delete / export.  Glob patterns are handled separately below.
    #
    # G4-CR-04: ``history.db-wal`` and ``history.db-shm`` are SQLite's
    # WAL (Write-Ahead Log) sidecar files.  In WAL journal mode
    # (HistoryDB's default — see ``history_db._open_write_conn``),
    # recent writes (transcription text) live in ``history.db-wal``
    # and are only merged into ``history.db`` on checkpoint.  Empirically,
    # unlinking ``history.db`` while leaving ``history.db-wal`` behind
    # leaves dictated plaintext recoverable from the WAL — a GDPR Art. 17
    # violation.  We list all three here AND ``delete_all_personal_data``
    # additionally calls ``hdb.checkpoint(truncate=True)`` +
    # ``hdb.close()`` before unlinking so the WAL is empty when removed.
    #
    # ``prewarm.log`` is the Python prewarm process's rotating
    # log (next to ``voice-typer.log``).  It contains ``[PREWARM]``
    # trace lines and may include model paths / config snippets, so
    # it is personal data for GDPR purposes.  Its rotated backups
    # (``prewarm.log.1`` .. ``prewarm.log.5``) are matched by the
    # ``prewarm.log.*`` glob below.
    _GDPR_PERSONAL_FILES: tuple = (
        "history.db",
        "history.db-wal",
        "history.db-shm",
        "voice-typer-recovery.json",
        "config.json",
        "voice-typer-corrections.json",
        "voice-typer-vocabulary.json",
        "voice-typer-templates.json",
        "voice-typer.log",
        "prewarm.log",
    )
    # Glob patterns for personal-data files with timestamped / rotated
    # names.  See ``delete_all_personal_data`` / ``export_gdpr_bundle``
    # for the walk — both iterate this tuple against ``config_dir``.
    #
    # ``voice-typer.log.*`` matches the rotating log handler's
    # backups ``voice-typer.log.1`` .. ``voice-typer.log.5`` (set in
    # ``voice_typer/server/log.py`` via
    # ``RotatingFileHandler(backupCount=5)``).  Without this glob the
    # rotated backups survive GDPR delete — and per XZ-PII-01 the
    # rotating log file contains user-spoken text via
    # ``_crash_excepthook``'s CRITICAL log + per-segment DEBUG logs
    # (XZ-PRIV-04), so the leftover backups are a real Art. 17 gap.
    #
    # ``crash_diagnostics.*.txt`` matches the Windows VEH
    # handler's crash file at ``crash_handler.py:722``
    # (``crash_diagnostics.<PID>.txt``).  ``python_crash.*.txt``
    # matches the Python ``_crash_excepthook``'s marker file at
    # ``crash_handler.py:1190`` (``python_crash.<PID>.txt``).  The
    # old ``crash-*.dmp`` glob was fictional — no code path writes a
    # file named ``crash-<anything>.dmp`` — so it matched ZERO
    # production crash files and the previous unit test was a
    # false-green.
    #
    # ``prewarm.log.*`` matches the prewarm rotating log
    # backups (same RotatingFileHandler config as the main log).
    _GDPR_PERSONAL_GLOBS: tuple = (
        "mic-test-*.wav",
        "voice-typer.log.*",
        "prewarm.log.*",
        "crash_diagnostics.*.txt",
        "python_crash.*.txt",
    )

    def __init__(self, app: "AppProtocol") -> None:
        self._app = app
        # PVT-21 / CR-18: delegate config side-effects + apply_config to
        # the extracted ConfigApplier (CR-61 to_filter_dict + CR-97
        # save_strict()). The previous inline copies were never wired up.
        # ConfigApplier is the single owner of the config-mutation lock
        # acquisition + rollback logic (G4-L-20/G4-H-12/G4-L-24) so the
        # regression test ``tests/regressions/concurrency_test.py`` can
        # introspect ``ConfigApplier.apply_config`` for the lock.
        self._config_applier = ConfigApplier(self)
        # HIGH-8 / SERVICE-1: per-download cancellation events guarded by
        # a lock, so concurrent ``download_model`` IPC calls (via the
        # ThreadPoolExecutor) don't overwrite each other's event. The
        # previous single-instance attribute meant the second call's
        # ``self._download_cancel_event = threading.Event()`` clobbered
        # the first call's reference; the first call's polling loop then
        # polled the wrong event, and when the second call finished and
        # set the attribute to ``None`` the first call's
        # ``.is_set()`` raised AttributeError.
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._download_cancel_lock = threading.Lock()
        self._active_download_id: str | None = None
        # EC-FIX-15 / EC-24: the legacy single-instance
        # ``self._download_cancel_event`` attribute (retained as a test
        # seam for backwards-compat with tests that set/read it
        # directly) has been REMOVED.  Production code uses the
        # per-download dict above exclusively.  Callers that need to
        # signal a cancel must use ``_register_download`` /
        # ``_download_cancel_events[download_id]`` / ``_is_download_cancelled``.
        # PERF-FIX-1: short-TTL cache (5s) for refresh_microphones so
        # rapid refresh clicks don't re-query PortAudio each time.
        # XV-5: initialised to ``None`` (not ``[]``) so the cache check
        # can distinguish "never queried" from "queried and got 0 mics"
        # via an ``is not None`` guard. A bare-truthiness check would
        # bypass the cache when PortAudio legitimately returned an empty
        # list, re-querying PortAudio on every refresh call.
        self._microphones_cache: list | None = None
        self._microphones_cache_ts: float = 0.0
        # PERF-10 / SVC-9: short-TTL cache (5s) for get_model_status so the
        # renderer's 2s poll doesn't re-stat the filesystem for every model
        # on every call. The status is expensive to compute (N dir checks +
        # dependency probes). Invalidation is forced on download/delete.
        self._model_status_cache: dict | None = None
        self._model_status_cache_ts: float = 0.0
        self._model_status_cache_lock = threading.Lock()

    # ── Config ──────────────────────────────────────────────────

    def _keyring_status(self) -> dict[str, object]:
        """SVC-6: probe the OS keychain backend once and return a
        status dict shaped ``{available, backend, fallback, reason}``.

        Centralizes the duplicated try/except that previously lived in
        both :meth:`get_config` and :meth:`get_defaults`. Wrapping the
        ``credential_store.get_keyring_status()`` call here means a
        broken keyring library never breaks the IPC ``get_config`` /
        ``get_defaults`` paths (which would lock the renderer out of
        all settings). Both callers now route through this helper so
        the probe has a single source of truth.
        """
        try:
            from voice_typer.server import credential_store

            return credential_store.get_keyring_status()
        except Exception as exc:
            log.debug("[SERVICE] keyring_status probe failed: %s", exc)
            return {
                "available": False,
                "backend": None,
                "fallback": True,
                "reason": f"credential_store probe failed: {exc}",
            }

    def get_config(self) -> dict[str, object]:
        """Return the sanitized config (API keys redacted).

        RW-01: also includes a ``keyring_status`` field describing the
        OS keychain backend state, so the renderer can show
        "Stored securely in your OS keychain" indicators next to API
        key inputs (or a warning when only the plaintext fallback is
        available).
        """
        # EC-FIX-15 / EC-22: import the canonical sanitizer from the
        # transport-neutral ``config_sanitizer`` module instead of
        # reaching DOWN into the IPC transport layer (``ipc_server``),
        # which created a real import cycle (ipc_server imports
        # VoiceTyperService from this module).
        from voice_typer.server.config_sanitizer import sanitize_config_for_ipc

        sanitized = sanitize_config_for_ipc(self._app.config)
        # SVC-6: route through the shared helper (single try/except).
        sanitized["keyring_status"] = self._keyring_status()
        return sanitized

    def get_defaults(self) -> dict[str, object]:
        """Return default config values (sanitized).

        RW-01: includes the same ``keyring_status`` field as
        :meth:`get_config` so the renderer's "Reset to Defaults" flow
        can show the same keychain indicators.
        """
        from voice_typer.server.config import Config

        # EC-FIX-15 / EC-22: import the canonical sanitizer from the
        # transport-neutral ``config_sanitizer`` module — see
        # :meth:`get_config` for rationale.
        from voice_typer.server.config_sanitizer import sanitize_config_for_ipc

        sanitized = sanitize_config_for_ipc(Config())
        # SVC-6: route through the shared helper (single try/except).
        sanitized["keyring_status"] = self._keyring_status()
        return sanitized

    # PVT-G5-024 (High, partial): ``set_config`` and ``save_config``
    # were REMOVED from this service layer.
    #
    # Rationale:
    #   - ``set_config`` (validated-config helper) had 0 production
    #     callers — the IPC ``set_config`` command is implemented in
    #     ``handlers/config_handlers.py::_handle_set_config``, which
    #     calls ``config.validate_config_update`` directly and then
    #     delegates to ``service.apply_config`` (NOT this method).
    #   - ``save_config`` (``self._app.config.save()`` wrapper) had 0
    #     production callers; the IPC ``save_config`` command was
    #     removed in ERR-IPC-003.  ``Config.save()`` is now invoked
    #     inside ``service.apply_config`` under the config-mutation
    #     lock so disk writes can't race.
    #
    # Callers should use:
    #   - ``config.validate_config_update(updates)`` directly for
    #     validation, OR
    #   - ``service.apply_config(updates)`` for the full atomic
    #     validate→mutate→side-effects→save→tray-invalidate flow.
    #
    # Tests that pinned the old methods (notably
    # ``tests/fixtures/ipc_test_helpers.py:155`` which assigns
    # ``service.set_config.return_value = ...`` on a MagicMock, and
    # ``tests/test_di_providers.py:544`` which asserts ``set_config``
    # is declared on ``ServiceProtocol``) need follow-up updates —
    # see the FA11-retry return summary.

    # ── Lifecycle ───────────────────────────────────────────────

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()

    # ── Config side effects (ARCH-005) ──────────────────────────

    def apply_config_side_effects(self, updates: dict) -> dict:
        """Apply side effects after config changes. Delegates to ConfigApplier (CR-61 + CR-97, PVT-21).

        Returns
        -------
        dict
            PVT-060 (session-3): side-effect status dict from
            :meth:`ConfigApplier.apply_config_side_effects` (shape
            ``{"autostart_status": dict | None, "prewarm_status": dict | None}``).
            Callers that previously discarded the return value still work.
        """
        return self._config_applier.apply_config_side_effects(updates)

    def change_model(self, model_size: str) -> None:
        """Switch the active ASR model to ``model_size``.

        Wraps ``self._app.change_model()`` so the IPC ``set_config``
        handler doesn't call ``self.app.change_model()`` directly
        (ADR 0008 §3.1).
        """
        self._app.change_model(model_size)

    def set_active_backend(self, backend: str) -> None:
        """Set the active ASR backend (e.g. ``"whisper"``, ``"qwen"``).

        Wraps ``self._app.models.set_active_backend()`` so the IPC
        ``set_config`` handler doesn't reach into ``app.models``
        directly (ADR 0008 §3.1).
        """
        self._app.models.set_active_backend(backend)

    def apply_config(self, updates: dict) -> dict:
        """Apply validated config updates atomically. Delegates to ConfigApplier (CR-61 + CR-97, PVT-21).

        Returns
        -------
        dict
            PVT-060 (session-3): side-effect status dict from
            :meth:`ConfigApplier.apply_config` (shape
            ``{"autostart_status": dict | None, "prewarm_status": dict | None}``).
            Callers that previously discarded the return value still work.
        """
        return self._config_applier.apply_config(updates)

    # ── PROD-010: Export diagnostics ─────────────────────────────────

    def export_diagnostics(self) -> dict:
        """PROD-010: Create a diagnostic bundle for support.

        Delegates to CrashRecovery.create_diagnostic_bundle().
        Returns ``{"success": bool, "path": str}`` on success or
        ``{"success": False, "message": str}`` on failure.
        """
        try:
            # Use ``getattr`` instead of direct attribute access so the
            # static type checker doesn't flag the access (``self._app``
            # is typed as :class:`AppProtocol` which doesn't declare
            # ``_crash_recovery`` per ADR-0008-§3.1 — see
            # ``providers.py`` for the full rationale). ``getattr`` returns
            # ``Any`` to the type checker and is functionally equivalent at
            # runtime.
            recovery = self._app._crash_recovery
            if recovery is None:
                from voice_typer.server.crash_recovery import CrashRecovery

                recovery = CrashRecovery()
            path = recovery.create_diagnostic_bundle()
            if path:
                return {"success": True, "path": path}
            else:
                return {"success": False, "message": "Failed to create diagnostic bundle"}
        except Exception as exc:
            log.error("export_diagnostics failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    # ── Privacy / GDPR (CR-87 / CR-88) ───────────────────────────────
    #
    # CR-87 (GDPR Art. 17 right-to-erasure) and CR-88 (Art. 20
    # right-to-data-portability).  Both are wrapped by
    # :mod:`voice_typer.server.handlers.privacy_handlers` (thin IPC
    # envelopes that delegate to these service methods).  The handlers
    # pass through the service's return shape unchanged so the
    # renderer can show the user exactly which files were
    # deleted/exported and which failed.
    #
    # Personal-data file set (CR-87 / CR-88 spec):
    #
    #   * ``history.db``                       — transcription history
    #   * ``voice-typer-recovery.json``        — crash-recovery buffer
    #   * ``config.json``                      — user settings + secrets
    #   * ``voice-typer-corrections.json``     — vocabulary corrections
    #   * ``voice-typer-vocabulary.json``      — user vocabulary
    #   * ``voice-typer-templates.json``       — user templates
    #   * ``voice-typer.log``                  — runtime log (Python side)
    #   * ``voice-typer.log.*``                — rotated backups (.1..5)
    #   * ``prewarm.log`` / ``prewarm.log.*``  — prewarm process log
    #   * ``mic-test-*.wav``                   — mic-test recordings
    #   * ``crash_diagnostics.*.txt``          — Windows VEH crash file
    #   * ``python_crash.*.txt``               — Python excepthook marker
    #   * ``logs/`` (subdir)                   — Rust host rotating logs
    #
    # Electron-logs gap: ``<userData>/electron-main.log`` and
    # ``<userData>/electron-renderer-errors.log`` live in a DIFFERENT
    # directory (Electron's ``app.getPath("userData")``) that the Python
    # backend cannot reach from ``_config_dir()``.  The Electron host
    # must expose a ``deleteAllPersonalData`` IPC handler that unlinks
    # those files; this Python method cannot do it.  See
    # ``docs/privacy/gdpr-delete.md`` "Log files" section.
    #
    # Model weights (``<config_dir>/models/`` and
    # ``<config_dir>/huggingface/``) are explicitly EXCLUDED — they
    # are downloadable artifacts, not personal data.

    def delete_all_personal_data(self) -> dict:
        """GDPR Art. 17 — right to erasure.

        Delete every personal-data artifact the app owns (history DB,
        crash-recovery buffer, config + secrets, corrections /
        vocabulary / templates, runtime log + rotated backups, prewarm
        log, mic-test recordings, crash diagnostic files, archived
        crash diagnostics, and the Rust host's ``logs/`` subdirectory).
        Model weights are explicitly preserved — they are not personal
        data (CR-87 spec).

        G4-CR-04: SQLite WAL sidecars (``history.db-wal`` /
        ``history.db-shm``) are unlinked alongside ``history.db``,
        and ``hdb.checkpoint(truncate=True)`` + ``hdb.close()`` are
        called BEFORE the unlink so the writer thread releases its
        file descriptor and the WAL is empty when removed.  Without
        this, dictated plaintext remains recoverable from the WAL by
        any process with filesystem access.

        G4-CR-05: After file deletion, also iterates
        ``credential_store.PROVIDER_TO_CONFIG_FIELD`` and calls
        ``credential_store.delete_secret(provider, config=app.config)``
        for each provider (openai / groq / deepgram / cloud / llm) —
        removing the entry from the OS keychain (with plaintext
        fallback for headless Linux), clearing the on-disk reference
        token in config.json, AND zeroing the in-memory ``Config``
        attribute.  Finally calls
        ``credential_store.clear_in_memory_secrets(app.config)`` as
        a belt-and-suspenders pass, and invalidates the cached
        ``LLMPolisher`` (``app._llm_polisher = None``) so the next
        polish request rebuilds with empty credentials rather than
        reusing a cached client bound to the now-deleted key.

        G4-M-33: ``crash_diagnostics_archive/`` (where the crash
        handler moves processed crash dumps — see agent 2-p's
        crash_handler change) is also recursively removed.  Without
        this, archived crash dumps (which may contain memory
        snapshots) survive the GDPR delete.

        Returns::

            {"success": bool,
             "erased": ["/path/to/history.db", ...],
             "failed": {"/path/to/locked.log": "PermissionError: ..."}}

        ``success`` is ``True`` if no failures occurred; the renderer
        uses ``failed`` to show the user which files could not be
        deleted (e.g. locked by another process) so they can manually
        delete them.  A fresh-install config dir (no artifacts) is
        treated as success — there's nothing to erase, but the user's
        right to erasure is satisfied.
        """
        import shutil

        from voice_typer.server import credential_store
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        erased: list = []
        failed: dict = {}

        # ── G4-CR-04: checkpoint + close the live HistoryDB writer
        # BEFORE unlinking so the WAL is empty when removed and the
        # writer thread releases its file descriptor (Windows refuses
        # to unlink an open file).  Wrapped in try/except so a failure
        # here doesn't abort the GDPR delete — the file unlink loop
        # below will still try to remove the files (and report any
        # PermissionError in ``failed``).  ``checkpoint`` is added by
        # agent 2-b; if the method is missing on this build we skip
        # gracefully (the WAL sidecar unlink below still clears stale
        # WAL contents, but dictated plaintext written since the last
        # passive checkpoint may be recoverable in that case).
        hdb = getattr(self._app, "history_db", None)
        if hdb is not None:
            try:
                checkpoint_fn = getattr(hdb, "checkpoint", None)
                if callable(checkpoint_fn):
                    try:
                        checkpoint_fn(truncate=True)
                    except TypeError:
                        # Method exists but doesn't accept truncate= kwarg
                        # (older signature) — try positional.
                        try:
                            checkpoint_fn(True)
                        except Exception:
                            log.debug(
                                "[SERVICE] GDPR delete: hdb.checkpoint(True) failed",
                                exc_info=True,
                            )
                    except Exception:
                        log.debug(
                            "[SERVICE] GDPR delete: hdb.checkpoint(truncate=True) failed",
                            exc_info=True,
                        )
            except Exception:
                log.debug(
                    "[SERVICE] GDPR delete: hdb.checkpoint access failed",
                    exc_info=True,
                )
            try:
                hdb.close()
            except Exception:
                log.debug(
                    "[SERVICE] GDPR delete: hdb.close() before unlink failed",
                    exc_info=True,
                )

        # ── Recursively remove the Rust host's ``logs/``
        # subdirectory (``<config_dir>/logs/voice-typer.log`` +
        # rotated backups ``.log.1``..``.log.4`` — written by
        # ``src-tauri/src/platform/logging.rs:30-34``).  The Python
        # glob walk below only matches files at the ``config_dir``
        # root, so without this step the entire Rust log tree survives
        # GDPR delete.  Per XZ-LOG-02 the Rust logger has no PII
        # redaction, so dictated-text fragments may be present.
        # Best-effort: the ``exists()`` guard makes a missing dir
        # (fresh install, or pre-Tauri-migration build) a silent no-op,
        # and a per-file OSError is caught + surfaced in ``failed``
        # (WARNING-log) so the renderer can tell the user to manually
        # delete the directory rather than silently swallowing the
        # failure (per project rule "no silent swallows").
        rust_logs_dir = config_dir / "logs"
        if rust_logs_dir.exists():
            try:
                shutil.rmtree(rust_logs_dir, ignore_errors=False)
                erased.append(str(rust_logs_dir))
                log.debug(
                    "[SERVICE] GDPR delete: removed Rust logs/ dir at %s",
                    rust_logs_dir,
                )
            except OSError as exc:
                # Surface the failure in ``failed`` (WARNING-log)
                # because the Rust logs may contain PII — the user
                # should be told to manually delete the directory.
                log.warning(
                    "[SERVICE] GDPR delete: could not rmtree Rust logs/ dir "
                    "at %s: %s — user may need to delete it manually",
                    rust_logs_dir,
                    exc,
                )
                failed[str(rust_logs_dir)] = f"{type(exc).__name__}: {exc}"

        # 1. Hardcoded personal-data files.
        # G4-CR-04: wrap unlink in try/except PermissionError so a
        # locked file (Windows: file open in another process; POSIX:
        # EBUSY on rare mount points) is reported in ``failed`` rather
        # than aborting the whole GDPR delete.
        for name in self._GDPR_PERSONAL_FILES:
            path = config_dir / name
            if not path.exists():
                continue
            try:
                path.unlink()
                erased.append(str(path))
            except PermissionError as exc:
                failed[str(path)] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                failed[str(path)] = f"{type(exc).__name__}: {exc}"

        # 2. Glob-pattern personal-data files (mic-test recordings,
        # rotated log backups, crash diagnostic files — see
        # ``_GDPR_PERSONAL_GLOBS`` for the per-pattern rationale).
        for pattern in self._GDPR_PERSONAL_GLOBS:
            for path in config_dir.glob(pattern):
                try:
                    path.unlink()
                    erased.append(str(path))
                except PermissionError as exc:
                    failed[str(path)] = f"{type(exc).__name__}: {exc}"
                except Exception as exc:
                    failed[str(path)] = f"{type(exc).__name__}: {exc}"

        # ── G4-M-33: remove archived crash diagnostics (agent 2-p's
        # crash_handler moves processed dumps here instead of unlinking
        # them so the diagnostic bundle can include them).  Best-effort:
        # if the directory doesn't exist (fresh install, or older build
        # that hasn't picked up 2-p's change yet), this is a no-op.  If
        # shutil.rmtree hits a PermissionError on a child file, the
        # directory path is added to ``failed`` rather than aborting.
        archive_dir = config_dir / "crash_diagnostics_archive"
        if archive_dir.exists():
            try:
                shutil.rmtree(archive_dir)
                erased.append(str(archive_dir))
            except PermissionError as exc:
                failed[str(archive_dir)] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                failed[str(archive_dir)] = f"{type(exc).__name__}: {exc}"

        # ── G4-CR-05: clear OS keychain entries + in-memory Config
        # attributes for every provider.  ``delete_secret`` is
        # best-effort (never raises) — it removes the keychain entry,
        # clears the on-disk reference token in config.json, AND (when
        # ``config=`` is passed) zeros the in-memory attribute.  We pass
        # ``config=app.config`` so all three stores are cleared in one
        # call per provider.  Failures (e.g. keyring backend broken)
        # are logged inside ``delete_secret``; we surface them in
        # ``failed`` so the renderer can show the user which providers
        # could not be cleared from the keychain.
        app = self._app
        app_config = getattr(app, "config", None)
        for provider in credential_store.PROVIDER_TO_CONFIG_FIELD:
            try:
                credential_store.delete_secret(provider, config=app_config)
            except Exception as exc:
                key = f"keychain:{provider}"
                failed[key] = f"{type(exc).__name__}: {exc}"

        # Belt-and-suspenders: zero every api_key attribute on the
        # in-memory Config (covers any provider whose delete_secret
        # call above didn't get to setattr, e.g. because of an early
        # return inside delete_secret — currently impossible, but
        # defense in depth).
        if app_config is not None:
            try:
                credential_store.clear_in_memory_secrets(app_config)
            except Exception as exc:
                failed["in_memory_config"] = f"{type(exc).__name__}: {exc}"

        # Invalidate the cached LLMPolisher / CloudEngine instances so
        # the next request rebuilds them with the (now-empty) API key
        # rather than reusing a client bound to the deleted credential.
        # ``apply_config`` already does this when an ``llm_*`` field
        # changes, but the GDPR delete path bypasses ``apply_config``
        # (it deletes the on-disk file directly), so we invalidate here
        # explicitly.  ``contextlib.suppress`` because the attribute
        # may not exist on fresh installs / test mocks.
        #
        # Use ``setattr`` instead of direct attribute assignment so the
        # static type checker doesn't flag the access (``app`` is typed
        # as :class:`AppProtocol` which doesn't declare ``_llm_polisher``
        # / ``_cloud_engine`` per ADR-0008-§3.1 — see ``providers.py``
        # for the full rationale). ``setattr`` returns ``Any`` to the
        # type checker and is functionally equivalent at runtime.
        with contextlib.suppress(Exception):
            app._llm_polisher = None
        with contextlib.suppress(Exception):
            app._cloud_engine = None

        # ── G4-CR-04: re-create the live HistoryDB instance so the app
        # can keep accepting dictations after the GDPR delete.  The
        # writer thread was shut down by ``hdb.close()`` above; without
        # re-creation, the next ``add_transcription`` call would raise
        # (or silently drop the write) because the writer queue is
        # closed.  We construct a fresh ``HistoryDB`` at the default
        # path (``<config_dir>/history.db``) — HistoryDB.__init__ will
        # re-create the file with a fresh schema on first write.  Best-
        # effort: if construction fails (e.g. disk full, permissions),
        # log and leave ``app.history_db`` as the closed instance — the
        # user will see a "history DB unavailable" warning on the next
        # dictation, but the GDPR delete itself succeeded.
        if hdb is not None:
            try:
                from voice_typer.server.history_db import HistoryDB

                new_hdb = HistoryDB()
                # ``app`` is typed as :class:`AppProtocol` (which
                # declares ``history_db``), so the assignment type-checks
                # cleanly without an attr-defined suppression marker.
                app.history_db = new_hdb
            except Exception:
                log.debug(
                    "[SERVICE] GDPR delete: could not re-create HistoryDB after erase",
                    exc_info=True,
                )

        log.info(
            "[SERVICE] GDPR Art. 17 delete: erased %d file(s)/dir(s), %d failure(s)",
            len(erased),
            len(failed),
        )
        result: dict = {"success": not failed, "erased": erased}
        if failed:
            result["failed"] = failed
        return result

    def reset_config_to_defaults(self, *, preserve_api_keys: bool = True) -> dict:
        """G4-L-25: factory-reset the in-memory + on-disk config to defaults.

        Snapshots the current ``config.json`` to ``config.json.bak``
        (so the user can recover their settings if they clicked
        "Reset to defaults" by mistake), then constructs a fresh
        :class:`Config` (all defaults) and — by default — preserves
        the 5 API-key fields (``openai_api_key`` / ``groq_api_key`` /
        ``deepgram_api_key`` / ``cloud_api_key`` / ``llm_api_key``)
        from the pre-reset config so the user doesn't have to re-enter
        their keys after a reset.  Set ``preserve_api_keys=False`` to
        also wipe API keys (rare; the GDPR delete path is the right
        tool for that — it also clears the keychain).

        This method does NOT touch:

          * ``history.db`` (transcription history — GDPR Art. 17
            delete is a separate, intentional action).
          * ``voice-typer-corrections.json`` / ``vocabulary.json`` /
            ``templates.json`` (user customizations — preserved across
            a factory reset).
          * ``voice-typer.log`` (runtime log — rotated normally).
          * OS keychain entries (only the in-memory + on-disk config
            are reset).

        Acquires ``app._config_mutation_lock`` so a concurrent
        ``set_config`` IPC call can't interleave attribute writes
        with the reset.  Calls ``Config.save_strict()`` so a disk
        failure is surfaced as a ``RuntimeError`` rather than a
        silent success.  Invalidates the cached ``LLMPolisher`` so
        the next polish request rebuilds with the reset config.

        Agent 2-j wires the IPC handler that calls this method
        (``config_handlers.reset_config_to_defaults``).

        Returns::

            {"success": bool,
             "backup_path": "/path/to/config.json.bak"}

        On backup or save failure, returns::

            {"success": False, "message": "..."}
        """
        import shutil

        from voice_typer.server import credential_store
        from voice_typer.server.config import Config, _config_dir

        app = self._app
        # Use ``getattr`` instead of direct attribute access so the
        # static type checker doesn't flag the access (``app`` is typed
        # as :class:`AppProtocol` which doesn't declare
        # ``_config_mutation_lock`` per ADR-0008-§3.1 — see
        # ``providers.py`` for the full rationale). ``getattr`` returns
        # ``Any`` to the type checker and is functionally equivalent at
        # runtime.
        with app._config_mutation_lock:
            config_dir = _config_dir()
            config_file = config_dir / "config.json"
            backup_path = config_dir / "config.json.bak"

            # 1. Snapshot current config.json → config.json.bak.
            # Best-effort: if config.json doesn't exist (fresh
            # install), skip the backup.  If the backup write fails
            # (disk full, permissions), return failure — we don't
            # want to reset without a recovery path.
            if config_file.exists():
                try:
                    shutil.copy2(config_file, backup_path)
                except OSError as exc:
                    log.error("[SERVICE] reset_config_to_defaults: backup failed: %s", exc)
                    return {
                        "success": False,
                        "message": "failed to back up current config (see log)",
                    }

            # 2. Snapshot the API-key fields from the live Config
            # (these hold the REAL values, not the keyring://
            # reference tokens — see ``Config.load``).  We preserve
            # them so the user doesn't have to re-enter their keys
            # after a factory reset.
            preserved_keys: dict[str, str] = {}
            old_config = getattr(app, "config", None)
            if preserve_api_keys and old_config is not None:
                for field in credential_store.PROVIDER_TO_CONFIG_FIELD.values():
                    try:
                        value = getattr(old_config, field, "")
                    except Exception:
                        value = ""
                    if value:
                        preserved_keys[field] = value

            # 3. Construct a fresh Config (all defaults).
            new_config = Config()

            # 4. Re-apply preserved API keys.
            for field, value in preserved_keys.items():
                try:
                    setattr(new_config, field, value)
                except Exception:
                    log.debug(
                        "[SERVICE] reset_config_to_defaults: could not restore %s",
                        field,
                        exc_info=True,
                    )

            # 5. Save to disk (raises on failure — see Config.save_strict).
            try:
                # Swap the in-memory Config BEFORE save so save() reads
                # the new defaults (and routes preserved API keys
                # through credential_store if keyring is available).
                app.config = new_config
                new_config.save_strict()
            except Exception as exc:
                log.error("[SERVICE] reset_config_to_defaults: save_strict failed: %s", exc)
                return {
                    "success": False,
                    "message": "failed to persist reset config to disk (see log)",
                }

            # 6. Invalidate cached LLMPolisher / CloudEngine so the
            # next request rebuilds with the reset config.
            #
            # Use ``setattr`` (see the GDPR-delete path above for the
            # full rationale).
            with contextlib.suppress(Exception):
                app._llm_polisher = None
            with contextlib.suppress(Exception):
                app._cloud_engine = None

            log.info(
                "[SERVICE] reset_config_to_defaults: reset to defaults, backup at %s, preserved %d API key(s)",
                backup_path,
                len(preserved_keys),
            )
            return {
                "success": True,
                "backup_path": str(backup_path) if backup_path.exists() else "",
            }

    def export_gdpr_bundle(self) -> dict:
        """GDPR Art. 20 — right to data portability.

        Produce a single timestamped ``.zip`` at
        ``<config_dir>/gdpr-export-YYYYMMDD-HHMMSS.zip`` containing
        every personal-data artifact the app owns (the same set as
        :meth:`delete_all_personal_data`).  Unlike
        :meth:`export_diagnostics` (which redacts PII for a support
        ticket bundle), this export is the user's OWN data verbatim —
        no redaction.  Model weights are excluded (not personal data).

        G4-M-46: before zipping ``history.db``, calls
        ``hdb.checkpoint(truncate=True)`` on the live HistoryDB
        writer so the WAL is merged into the main DB file.  Without
        this, the exported ``history.db`` is unparseable — SQLite
        refuses to open a WAL-mode DB whose ``-wal`` sidecar is
        absent, and the WAL sidecar is NOT included in the zip (it
        would be stale by the time the user unzips the export on
        another machine).

        G4-L-26: after creating the zip, rotates
        ``gdpr-export-*.zip`` files in the config dir — keeps the
        most recent 5 (by mtime), unlinks older ones.  Without
        rotation, repeated GDPR exports accumulate unboundedly (each
        is 1-50 MB depending on history size).

        Returns::

            {"success": bool,
             "path": "/tmp/.../gdpr-export-20240101-120000.zip"}

        On failure (e.g. the config dir is not writable), returns::

            {"success": False, "message": "..."}.

        A fresh-install config dir (no artifacts) still produces a
        (mostly empty) zip rather than raising — the user's right to
        portability is satisfied even if there's nothing to export.
        """
        import time as _time
        import zipfile as _zipfile

        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        timestamp = _time.strftime("%Y%m%d-%H%M%S")
        zip_path = config_dir / f"gdpr-export-{timestamp}.zip"

        # ── G4-M-46: checkpoint the live HistoryDB writer BEFORE
        # zipping ``history.db`` so the WAL is merged into the main DB.
        # Without this, the exported ``history.db`` is unparseable:
        # SQLite opens WAL-mode DBs by first reading ``history.db-wal``
        # to apply pending transactions, and the WAL sidecar is not
        # included in the export (it's a transient file).  Checkpoint
        # + truncate ensures all dictated text is in ``history.db``
        # proper and the WAL is empty.  Best-effort: if the writer is
        # not running (fresh install, or ``checkpoint`` method missing
        # on this build — agent 2-b is adding it), we skip gracefully.
        hdb = getattr(self._app, "history_db", None)
        if hdb is not None:
            checkpoint_fn = getattr(hdb, "checkpoint", None)
            if callable(checkpoint_fn):
                try:
                    try:
                        checkpoint_fn(truncate=True)
                    except TypeError:
                        # Older signature without truncate= kwarg.
                        checkpoint_fn(True)
                except Exception:
                    log.debug(
                        "[SERVICE] GDPR export: hdb.checkpoint(truncate=True) failed",
                        exc_info=True,
                    )

        # Build the zip to a temp path (``.zip.tmp``) in the
        # same directory, then ``os.replace`` to the final path on
        # success.  ``ZipFile(zip_path, "w", ...)`` truncates the
        # destination incrementally — if the process is killed mid-zip
        # (or disk fills, or an ``zf.write`` raises), the user is left
        # with a partial/corrupt ``.zip`` that may open but be missing
        # entries, or fail CRC checks on extract.  The GDPR export is
        # a user-triggered compliance operation (Art. 20 data
        # portability); a silently-truncated zip is a legal/compliance
        # risk.  Building to ``.zip.tmp`` + ``os.replace`` is atomic
        # on POSIX (rename(2)) and atomic-ish on Windows
        # (MoveFileExW with MOVEFILE_REPLACE_EXISTING via
        # ``os.replace``).  On failure we unlink the temp file so no
        # partial artifact is left, then surface the failure to the
        # renderer as a structured ``{"success": False, "message": …}``.
        tmp_path = zip_path.with_suffix(".zip.tmp")
        try:
            with _zipfile.ZipFile(tmp_path, "w", _zipfile.ZIP_DEFLATED) as zf:
                # 1. Hardcoded personal-data files.
                for name in self._GDPR_PERSONAL_FILES:
                    path = config_dir / name
                    if path.exists() and path.is_file():
                        try:
                            zf.write(path, arcname=name)
                        except Exception as exc:
                            log.debug(
                                "[SERVICE] GDPR export: could not add %s to zip: %s",
                                path,
                                exc,
                            )
                # 2. Glob-pattern personal-data files (mic-test
                # recordings, rotated log backups ``voice-typer.log.*``
                # + ``prewarm.log.*``, crash diagnostic
                # files ``crash_diagnostics.*.txt`` +
                # ``python_crash.*.txt``).
                for pattern in self._GDPR_PERSONAL_GLOBS:
                    for path in config_dir.glob(pattern):
                        if not path.is_file():
                            continue
                        try:
                            zf.write(path, arcname=path.name)
                        except Exception as exc:
                            log.debug(
                                "[SERVICE] GDPR export: could not add %s to zip: %s",
                                path,
                                exc,
                            )
            # ZipFile block completed successfully — atomic
            # rename of the completed temp zip into the final path.
            # ``os.replace`` is atomic on POSIX (rename(2)) and
            # replaces any existing destination on Windows
            # (MoveFileExW with MOVEFILE_REPLACE_EXISTING).  If
            # ``os.replace`` itself fails (e.g. cross-filesystem —
            # should not happen since both paths are in the same
            # dir), we unlink the temp file and let the outer
            # ``except`` surface the failure.
            try:
                os.replace(tmp_path, zip_path)
            except OSError:
                with contextlib.suppress(FileNotFoundError):
                    tmp_path.unlink()
                raise
        except Exception as exc:
            # Unlink any partial temp file so no corrupt
            # artifact is left on disk.  ``suppress(FileNotFoundError)``
            # because the temp file may not exist (ZipFile failed
            # before opening it, OR the inner ``os.replace`` handler
            # already unlinked it).  Log + return structured failure.
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
            log.error("export_gdpr_bundle failed: %s", exc)
            return {
                "success": False,
                "message": redact_secret(redact_url(str(exc))),
            }

        # ── G4-L-26: rotate ``gdpr-export-*.zip`` — keep most recent
        # 5 (by mtime), unlink older.  Without rotation, repeated
        # exports accumulate unboundedly.  We sort by mtime descending
        # and unlink everything past the 5th.  Best-effort: a
        # PermissionError on unlink is logged but does not fail the
        # export (the new zip was already written successfully).
        try:
            exports = sorted(
                config_dir.glob("gdpr-export-*.zip"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in exports[5:]:
                try:
                    stale.unlink()
                except PermissionError as exc:
                    log.debug(
                        "[SERVICE] GDPR export rotation: could not unlink %s: %s",
                        stale,
                        exc,
                    )
                except Exception as exc:
                    log.debug(
                        "[SERVICE] GDPR export rotation: could not unlink %s: %s",
                        stale,
                        exc,
                    )
        except Exception:
            log.debug(
                "[SERVICE] GDPR export rotation: glob/stat failed",
                exc_info=True,
            )

        log.info(
            "[SERVICE] GDPR Art. 20 export: wrote %s (%d bytes)",
            zip_path,
            zip_path.stat().st_size if zip_path.exists() else 0,
        )
        return {"success": True, "path": str(zip_path)}


__all__ = [
    "APP_NAME",
    "ConfigApplier",
    "DownloadCancelled",
    "DownloadConsentRequired",
    "DownloadError",
    "DownloadResult",
    "DownloadSuccess",
    "ForceCancelResult",
    "StatusResponse",
    "VoiceTyperService",
    "_MODEL_STATUS_CACHE_TTL_S",
    "_apply_audio_preset",
    "_find_symlink_in_tree",
]
