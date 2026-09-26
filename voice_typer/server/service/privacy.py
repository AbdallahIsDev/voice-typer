"""Privacy / GDPR domain mixin (Art. 17/20). Inventory: _user_data_files."""

import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Protocol

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server._user_data_files import (
    _GDPR_PERSONAL_FILES,
    _GDPR_PERSONAL_GLOBS as _GDPR_PERSONAL_GLOBS_INVENTORY,
)
from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


# Structural Protocols: helpers accept live objects and test doubles.
class _ClosableHistoryDB(Protocol):
    """Duck-typed HistoryDB surface used by the checkpoint helper."""

    def close(self) -> None: ...


class _ZipWriter(Protocol):
    """Duck-typed zipfile.ZipFile surface used by the export path."""

    def write(self, filename: Any, arcname: Any = ...) -> None: ...


# Once-per-process warn when app lacks _config_mutation_lock (test fakes).
_GDPR_CONFIG_LOCK_MISSING_WARNED: bool = False


class PrivacyMixin(ServiceMixinBase):
    """GDPR right-to-erasure (Art. 17) + data portability (Art. 20)."""

    # Inventory imported from _user_data_files (shared with uninstall
    _GDPR_PERSONAL_FILES: tuple = _GDPR_PERSONAL_FILES
    # Glob patterns (rotated logs, crash dumps, mic tests) walked against
    _GDPR_PERSONAL_GLOBS: tuple = (
        "mic-test-*.wav",
        "lausu.log.*",
        "prewarm.log.*",
        "crash_diagnostics.*.txt",
        "python_crash.*.txt",
        # Rotated backups of the renderer-error log. Nothing rotates
        "legacy-renderer-errors.log.*",
        # lausu-diagnostics-*.zip contains PII
        "lausu-diagnostics-*.zip",
        # gdpr-export-*.zip contains user full personal data
        "gdpr-export-*.zip",
        # (High): four config-backup file classes ALL contain
        "config.json.v*.bak",
        "config.json.pre-migration-v*.bak",
        "config.json.bak.failed-migration-*",
        "config.json.corrupt-*",
        # Full plaintext history copies from schema migration backups.
        "history.db.pre-migration-v*",
        # Shared inventory (uninstall-purge parity): corrupt quarantine +
        *_GDPR_PERSONAL_GLOBS_INVENTORY,
    )

    # GDPR Art. 17/20 methods; inventory above is the delete/export set.

    # * ``lausu.log`` : runtime log (Python side)

    # Private @staticmethod helpers: one GDPR pipeline slice each.

    @staticmethod
    def _gdpr_checkpoint_history_db(hdb: _ClosableHistoryDB | None, *, close: bool = False) -> None:
        """Best-effort: missing/failed checkpoint is DEBUG-logged; caller
        still attempts sidecar unlink (stale plaintext may remain).
        """
        if hdb is None:
            return
        try:
            checkpoint_fn = getattr(hdb, "checkpoint", None)
            if callable(checkpoint_fn):
                checkpoint_fn(truncate=True)
        except Exception:
            log.debug(
                "[SERVICE] GDPR: hdb.checkpoint(truncate=True) failed",
                exc_info=True,
            )
        if close:
            try:
                hdb.close()
            except Exception:
                log.debug(
                    "[SERVICE] GDPR: hdb.close() before unlink failed",
                    exc_info=True,
                )

    @staticmethod
    def _gdpr_rmtree_dir(
        target: "os.PathLike[str] | str",
        erased: list,
        failed: dict,
        *,
        label: str,
    ) -> None:
        """copies of the same shape). Best-effort: a missing directory is a"""

        target_path = Path(target)
        if not target_path.exists():
            return
        try:
            shutil.rmtree(target_path, ignore_errors=False)
            erased.append(str(target_path))
            log.debug(
                "[SERVICE] GDPR delete: removed %s dir at %s",
                label,
                target_path,
            )
        except Exception as exc:
            log.warning(
                "[SERVICE] GDPR delete: could not rmtree %s dir at %s: %s, user may need to delete it manually",
                label,
                target_path,
                exc,
            )
            failed[str(target_path)] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_safe_unlink(path: "os.PathLike[str] | str", erased: list, failed: dict) -> bool:
        """``False`` without recording anything; a successful unlink"""

        target_path = Path(path)
        if not target_path.exists():
            return False
        try:
            target_path.unlink()
        except Exception as exc:
            failed[str(target_path)] = f"{type(exc).__name__}: {exc}"
            return False
        erased.append(str(target_path))
        return True

    @staticmethod
    def _gdpr_unlink_personal_files(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """Walks :data:`_GDPR_PERSONAL_FILES` and unlinks each existing"""

        for name in PrivacyMixin._GDPR_PERSONAL_FILES:
            PrivacyMixin._gdpr_safe_unlink(Path(config_dir) / name, erased, failed)

    @staticmethod
    def _gdpr_unlink_personal_globs(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """Walks :data:`_GDPR_PERSONAL_GLOBS` (mic-test recordings,"""

        seen: set[str] = set()
        for pattern in PrivacyMixin._GDPR_PERSONAL_GLOBS:
            for path in Path(config_dir).glob(pattern):
                key = str(path)
                if key in seen:
                    # Already unlinked by an earlier overlapping
                    continue
                seen.add(key)
                PrivacyMixin._gdpr_safe_unlink(path, erased, failed)

    @staticmethod
    def _gdpr_rmtree_rust_logs(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """``<config_dir>/logs/lausu.log`` + rotated backups"""

        from voice_typer.server.log import get_logs_dir

        PrivacyMixin._gdpr_rmtree_dir(get_logs_dir(Path(config_dir)), erased, failed, label="Rust logs/")

    @staticmethod
    def _gdpr_rmtree_db_dir(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """``<config_dir>/db/history.db`` (+ ``-wal``/``-shm`` sidecars,"""

        PrivacyMixin._gdpr_rmtree_dir(Path(config_dir) / "db", erased, failed, label="db")

    @staticmethod
    def _gdpr_rmtree_predecessor_profile(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """That subdirectory is the legacy Chromium profile dir (caches,"""

        PrivacyMixin._gdpr_rmtree_dir(Path(config_dir) / "legacy-profile", erased, failed, label="legacy profile/")

    @staticmethod
    def _gdpr_rmtree_crash_archive(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """``crash_diagnostics/`` is where the crash handler moves"""

        PrivacyMixin._gdpr_rmtree_dir(Path(config_dir) / "crash_diagnostics", erased, failed, label="crash_diagnostics")

    @staticmethod
    def _gdpr_clear_keychain(app: object, failed: dict) -> None:
        """Iterates :data:`credential_store.PROVIDER_TO_CONFIG_FIELD` and
        calls ``credential_store.delete_secret(provider, config=...)``
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
        if app_config is not None:
            try:
                credential_store.clear_in_memory_secrets(app_config)
            except Exception as exc:
                failed["in_memory_config"] = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _gdpr_invalidate_cached_engines(app: object) -> None:
        """After the GDPR delete, the next polish / cloud-engine request
        must rebuild with the (now-empty) API key rather than reusing
        """
        from voice_typer.server.service import _app_internals

        with contextlib.suppress(Exception):
            _app_internals.invalidate_llm_polisher(app)
        with contextlib.suppress(Exception):
            _app_internals.invalidate_cloud_engine(app)

    @staticmethod
    def _gdpr_invalidate_managers(app: object) -> None:
        """the live in-memory managers.
        The unlink step in :meth:`delete_all_personal_data` removes
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
                "VocabularyManager in-memory state, the on-disk file "
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
                "TemplateManager in-memory state, the on-disk file "
                "is gone but the in-memory cache may still hold deleted "
                "PII until the next process restart",
                exc_info=True,
            )

    @staticmethod
    def _gdpr_recreate_history_db(app: object) -> None:
        """The writer thread was shut down by the checkpoint+close step
        in :meth:`delete_all_personal_data`; without re-creation, the
        """
        try:
            from voice_typer.server.history_db import HistoryDB

            new_hdb = HistoryDB()
            # ``app`` is typed as :class:`AppProtocol` (which
            app.history_db = new_hdb
        except Exception:
            log.debug(
                "[SERVICE] GDPR delete: could not re-create HistoryDB after erase",
                exc_info=True,
            )

    @staticmethod
    def _gdpr_post_cleanup_sweep(config_dir: "os.PathLike[str] | str", erased: list, failed: dict) -> None:
        """``credential_store.delete_secret`` re-creates
        ``config.json.lock``; re-unlink it here so it doesn't survive
        """

        for re_cleanup_name in ("config.json.lock", ".restart_token"):
            PrivacyMixin._gdpr_safe_unlink(Path(config_dir) / re_cleanup_name, erased, failed)

    @staticmethod
    def _gdpr_zip_directory(
        zf: _ZipWriter,
        config_dir: "os.PathLike[str] | str",
        subdir: str,
        prefix: str,
    ) -> None:
        """The GDPR delete path removes the ``logs/`` (Rust host rotating"""

        root = Path(config_dir) / subdir
        if not root.is_dir():
            return
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            arcname = f"{prefix}/{rel}"
            try:
                zf.write(path, arcname=arcname)
            except Exception as exc:
                log.debug(
                    "[SERVICE] GDPR export: could not add %s to zip: %s",
                    path,
                    exc,
                )

    @staticmethod
    def _gdpr_build_zip(zf: _ZipWriter, config_dir: "os.PathLike[str] | str") -> None:
        """Walks :data:`_GDPR_PERSONAL_FILES` and"""

        config_dir_path = Path(config_dir)
        # 1. Hardcoded personal-data files.
        for name in PrivacyMixin._GDPR_PERSONAL_FILES:
            path = config_dir_path / name
            if path.exists() and path.is_file():
                try:
                    zf.write(path, arcname=name)
                except Exception as exc:
                    log.debug(
                        "[SERVICE] GDPR export: could not add %s to zip: %s",
                        path,
                        exc,
                    )
        # 2. Glob-pattern personal-data files (mic-test recordings,
        seen: set[str] = set()
        for pattern in PrivacyMixin._GDPR_PERSONAL_GLOBS:
            for path in config_dir_path.glob(pattern):
                if not path.is_file():
                    continue
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    zf.write(path, arcname=path.name)
                except Exception as exc:
                    log.debug(
                        "[SERVICE] GDPR export: could not add %s to zip: %s",
                        path,
                        exc,
                    )
        # 3. Recursive subdirectory walks. ``logs/`` holds the Rust
        PrivacyMixin._gdpr_zip_directory(zf, config_dir, "logs", "logs")
        PrivacyMixin._gdpr_zip_directory(zf, config_dir, "db", "db")
        PrivacyMixin._gdpr_zip_directory(zf, config_dir, "crash_diagnostics", "crash_diagnostics")

    @staticmethod
    def _gdpr_rotate_exports(config_dir: "os.PathLike[str] | str") -> None:
        """Keeps the most recent 5 (by mtime), unlinks older ones.
        Without rotation, repeated GDPR exports accumulate unboundedly
        """

        try:
            exports = sorted(
                Path(config_dir).glob("gdpr-export-*.zip"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in exports[5:]:
                try:
                    stale.unlink()
                except OSError as exc:
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
        """Delete every personal-data artifact the app owns (history DB,"""
        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        erased: list = []
        failed: dict = {}

        # checkpoint + close the live HistoryDB writer
        hdb = getattr(self._app, "history_db", None)
        self._gdpr_checkpoint_history_db(hdb, close=True)

        # Rust logs/ subdirectory (recursively removed). Outside the
        self._gdpr_rmtree_rust_logs(config_dir, erased, failed)
        self._gdpr_rmtree_db_dir(config_dir, erased, failed)
        self._gdpr_rmtree_predecessor_profile(config_dir, erased, failed)

        # Acquire config_mutation_lock when present; warn once if missing.
        app = self._app
        config_lock = getattr(app, "_config_mutation_lock", None)
        if config_lock is None:
            global _GDPR_CONFIG_LOCK_MISSING_WARNED
            if not _GDPR_CONFIG_LOCK_MISSING_WARNED:
                _GDPR_CONFIG_LOCK_MISSING_WARNED = True
                log.warning(
                    "[SERVICE] GDPR delete_all_personal_data: app has no "
                    "_config_mutation_lock, running lock-free; concurrent "
                    "set_config / reset_config_to_defaults / onboarding_apply "
                    "may interleave with the delete (this warning fires once "
                    "per process)"
                )

        def _gdpr_critical_section() -> None:
            # Hardcoded personal-data files + glob-pattern personal-data files.
            self._gdpr_unlink_personal_files(config_dir, erased, failed)
            self._gdpr_unlink_personal_globs(config_dir, erased, failed)

            # Archived crash diagnostics directory.
            self._gdpr_rmtree_crash_archive(config_dir, erased, failed)

            # OS keychain entries + in-memory Config attrs + cached engines.
            self._gdpr_clear_keychain(app, failed)
            self._gdpr_invalidate_cached_engines(app)

            # Re-read the (now-empty) vocabulary / templates files into the
            self._gdpr_invalidate_managers(app)

            # Post-cleanup sweep for re-created lock files.
            self._gdpr_post_cleanup_sweep(config_dir, erased, failed)

        if config_lock is not None:
            with config_lock:
                _gdpr_critical_section()
        else:
            _gdpr_critical_section()

        # Re-create the live HistoryDB instance so the app can keep
        if hdb is not None:
            self._gdpr_recreate_history_db(app)

        log.info(
            "[SERVICE] GDPR Art. 17 delete: erased %d files/dirs, %d failures",
            len(erased),
            len(failed),
        )
        result: dict = {"success": not failed, "erased": erased}
        if failed:
            result["failed"] = failed
        return result

    def export_gdpr_bundle(self) -> dict:
        """Produce a single timestamped ``.zip`` at"""
        import time as _time
        import zipfile as _zipfile

        from voice_typer.server.config import _config_dir

        config_dir = _config_dir()
        timestamp = _time.strftime("%Y%m%d-%H%M%S")
        zip_path = config_dir / f"gdpr-export-{timestamp}.zip"

        # checkpoint the live HistoryDB writer BEFORE
        hdb = getattr(self._app, "history_db", None)
        self._gdpr_checkpoint_history_db(hdb, close=False)

        # Build the zip to a temp path (``.zip.tmp``) in the
        tmp_path = zip_path.with_suffix(".zip.tmp")
        try:
            with _zipfile.ZipFile(tmp_path, "w", _zipfile.ZIP_DEFLATED) as zf:
                self._gdpr_build_zip(zf, config_dir)
            # ZipFile block completed successfully, atomic
            try:
                os.replace(tmp_path, zip_path)
            except OSError:
                with contextlib.suppress(FileNotFoundError):
                    tmp_path.unlink()
                raise
        except Exception as exc:
            # Unlink any partial temp file so no corrupt
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
            log.exception("export_gdpr_bundle failed: %s", exc)
            return {
                "success": False,
                "message": redact_secret(redact_url(str(exc))),
            }

        # rotate ``gdpr-export-*.zip``: keep most recent 5.
        self._gdpr_rotate_exports(config_dir)

        log.info(
            "[SERVICE] GDPR Art. 20 export: wrote %s (%d bytes)",
            zip_path,
            zip_path.stat().st_size if zip_path.exists() else 0,
        )
        return {"success": True, "path": str(zip_path)}


__all__ = ["PrivacyMixin"]
