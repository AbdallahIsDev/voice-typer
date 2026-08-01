"""Privacy / GDPR domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( / Phase 4.5 spaghetti split). Owns the two cross-cutting
privacy methods that don't belong to a single domain mixin:

* :meth:`PrivacyMixin.delete_all_personal_data`   — GDPR Art. 17
* :meth:`PrivacyMixin.export_gdpr_bundle`          — GDPR Art. 20

These methods previously lived on :class:`VoiceTyperService` itself
because they touch *every* domain (history DB, config, logs, keychain,
crash archives). They are extracted here as a :class:`PrivacyMixin`
so :class:`VoiceTyperService` shrinks back to a thin composition
root (``__init__`` + ``restart`` / ``quit`` + the TypedDict response
shapes). Every public method name and signature is preserved verbatim;
the mixin is composed via multiple inheritance so
``VoiceTyperService.delete_all_personal_data`` resolves to
``PrivacyMixin.delete_all_personal_data`` (MRO), which is what the
regression guards in ``tests/test_gdpr_delete.py`` /
``tests/test_gdpr_export.py`` / ``tests/test_reset_config_to_defaults.py``
assert via ``hasattr(VoiceTyperService, ...)``.

Refactor note: the two public methods are now thin (~20-LOC)
orchestrators. The per-step work is delegated to private
``@staticmethod`` helpers below (``_gdpr_checkpoint_history_db`` /
``_gdpr_unlink_personal_files`` / ``_gdpr_unlink_personal_globs`` /
``_gdpr_rmtree_rust_logs`` / ``_gdpr_rmtree_crash_archive`` /
``_gdpr_clear_keychain`` / ``_gdpr_invalidate_cached_engines`` /
``_gdpr_invalidate_managers`` / ``_gdpr_recreate_history_db`` /
``_gdpr_post_cleanup_sweep`` / ``_gdpr_build_zip`` /
``_gdpr_rotate_exports``). Each helper owns
one well-named slice of the GDPR pipeline; the orchestrator's job
is to call them in order and assemble the result dict. No behavior
change — the public return shapes and side effects are identical to
the pre-refactor implementation, and the existing
``tests/test_gdpr_*.py`` suite continues to pass unmodified.
"""

import contextlib
import logging
import os

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server._user_data_files import _GDPR_PERSONAL_FILES
from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class PrivacyMixin(ServiceMixinBase):
    """GDPR right-to-erasure (Art. 17) and data portability (Art. 20).

    Both methods access ``self._app`` (the wrapped
    :class:`VoiceTyperApp` instance) and walk the user's ``config_dir``
    for personal-data artifacts.  The artifact set is defined by the
    two class constants below so :meth:`delete_all_personal_data` and
    :meth:`export_gdpr_bundle` stay in lock-step (the export must
    contain exactly the files the delete would erase — minus the
    keychain entries, which are OS-managed and not zippable).

    The factory-reset method (``reset_config_to_defaults``) lives on
    :class:`ConfigMutationMixin` since it is config-mutation domain,
    not privacy: it acquires ``_config_mutation_lock``, calls
    ``Config.save_strict()``, and invalidates cached engines — the
    same pattern as ``ConfigMutationMixin.apply_config``.
    """

    # Hardcoded list of personal-data file names (not glob patterns)
    # to delete / export.  Glob patterns are handled separately below.
    #
    # The tuple itself is imported from
    # :mod:`voice_typer.server._user_data_files` (the single source of
    # truth shared with :func:`voice_typer.server.config.purge_user_data`)
    # so the GDPR delete path and the uninstall-purge path cannot drift
    # from each other or from the canonical ``*_FILENAME`` constants
    # owned by each artifact's module. The detailed per-entry rationale
    # is documented at the definition site in ``_user_data_files.py``.
    #
    # ``history.db-wal`` and ``history.db-shm`` are SQLite's
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
    _GDPR_PERSONAL_FILES: tuple = _GDPR_PERSONAL_FILES
    # Glob patterns for personal-data files with timestamped / rotated
    # names.  See ``delete_all_personal_data`` / ``export_gdpr_bundle``
    # for the walk — both iterate this tuple against ``config_dir``.
    #
    # ``voice-typer.log.*`` matches the rotating log handler's
    # backups ``voice-typer.log.1`` .. ``voice-typer.log.5`` (set in
    # ``voice_typer/server/log.py`` via
    # ``RotatingFileHandler(backupCount=5)``).  Without this glob the
    # rotated backups survive GDPR delete — and per  the
    # rotating log file contains user-spoken text via
    # ``_crash_excepthook``'s CRITICAL log + per-segment DEBUG logs
    # (), so the leftover backups are a real Art. 17 gap.
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
        # Electron renderer-error log rotated backups. The current
        # structured-logger implementation does NOT rotate this file
        # (see ``structuredLogger.ts:207``: "single file, but the glob
        # ``electron-renderer-errors.log*`` also catches any future
        # rotation"), but a future change may add a rotating handler —
        # this glob ensures rotated backups (if/when they exist) are
        # swept up by the GDPR delete / export walk alongside the
        # canonical file above.
        "electron-renderer-errors.log.*",
        # history.db.corrupt-* retains dictated plaintext
        "history.db.corrupt-*",
        # voice-typer-diagnostics-*.zip contains PII
        "voice-typer-diagnostics-*.zip",
        # gdpr-export-*.zip contains user full personal data
        "gdpr-export-*.zip",
        # (High): four config-backup file classes ALL contain
        # full on-disk config.json including plaintext API keys (when
        # keyring is unavailable). Pre- these survived GDPR
        # Art. 17 delete — a direct right-to-erasure violation.
        #
        # ``config.json.v*.bak`` — versioned-downgrade backups from
        # ``Config._backup_before_downgrade`` ( made the
        # filename timestamped: ``config.json.v{N}-{ts}-{pid}-{ns}.bak``,
        # but the glob ``config.json.v*.bak`` also catches the legacy
        # single-slot ``config.json.v{N}.bak`` from pre- builds).
        #
        # ``config.json.pre-migration-v*.bak`` — pre-migration backups
        # from ``Config._backup_before_migration`` (timestamped:
        # ``config.json.pre-migration-v{N}-{ts}-{pid}-{ns}.bak``).
        #
        # ``config.json.bak.failed-migration-*`` — failed-migration
        # backups from ``config_internals/migrations.py:_run_migrations``
        # (timestamped: ``config.json.bak.failed-migration-{ts}-to-v{N}``).
        #
        # ``config.json.corrupt-*`` — corrupt-quarantine backups from
        # ``Config.load`` (timestamped:
        # ``config.json.corrupt-{ts}-{pid}-{ns}``).
        "config.json.v*.bak",
        "config.json.pre-migration-v*.bak",
        "config.json.bak.failed-migration-*",
        "config.json.corrupt-*",
        # (Medium): history.db.pre-migration-v* is a
        # byte-for-byte copy of the full history DB made by
        # ``HistoryDB._backup_before_migration`` before schema
        # migration. Contains all dictated text in plaintext. Pre-
        # this survived GDPR Art. 17 delete — same gap as
        # ``history.db.corrupt-*`` () which was already
        # covered.
        "history.db.pre-migration-v*",
    )

    # Privacy / GDPR ( / ) ───────────────────────────────
    #
    # (GDPR Art. 17 right-to-erasure) and  (Art. 20
    # right-to-data-portability).  Both are wrapped by
    # :mod:`voice_typer.server.handlers.privacy_handlers` (thin IPC
    # envelopes that delegate to these service methods).  The handlers
    # pass through the service's return shape unchanged so the
    # renderer can show the user exactly which files were
    # deleted/exported and which failed.
    #
    # Personal-data file set ( /  spec):
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

    # ───────────────────────────────────────────────────────────────────
    # Refactor: private helpers extracted from the two public methods.
    # Each helper owns one slice of the GDPR pipeline.  They are
    # ``@staticmethod``s because they don't need ``self`` — they
    # operate on the parameters passed in (``config_dir``, ``hdb``,
    # ``app``, the ``erased`` / ``failed`` accumulators).  Keeping
    # them on the class (rather than as module-level functions) lets
    # a future ``PrivacyMixin`` subclass override one helper without
    # touching the orchestrator (e.g. a test double that skips the
    # keychain clear).
    # ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _gdpr_checkpoint_history_db(hdb: object, *, close: bool = False) -> None:
        """checkpoint the live HistoryDB writer.

        Calls ``hdb.checkpoint(truncate=True)`` so the WAL is merged
        into the main ``history.db`` file.  Without this, dictated
        plaintext remains recoverable from the WAL sidecar by any
        process with filesystem access (Art. 17 violation on delete),
        and the exported ``history.db`` is unparseable on Art. 20
        export (SQLite refuses to open a WAL-mode DB whose ``-wal``
        sidecar is absent).

        Best-effort: if ``checkpoint`` is missing on this build (older
        signature) or raises, the failure is logged at DEBUG and the
        caller proceeds — the WAL sidecar unlink / non-inclusion is
        still attempted, but stale plaintext written since the last
        passive checkpoint may be recoverable in that case.

        Parameters
        ----------
        hdb
            The live ``HistoryDB`` instance (or any duck-typed object
            exposing ``checkpoint`` / ``close``).
        close
            If ``True``, also call ``hdb.close()`` so the writer
            thread releases its file descriptor (Windows refuses to
            unlink an open file).  Used by the delete path; the
            export path leaves the writer running because the user
            may continue dictating after the export.
        """
        if hdb is None:
            return
        try:
            checkpoint_fn = getattr(hdb, "checkpoint", None)
            if callable(checkpoint_fn):
                try:
                    try:
                        checkpoint_fn(truncate=True)
                    except TypeError:
                        # Method exists but doesn't accept truncate= kwarg
                        # (older signature) — try positional.
                        try:
                            checkpoint_fn(True)
                        except Exception:
                            log.debug(
                                "[SERVICE] GDPR: hdb.checkpoint(True) failed",
                                exc_info=True,
                            )
                except Exception:
                    log.debug(
                        "[SERVICE] GDPR: hdb.checkpoint(truncate=True) failed",
                        exc_info=True,
                    )
        except Exception:
            log.debug(
                "[SERVICE] GDPR: hdb.checkpoint access failed",
                exc_info=True,
            )
        if close:
            try:
                hdb.close()  # type: ignore[attr-defined]
            except Exception:
                log.debug(
                    "[SERVICE] GDPR: hdb.close() before unlink failed",
                    exc_info=True,
                )

    @staticmethod
    def _gdpr_unlink_personal_files(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """Unlink each hardcoded personal-data file in ``config_dir``.

                Walks :data:`_GDPR_PERSONAL_FILES` and unlinks each existing
        file.  : each unlink is wrapped in
                ``try/except PermissionError`` so a locked file (Windows: file
                open in another process; POSIX: EBUSY on rare mount points) is
                reported in ``failed`` rather than aborting the whole GDPR
                delete.
        """
        from pathlib import Path

        for name in PrivacyMixin._GDPR_PERSONAL_FILES:
            path = Path(config_dir) / name
            if not path.exists():
                continue
            try:
                path.unlink()
                erased.append(str(path))
            except PermissionError as exc:
                failed[str(path)] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                failed[str(path)] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_unlink_personal_globs(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """Unlink each glob-matched personal-data file in ``config_dir``.

        Walks :data:`_GDPR_PERSONAL_GLOBS` (mic-test recordings,
        rotated log backups, crash diagnostic files — see the per-
        pattern rationale on the constant).  Same per-unlink error
        handling as :meth:`_gdpr_unlink_personal_files`.
        """
        from pathlib import Path

        for pattern in PrivacyMixin._GDPR_PERSONAL_GLOBS:
            for path in Path(config_dir).glob(pattern):
                try:
                    path.unlink()
                    erased.append(str(path))
                except PermissionError as exc:
                    failed[str(path)] = f"{type(exc).__name__}: {exc}"
                except Exception as exc:
                    failed[str(path)] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_rmtree_rust_logs(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """Recursively remove the Rust host's ``logs/`` subdirectory.

                ``<config_dir>/logs/voice-typer.log`` + rotated backups
                ``.log.1``..``.log.4`` are written by
                ``src-tauri/src/platform/logging.rs:30-34``.  The Python glob
                walk in :meth:`_gdpr_unlink_personal_globs` only matches files
                at the ``config_dir`` root, so without this step the entire
        Rust log tree survives GDPR delete.  Per  the Rust
                logger has no PII redaction, so dictated-text fragments may
                be present.

                Best-effort: the ``exists()`` guard makes a missing dir
                (fresh install, or pre-Tauri-migration build) a silent no-op,
                and a per-file OSError is caught + surfaced in ``failed``
                (WARNING-log) so the renderer can tell the user to manually
                delete the directory rather than silently swallowing the
                failure (per project rule "no silent swallows").
        """
        import shutil
        from pathlib import Path

        rust_logs_dir = Path(config_dir) / "logs"
        if not rust_logs_dir.exists():
            return
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

    @staticmethod
    def _gdpr_rmtree_crash_archive(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """remove archived crash diagnostics directory.

        ``crash_diagnostics_archive/`` is where the crash handler moves
        processed crash dumps (instead of unlinking them so the
        diagnostic bundle can include them).  Best-effort: if the
        directory doesn't exist (fresh install, or older build that
        hasn't picked up the crash_handler change), this is a no-op.
        If ``shutil.rmtree`` hits a ``PermissionError`` on a child
        file, the directory path is added to ``failed`` rather than
        aborting.
        """
        import shutil
        from pathlib import Path

        archive_dir = Path(config_dir) / "crash_diagnostics_archive"
        if not archive_dir.exists():
            return
        try:
            shutil.rmtree(archive_dir)
            erased.append(str(archive_dir))
        except PermissionError as exc:
            failed[str(archive_dir)] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            failed[str(archive_dir)] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_clear_keychain(app: object, failed: dict) -> None:
        """clear OS keychain entries + in-memory Config attrs.

        Iterates :data:`credential_store.PROVIDER_TO_CONFIG_FIELD` and
        calls ``credential_store.delete_secret(provider, config=...)``
        for each provider (openai / groq / deepgram / cloud / llm) —
        removing the entry from the OS keychain (with plaintext
        fallback for headless Linux), clearing the on-disk reference
        token in config.json, AND zeroing the in-memory ``Config``
        attribute.  Finally calls
        ``credential_store.clear_in_memory_secrets(app.config)`` as a
        belt-and-suspenders pass.

        Failures (e.g. keyring backend broken) are logged inside
        ``delete_secret``; we surface them in ``failed`` so the
        renderer can show the user which providers could not be
        cleared from the keychain.
        """
        from voice_typer.server import credential_store

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

    @staticmethod
    def _gdpr_invalidate_cached_engines(app: object) -> None:
        """Invalidate the cached LLMPolisher / CloudEngine instances.

        After the GDPR delete, the next polish / cloud-engine request
        must rebuild with the (now-empty) API key rather than reusing
        a client bound to the deleted credential.  ``apply_config``
        already does this when an ``llm_*`` field changes, but the
        GDPR delete path bypasses ``apply_config`` (it deletes the
        on-disk file directly), so we invalidate here explicitly.

        ``contextlib.suppress`` because the attribute may not exist
        on fresh installs / test mocks.  Uses ``setattr`` so the
        static type checker doesn't flag the access (``app`` is typed
        as :class:`AppProtocol` which doesn't declare
        ``_llm_polisher`` / ``_cloud_engine`` per ADR-0008-§3.1).
        """
        with contextlib.suppress(Exception):
            app._llm_polisher = None
        with contextlib.suppress(Exception):
            app._cloud_engine = None

    @staticmethod
    def _gdpr_invalidate_managers(app: object) -> None:
        """Re-read the (now-empty) vocabulary / templates files into
        the live in-memory managers.

        The unlink step in :meth:`delete_all_personal_data` removes
        ``voice-typer-vocabulary.json`` and ``voice-typer-templates.json``
        from disk, but the live ``app._vocabulary_manager`` /
        ``app._template_manager`` instances still hold their pre-delete
        in-memory state (``_data`` / ``_templates`` populated with the
        user's now-deleted PII). Without this invalidation step, the
        next dictation would still apply the deleted vocabulary /
        templates — a GDPR Art. 17 right-to-erasure violation (the
        data "appears" deleted on disk but is still actively used by
        the running process).

        We re-read by calling the managers' own ``_load_and_merge`` /
        ``_load`` methods — which now see the missing file and fall
        back to the bundled defaults (vocabulary) / empty list
        (templates). The acquire/release of each manager's ``_lock``
        is required because both methods mutate the manager's
        in-memory state and the docstring contract for
        :meth:`TemplateManager._load` explicitly notes that callers
        outside ``__init__`` must hold the lock (the in-memory list
        is otherwise observable mid-swap by a concurrent ``match`` /
        ``apply_to_text`` call).

        Best-effort: if a manager is ``None`` (cold-start path where
        the lazy property has not yet been triggered), there is
        nothing to invalidate — the next access will construct a
        fresh instance that reads the (now-empty) file.  All
        exceptions are suppressed at WARNING level so a failure here
        does not abort the GDPR delete (the on-disk files are already
        gone — the user's right-to-erasure is satisfied; only the
        in-memory cache invalidation failed, which is a quality-of-
        service issue, not a privacy issue).
        """
        # VocabularyManager
        try:
            vm = getattr(app, "_vocabulary_manager", None)
            if vm is not None and hasattr(vm, "_lock") and hasattr(vm, "_load_and_merge"):
                with vm._lock:
                    vm._load_and_merge()
        except Exception:
            log.warning(
                "[SERVICE] GDPR delete: could not invalidate live "
                "VocabularyManager in-memory state — the on-disk file "
                "is gone but the in-memory cache may still hold deleted "
                "PII until the next process restart",
                exc_info=True,
            )
        # TemplateManager
        try:
            tm = getattr(app, "_template_manager", None)
            if tm is not None and hasattr(tm, "_lock") and hasattr(tm, "_load"):
                with tm._lock:
                    tm._load()
        except Exception:
            log.warning(
                "[SERVICE] GDPR delete: could not invalidate live "
                "TemplateManager in-memory state — the on-disk file "
                "is gone but the in-memory cache may still hold deleted "
                "PII until the next process restart",
                exc_info=True,
            )

    @staticmethod
    def _gdpr_recreate_history_db(app: object) -> None:
        """re-create the live HistoryDB after GDPR delete.

        The writer thread was shut down by the checkpoint+close step
        in :meth:`delete_all_personal_data`; without re-creation, the
        next ``add_transcription`` call would raise (or silently drop
        the write) because the writer queue is closed.  We construct
        a fresh ``HistoryDB`` at the default path
        (``<config_dir>/history.db``) — ``HistoryDB.__init__`` will
        re-create the file with a fresh schema on first write.

        Best-effort: if construction fails (e.g. disk full,
        permissions), log and leave ``app.history_db`` as the closed
        instance — the user will see a "history DB unavailable"
        warning on the next dictation, but the GDPR delete itself
        succeeded.
        """
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

    @staticmethod
    def _gdpr_post_cleanup_sweep(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """post-cleanup sweep for re-created lock files.

        ``credential_store.delete_secret`` re-creates
        ``config.json.lock``; re-unlink it here so it doesn't survive
        GDPR delete.  Also re-sweeps ``.restart_token`` (defensive).
        """
        from pathlib import Path

        for re_cleanup_name in ("config.json.lock", ".restart_token"):
            re_cleanup_path = Path(config_dir) / re_cleanup_name
            if not re_cleanup_path.exists():
                continue
            try:
                re_cleanup_path.unlink()
                erased.append(str(re_cleanup_path))
            except PermissionError as exc:
                failed[str(re_cleanup_path)] = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                failed[str(re_cleanup_path)] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_build_zip(zf: object, config_dir: "os.PathLike[str] | str") -> None:
        """write every personal-data artifact into ``zf``.

        Walks :data:`_GDPR_PERSONAL_FILES` and
        :data:`_GDPR_PERSONAL_GLOBS` against ``config_dir`` and adds
        each existing file to the open ``zipfile.ZipFile`` ``zf``.
        Per-file errors are caught + logged at DEBUG so a single
        unreadable file doesn't abort the whole export (the user gets
        a partial zip rather than nothing).
        """
        from pathlib import Path

        config_dir_path = Path(config_dir)
        # 1. Hardcoded personal-data files.
        for name in PrivacyMixin._GDPR_PERSONAL_FILES:
            path = config_dir_path / name
            if path.exists() and path.is_file():
                try:
                    zf.write(path, arcname=name)  # type: ignore[attr-defined]
                except Exception as exc:
                    log.debug(
                        "[SERVICE] GDPR export: could not add %s to zip: %s",
                        path,
                        exc,
                    )
        # 2. Glob-pattern personal-data files (mic-test recordings,
        # rotated log backups ``voice-typer.log.*`` + ``prewarm.log.*``,
        # crash diagnostic files ``crash_diagnostics.*.txt`` +
        # ``python_crash.*.txt``).
        for pattern in PrivacyMixin._GDPR_PERSONAL_GLOBS:
            for path in config_dir_path.glob(pattern):
                if not path.is_file():
                    continue
                try:
                    zf.write(path, arcname=path.name)  # type: ignore[attr-defined]
                except Exception as exc:
                    log.debug(
                        "[SERVICE] GDPR export: could not add %s to zip: %s",
                        path,
                        exc,
                    )

    @staticmethod
    def _gdpr_rotate_exports(config_dir: "os.PathLike[str] | str") -> None:
        """rotate ``gdpr-export-*.zip`` files.

        Keeps the most recent 5 (by mtime), unlinks older ones.
        Without rotation, repeated GDPR exports accumulate unboundedly
        (each is 1-50 MB depending on history size).  Best-effort: a
        ``PermissionError`` on unlink is logged but does not fail the
        export (the new zip was already written successfully).
        """
        from pathlib import Path

        try:
            exports = sorted(
                Path(config_dir).glob("gdpr-export-*.zip"),
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

    def delete_all_personal_data(self) -> dict:
        """GDPR Art. 17 — right to erasure.

                Delete every personal-data artifact the app owns (history DB,
                crash-recovery buffer, config + secrets, corrections /
                vocabulary / templates, runtime log + rotated backups, prewarm
                log, mic-test recordings, crash diagnostic files, archived
                crash diagnostics, and the Rust host's ``logs/`` subdirectory).
                Model weights are explicitly preserved — they are not personal
        data ( spec).

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

                The per-step work is delegated to private ``@staticmethod``
                helpers (``_gdpr_checkpoint_history_db`` /
                ``_gdpr_unlink_personal_files`` / ``_gdpr_unlink_personal_globs``
                / ``_gdpr_rmtree_rust_logs`` / ``_gdpr_rmtree_crash_archive``
                / ``_gdpr_clear_keychain`` / ``_gdpr_invalidate_cached_engines``
                / ``_gdpr_recreate_history_db`` / ``_gdpr_post_cleanup_sweep``).
                This method is now a thin orchestrator that calls them in
                order and assembles the result dict.  See the helper docstrings
                for the per-step rationale (WAL checkpoint, keychain clear,
                engine invalidation, etc.).
        """
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        erased: list = []
        failed: dict = {}

        # checkpoint + close the live HistoryDB writer
        # BEFORE unlinking so the WAL is empty when removed and the
        # writer thread releases its file descriptor (Windows refuses
        # to unlink an open file).
        hdb = getattr(self._app, "history_db", None)
        self._gdpr_checkpoint_history_db(hdb, close=True)

        # Rust logs/ subdirectory (recursively removed).
        self._gdpr_rmtree_rust_logs(config_dir, erased, failed)

        # Hardcoded personal-data files + glob-pattern personal-data files.
        self._gdpr_unlink_personal_files(config_dir, erased, failed)
        self._gdpr_unlink_personal_globs(config_dir, erased, failed)

        # Archived crash diagnostics directory.
        self._gdpr_rmtree_crash_archive(config_dir, erased, failed)

        # OS keychain entries + in-memory Config attrs + cached engines.
        app = self._app
        self._gdpr_clear_keychain(app, failed)
        self._gdpr_invalidate_cached_engines(app)

        # Re-read the (now-empty) vocabulary / templates files into the
        # live in-memory managers so the next dictation doesn't apply
        # the just-deleted PII (Art. 17 right-to-erasure: the data
        # must not remain usable in any form).
        self._gdpr_invalidate_managers(app)

        # Re-create the live HistoryDB instance so the app can keep
        # accepting dictations after the GDPR delete.
        if hdb is not None:
            self._gdpr_recreate_history_db(app)

        # Post-cleanup sweep for re-created lock files.
        self._gdpr_post_cleanup_sweep(config_dir, erased, failed)

        log.info(
            "[SERVICE] GDPR Art. 17 delete: erased %d file(s)/dir(s), %d failure(s)",
            len(erased),
            len(failed),
        )
        result: dict = {"success": not failed, "erased": erased}
        if failed:
            result["failed"] = failed
        return result

    def export_gdpr_bundle(self) -> dict:
        """GDPR Art. 20 — right to data portability.

        Produce a single timestamped ``.zip`` at
        ``<config_dir>/gdpr-export-YYYYMMDD-HHMMSS.zip`` containing
        every personal-data artifact the app owns (the same set as
        :meth:`delete_all_personal_data`).  Unlike
        :meth:`export_diagnostics` (which redacts PII for a support
        ticket bundle), this export is the user's OWN data verbatim —
        no redaction.  Model weights are excluded (not personal data).

        Returns::

            {"success": bool,
             "path": "/tmp/.../gdpr-export-20240101-120000.zip"}

        On failure (e.g. the config dir is not writable), returns::

            {"success": False, "message": "..."}.

        A fresh-install config dir (no artifacts) still produces a
        (mostly empty) zip rather than raising — the user's right to
        portability is satisfied even if there's nothing to export.

        The per-step work is delegated to private ``@staticmethod``
        helpers (``_gdpr_checkpoint_history_db`` / ``_gdpr_build_zip``
        / ``_gdpr_rotate_exports``).  This method is now a thin
        orchestrator that handles the atomic temp-file + rename dance
        and assembles the result dict.  See the helper docstrings for
        the per-step rationale (WAL checkpoint for parseable SQLite
        export, per-file best-effort zip add, export rotation to bound
        on-disk usage).
        """
        import time as _time
        import zipfile as _zipfile

        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        timestamp = _time.strftime("%Y%m%d-%H%M%S")
        zip_path = config_dir / f"gdpr-export-{timestamp}.zip"

        # checkpoint the live HistoryDB writer BEFORE
        # zipping ``history.db`` so the WAL is merged into the main DB.
        # Without this, the exported ``history.db`` is unparseable:
        # SQLite opens WAL-mode DBs by first reading ``history.db-wal``
        # to apply pending transactions, and the WAL sidecar is not
        # included in the export (it's a transient file).
        hdb = getattr(self._app, "history_db", None)
        self._gdpr_checkpoint_history_db(hdb, close=False)

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
                self._gdpr_build_zip(zf, config_dir)
            # ZipFile block completed successfully — atomic
            # rename of the completed temp zip into the final path.
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

        # rotate ``gdpr-export-*.zip`` — keep most recent 5.
        self._gdpr_rotate_exports(config_dir)

        log.info(
            "[SERVICE] GDPR Art. 20 export: wrote %s (%d bytes)",
            zip_path,
            zip_path.stat().st_size if zip_path.exists() else 0,
        )
        return {"success": True, "path": str(zip_path)}


__all__ = ["PrivacyMixin"]
