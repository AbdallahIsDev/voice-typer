"""Model deletion, stale-selection recovery, and local archive import."""

from __future__ import annotations

import logging

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.i18n import t as _t
from voice_typer.server.model_registry import NO_MODEL_SIZE, ModelMetadata
from voice_typer.server.service._helpers import _find_symlink_in_tree

log = logging.getLogger(__name__)


class DeleteImportMixin:
    def _invalidate_tray_model_cache(self, context: str) -> None:
        """Best-effort tray-submenu cache invalidation shared by delete/import."""
        try:
            from voice_typer.server.tray_models import (
                invalidate_model_availability_cache,
            )

            invalidate_model_availability_cache()
        except Exception:
            log.debug(
                "[SERVICE] %s: invalidate_model_availability_cache failed",
                context,
                exc_info=True,
            )

    def delete_model(self, model_name: str) -> dict[str, object]:
        """Delete a downloaded model from the HuggingFace cache.

        Returns ``{"success": bool, "message": str}``.
        """
        import shutil

        from voice_typer.server.config import _config_dir
        from voice_typer.server.model_registry import get_model_metadata

        cache_dir = _config_dir() / "huggingface" / "hub"

        # Resolve repo_id from MODEL_REGISTRY so all registered
        meta = get_model_metadata(model_name)
        if meta is not None and meta.backend in ("whisper", "distil-whisper", "parakeet", "qwen"):
            repo_id = meta.repo_id
        elif model_name == "parakeet":
            repo_id = "nvidia/parakeet-tdt-0.6b-v3"
        elif model_name == "qwen":
            # Qwen is registered in MODEL_REGISTRY
            repo_id = "andrewleech/qwen3-asr-1.7b-onnx"
        else:
            repo_id = None

        if not repo_id:
            log.warning("[SERVICE] delete_model: unknown model '%s'", model_name)
            return {
                "success": False,
                "reason": "unknown_model",
                "message": _t("notify.model.delete.unknown_model", model=model_name),
            }

        # Compute whether ``model_name`` is the configured active model.
        current_backend = getattr(self._app.config, "asr_backend", "whisper")
        current_model = getattr(self._app.config, "model_size", "tiny")
        is_active = (
            (model_name == current_model and current_backend in ("whisper", "distil-whisper"))
            or (model_name == "parakeet" and current_backend == "parakeet")
            or (model_name == "qwen" and current_backend == "qwen")
        )

        model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
        if not model_dir.exists():
            # The model is NOT on disk. If it's ALSO the configured active
            if is_active:
                return self._clear_stale_active_model(model_name)
            log.info(
                "[SERVICE] delete_model: '%s' is not downloaded, nothing to delete",
                model_name,
            )
            return {
                "success": False,
                "reason": "not_downloaded",
                "message": _t("notify.model.delete.not_downloaded", model=model_name),
            }

        # ACTIVE-DELETE: the configured model CAN be deleted. Unload the
        if is_active:
            return self._delete_active_model(model_name, repo_id, model_dir, current_backend)

        try:
            shutil.rmtree(model_dir)
            log.info(
                "[SERVICE] Model '%s' deleted (repo=%s)",
                model_name,
                repo_id,
            )
            # Invalidate the tray models submenu cache so the next
            self._invalidate_tray_model_cache("delete_model")
            # On-disk model state changed, force the next status recompute.
            self._invalidate_model_status_cache()
            # Success: omit ``message`` so the renderer falls back to
            return {
                "success": True,
                "reason": "deleted",
            }
        except Exception as exc:
            log.warning("[SERVICE] delete_model failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    def _clear_stale_active_model(self, model_name: str) -> dict[str, object]:
        """Clear a stale active-model selection whose files are missing from disk."""
        updates, replacement = self._reassign_selection_away_from(model_name, context="stale")
        if updates:
            if updates.get("model_size") == NO_MODEL_SIZE:
                message = _t(
                    "notify.model.delete.stale_cleared_no_model",
                    model=model_name,
                )
                reason = "stale_cleared_no_model"
            else:
                message = _t(
                    "notify.model.delete.stale_cleared_switched",
                    model=model_name,
                    replacement=updates["model_size"],
                )
                reason = "stale_cleared_switched"
        else:
            message = _t(
                "notify.model.delete.stale_nothing_to_delete",
                model=model_name,
            )
            reason = "stale_nothing_to_delete"
        log.info("[SERVICE] delete_model: %s", message)
        # Stale-clear is a SUCCESS recovery path: omit ``message`` so
        return {"success": True, "reason": reason}

    def _reassign_selection_away_from(
        self, exclude_name: str, context: str
    ) -> tuple[dict[str, str], ModelMetadata | None]:
        """Move the active selection off ``exclude_name`` (shared core)."""
        updates: dict[str, str] = {}
        replacement = self._pick_downloaded_fallback_model(exclude_name)
        if replacement is not None:
            # ``_pick_downloaded_fallback_model`` never returns a distil
            if replacement.backend == "whisper":
                updates = {"asr_backend": "whisper", "model_size": replacement.name}
            else:
                updates = {"asr_backend": replacement.backend, "model_size": replacement.name}
        else:
            # NO downloaded model exists to fall back to, enter the
            updates = {"model_size": NO_MODEL_SIZE}
        try:
            # canonical config-mutation path (SEC-002 allowlisted keys,
            self._config_applier.apply_config(updates)
            if replacement is not None:
                log.info(
                    "[SERVICE] delete_model: %s model '%s' cleared, switched to '%s'",
                    context,
                    exclude_name,
                    replacement.name,
                )
            else:
                log.info(
                    "[SERVICE] delete_model: %s model '%s' cleared, "
                    "no other model is downloaded, entering 'no model selected' state",
                    context,
                    exclude_name,
                )
        except Exception as exc:
            # Best-effort: ``apply_config`` rolls the in-memory config
            log.warning(
                "[SERVICE] delete_model: cleared %s selection for '%s' but failed to persist config %s: %s",
                context,
                exclude_name,
                updates,
                exc,
            )
            updates = {}
        if updates:
            try:
                from voice_typer.server import event_bus

                event_bus.publish({"type": "config_changed", "data": updates})
            except Exception:
                log.debug("[SERVICE] delete_model: config_changed push failed", exc_info=True)
        self._invalidate_model_status_cache()
        if updates.get("model_size") == NO_MODEL_SIZE:
            # Landing in the genuine no-model state with no engine
            try:
                from voice_typer.server.i18n import t as _t
                from voice_typer.server.tray_types import AppState as _AppState

                self._app.tray.set_state(
                    _AppState.ERROR,
                    _t("state.model_manager.no_model_selected"),
                )
            except Exception:
                log.debug(
                    "[SERVICE] delete_model: no-model tray update failed (non-fatal)",
                    exc_info=True,
                )
        return updates, replacement

    def _delete_active_model(self, model_name: str, repo_id: str, model_dir, current_backend: str) -> dict[str, object]:
        """Delete the CONFIGURED model (ACTIVE-DELETE)."""
        import shutil

        recorder = getattr(self._app, "recorder", None)
        busy_event = getattr(self._app, "_busy_event", None)
        in_flight = getattr(recorder, "recording", False) is True
        if not in_flight and busy_event is not None:
            try:
                in_flight = not bool(busy_event.is_set())
            except Exception:
                in_flight = False
        if in_flight:
            log.info(
                "[SERVICE] delete_model: refusing active delete of '%s' while dictation is in flight",
                model_name,
            )
            return {
                "success": False,
                "reason": "dictation_in_flight",
                "message": _t("notify.model.delete.active_refused_recording"),
            }
        try:
            self._app.models.unload_backend_for_delete(current_backend)
        except Exception as exc:
            log.warning(
                "[SERVICE] delete_model: unload of active '%s' failed: %s",
                model_name,
                exc,
            )
            return {
                "success": False,
                "reason": "unload_failed",
                "message": _t("notify.model.delete.unload_failed", model=model_name),
            }
        updates, replacement = self._reassign_selection_away_from(model_name, context="active-delete")
        try:
            shutil.rmtree(model_dir)
            log.info(
                "[SERVICE] Model '%s' deleted (repo=%s)",
                model_name,
                repo_id,
            )
            self._invalidate_tray_model_cache("delete_model")
            self._invalidate_model_status_cache()
            if updates:
                if updates.get("model_size") == NO_MODEL_SIZE:
                    message = f"Deleted model '{model_name}', no model selected. Pick a model on the Models page."
                    reason = "deleted_no_model"
                else:
                    message = f"Deleted model '{model_name}', switched to '{updates['model_size']}'."
                    reason = "deleted_switched"
            else:
                # Selection switch did not persist (logged above), the
                message = f"Deleted model '{model_name}'."
                reason = "deleted"
            log.info("[SERVICE] delete_model: %s", message)
            # Success: omit ``message`` so the renderer falls back to
            return {"success": True, "reason": reason}
        except Exception as exc:
            log.warning("[SERVICE] delete_model failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    def _pick_downloaded_fallback_model(self, exclude_name: str) -> ModelMetadata | None:
        """Return metadata for the first downloaded model other than ``exclude_name``."""
        from voice_typer.server.model_registry import MODEL_REGISTRY

        status = self._compute_model_status()
        for name, meta in MODEL_REGISTRY.items():
            if name == exclude_name or meta.backend == "distil-whisper":
                continue
            entry = status.get(name)
            if entry is not None and bool(entry.get("downloaded")):
                return meta
        return None

    def import_model(self, dir_path: str) -> dict:
        """Scan a directory for HuggingFace model cache folders and import

        Returns:
        """
        import os
        import shutil

        from voice_typer.server.config import _config_dir
        from voice_typer.server.model_registry import MODEL_REGISTRY

        cache_dir = _config_dir() / "huggingface" / "hub"
        cache_dir.mkdir(parents=True, exist_ok=True)

        found_models: list[str] = []
        imported_models: list[str] = []
        errors: list[dict] = []

        # Build a reverse mapping: ``models--Org--RepoName`` → model name
        dir_to_model: dict[str, str] = {}
        for model_name, meta in MODEL_REGISTRY.items():
            expected = f"models--{meta.repo_id.replace('/', '--')}"
            dir_to_model[expected] = model_name

        # Collect candidate model-cache dirs (selected dir + one level deep).
        candidates: list[tuple[str, str]] = []  # (full_path, dir_name)

        # Check if the selected directory itself is a model cache dir
        base_name = os.path.basename(dir_path)
        if base_name in dir_to_model:
            candidates.append((dir_path, base_name))

        # Scan one level deep for model cache subdirectories
        try:
            for entry in os.listdir(dir_path):
                entry_path = os.path.join(dir_path, entry)
                if os.path.isdir(entry_path) and entry in dir_to_model:
                    candidates.append((entry_path, entry))
        except PermissionError:
            return {
                "success": False,
                "imported": [],
                "found": [],
                "errors": [{"model": "", "error": f"Permission denied reading {dir_path}"}],
            }

        # Import each candidate
        for src_path, dir_name in candidates:
            model_name = dir_to_model[dir_name]
            found_models.append(model_name)
            dest = cache_dir / dir_name
            try:
                # refuse to import a model cache that contains
                symlink = _find_symlink_in_tree(src_path)
                if symlink is not None:
                    log.warning(
                        "[SERVICE] import_model: refusing to import %s, "
                        "symlink detected at %s (symlinks are not allowed "
                        "in imported model cache dirs)",
                        model_name,
                        symlink,
                    )
                    errors.append(
                        {
                            "model": model_name,
                            "error": (
                                f"Refusing to import model containing a symlink "
                                f"({symlink}). Symlinks are not permitted in "
                                f"imported model cache directories."
                            ),
                        }
                    )
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                # symlinks=False as defense-in-depth.  The explicit
                shutil.copytree(src_path, dest, symlinks=False)
                imported_models.append(model_name)
            except Exception as exc:
                # redact str(exc) before appending to per-model
                redacted = redact_secret(redact_url(str(exc)))
                log.warning(
                    "[SERVICE] import_model: per-model import failed for '%s': %s",
                    model_name,
                    redacted,
                )
                errors.append({"model": model_name, "error": redacted})

        # Invalidate the tray models cache so the next right-click
        if imported_models:
            self._invalidate_tray_model_cache("import_model")

        if imported_models:
            log.info(
                "[SERVICE] Model import: %d found, %d imported, %d errors",
                len(found_models),
                len(imported_models),
                len(errors),
            )
        elif found_models:
            log.warning(
                "[SERVICE] Model import: %d found, 0 imported, %d errors, all imports failed",
                len(found_models),
                len(errors),
            )

        return {
            "success": True,
            "imported": imported_models,
            "found": found_models,
            "errors": errors,
        }
