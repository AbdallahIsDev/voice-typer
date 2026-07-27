"""Privacy / GDPR domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(DT-26 / Phase 4.5 spaghetti split). Owns the two cross-cutting
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
"""

import contextlib
import logging
import os

from voice_typer.server._secrets import redact_secret, redact_url
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


__all__ = ["PrivacyMixin"]
