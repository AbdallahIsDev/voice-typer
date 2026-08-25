"""Model deletion, stale-selection recovery, and local archive import."""

from __future__ import annotations

import logging

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.model_registry import NO_MODEL_SIZE, ModelMetadata
from voice_typer.server.service._helpers import _find_symlink_in_tree

log = logging.getLogger(__name__)


class DeleteImportMixin:
    def delete_model(self, model_name: str) -> dict[str, object]:
        """Delete a downloaded model from the HuggingFace cache.

        logs success/failure with model name and repo ID.

        previously the Models page only removed the model
        from the UI list without actually deleting the files.  A 1.5 GB
        model left on disk is a waste of space and confuses users who
        think they deleted it.  We now actually delete the cached files.

        REGISTRY-FIX: uses ``MODEL_REGISTRY`` (via ``get_model_metadata``)
        to resolve the HuggingFace repo ID instead of an incomplete
        hardcoded ``repo_map`` that was missing large-v3-turbo,
        distil-large-v3, distil-medium.en, and other variants.

        Returns ``{"success": bool, "message": str}``.
        """
        import shutil

        from voice_typer.server.config import _config_dir
        from voice_typer.server.model_registry import get_model_metadata

        cache_dir = _config_dir() / "huggingface" / "hub"

        # Resolve repo_id from MODEL_REGISTRY so all registered
        # whisper/distil-whisper variants (large-v3-turbo, distil-*,
        # base.*, large-*, etc.) are supported without hardcoding.
        # SVC-7: also resolve parakeet + qwen via the registry so
        # ``delete_model("qwen")`` returns the "not downloaded" message
        # (instead of the legacy "Unknown model" error) when the cache
        # dir is absent — the registry now carries the repo_id for both.
        meta = get_model_metadata(model_name)
        if meta is not None and meta.backend in ("whisper", "distil-whisper", "parakeet", "qwen"):
            repo_id = meta.repo_id
        elif model_name == "parakeet":
            repo_id = "nvidia/parakeet-tdt-0.6b-v3"
        elif model_name == "qwen":
            # Qwen is registered in MODEL_REGISTRY
            # (repo_id="andrewleech/qwen3-asr-1.7b-onnx" — the ONNX export,
            # torch-era "Qwen/Qwen-Audio" removed 2026-08-15); fall
            # through to the same cache-dir check so absent models
            # report "not downloaded" rather than "Unknown model".
            repo_id = "andrewleech/qwen3-asr-1.7b-onnx"
        else:
            repo_id = None

        if not repo_id:
            return {"success": False, "message": f"Unknown model: {model_name}"}

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
            # model, the config points at a model that was removed
            # out-of-band (deleted folder, moved cache) — a stale
            # selection. Clearing it removes the phantom "Active" state
            # from the Models page: the user can't delete a model that
            # isn't there, and must not be stuck with a dead disabled
            # button. The files are already gone, so this is a success
            # with a clear message ( STALE-ACTIVE).
            if is_active:
                return self._clear_stale_active_model(model_name)
            return {"success": False, "message": f"Model '{model_name}' is not downloaded."}

        # Don't allow deleting the active model WHILE it's on disk —
        # deleting an in-use model would break the running ASR backend.
        if is_active:
            return {
                "success": False,
                "message": "Cannot delete the active model. Switch to another model first.",
            }

        try:
            shutil.rmtree(model_dir)
            log.info(
                "[SERVICE] Model '%s' deleted (repo=%s)",
                model_name,
                repo_id,
            )
            # Invalidate the tray models submenu cache so the next
            # right-click reflects the deletion.
            try:
                from voice_typer.server.tray_models import (
                    invalidate_model_availability_cache,
                )

                invalidate_model_availability_cache()
            except Exception:
                log.debug("[SERVICE] invalidate_model_availability_cache failed", exc_info=True)
            # PERF-10 / SVC-9: on-disk model state changed — force the next
            # get_model_status() poll to recompute instead of serving stale
            # (still-present) cache.
            self._invalidate_model_status_cache()
            return {
                "success": True,
                "message": f"Deleted model '{model_name}' ({repo_id}).",
            }
        except Exception as exc:
            log.warning("[SERVICE] delete_model failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    # ── Stale-active model clear ( STALE-ACTIVE) ──────────────

    def _clear_stale_active_model(self, model_name: str) -> dict[str, object]:
        """Clear a stale active-model selection whose files are missing from disk.

        Called by :meth:`delete_model` when the configured active model is
        NOT present in the HF cache (removed out-of-band — deleted folder,
        moved cache, wiped disk). The config keeps pointing at a model that
        doesn't exist, so the Models page would otherwise show a phantom
        "Active" model with no way to clear it.

        Switches the active config to the first downloaded model (if any)
        via the canonical ``apply_config`` path (lock + validate + setattr +
        save), pushes ``config_changed`` so the renderer reapplies active
        state immediately, and invalidates the model-status cache.

        Defensive by design: any config-mutation failure is logged and the
        delete still returns success — the files are already gone, and the
        status-cache invalidation alone guarantees the next UI poll reflects
        truth (``downloaded: false``).
        """
        updates: dict[str, str] = {}
        replacement = self._pick_downloaded_fallback_model(model_name)
        if replacement is not None:
            # ``_pick_downloaded_fallback_model`` never returns a distil
            # variant (frontend active-keying mismatch), so the only
            # whisper-family backend left here is ``whisper``.
            if replacement.backend == "whisper":
                updates = {"asr_backend": "whisper", "model_size": replacement.name}
            else:
                updates = {"asr_backend": replacement.backend, "model_size": replacement.name}
        else:
            # NO downloaded model exists to fall back to — enter the
            # genuine "no model selected" state (``model_size=""``)
            # instead of leaving the config pointing at a phantom
            # model. ``NO_MODEL_SIZE`` is allowlisted for the IPC
            # ``set_config`` path and preserved by load-time coercion,
            # so this writes cleanly and survives restarts. The app
            # reports "No model selected" (tray tooltip, Models page)
            # until the user picks a model.
            updates = {"model_size": NO_MODEL_SIZE}
        try:
            # canonical config-mutation path (SEC-002 allowlisted keys,
            # config-mutation lock, side-effects, save_strict). Delegates
            # via the declared _config_applier: `apply_config` itself is
            # provided by ConfigMutationMixin on the COMPOSED service,
            # which this standalone mixin can't see statically.
            self._config_applier.apply_config(updates)
            if replacement is not None:
                log.info(
                    "[SERVICE] delete_model: stale active model '%s' cleared — switched to '%s'",
                    model_name,
                    replacement.name,
                )
            else:
                log.info(
                    "[SERVICE] delete_model: stale active model '%s' cleared — "
                    "no other model is downloaded, entering 'no model selected' state",
                    model_name,
                )
        except Exception as exc:
            # Never turn a successful delete into a failure. The files
            # are gone; the config-clear is best-effort (the status-cache
            # invalidation below still un-sticks the phantom state on the
            # next poll). ``apply_config`` rolls the in-memory config back
            # to the pre-setattr values on ``save_strict`` failure, so
            # ``updates`` is reset — the message + ``config_changed``
            # push below must not claim the switch happened.
            log.warning(
                "[SERVICE] delete_model: cleared stale selection for '%s' but failed to persist config %s: %s",
                model_name,
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
        if updates:
            if updates.get("model_size") == NO_MODEL_SIZE:
                message = f"Model '{model_name}' was not on disk — no model selected. Pick a model on the Models page."
            else:
                message = f"Model '{model_name}' was not on disk — switched to '{updates['model_size']}'."
        else:
            message = f"Model '{model_name}' was not on disk — nothing to delete."
        log.info("[SERVICE] delete_model: %s", message)
        return {"success": True, "message": message}

    def _pick_downloaded_fallback_model(self, exclude_name: str) -> ModelMetadata | None:
        """Return metadata for the first downloaded model other than ``exclude_name``.

        Iterates :data:`MODEL_REGISTRY` in registration order and returns the
        first model whose on-disk status (computed fresh via
        :meth:`_compute_model_status`, bypassing the 5s TTL cache) reports
        ``downloaded=True``. Returns ``None`` when no other model is on disk.

        Used by :meth:`_clear_stale_active_model` to pick the replacement
        for a deleted stale-active model — preferring the first downloaded
        model keeps the app immediately usable after the config clear.

        Distil-whisper variants are SKIPPED: the frontend's ``isModelActive``
        keys distil models by ``asr_backend === "distil-whisper"`` (a value
        the ``set_config`` allowlist never writes), so a distil fallback
        written as ``asr_backend: "whisper"`` would never render as active
        in the UI — an inconsistency worse than leaving the config as-is.
        """
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
        any recognized models into the app's HF cache.

        Accepts a path to a directory that may contain
        ``models--Org--RepoName`` subdirectories (the standard HuggingFace
        hub cache layout).  Each subdirectory whose name matches a known
        model in :data:`MODEL_REGISTRY` is copied into the app's HF cache
        so the renderer reports it as "downloaded".

        Returns:
            dict with keys:
              - ``success``: always True (errors are per-model, not fatal)
              - ``imported``: list of model names that were successfully copied
              - ``found``: list of model names that matched the registry
              - ``errors``: list of ``{"model": str, "error": str}`` for failures
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

        # Collect all candidate subdirectories (one level deep + the
        # selected dir itself if it's a model cache dir).
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
                # symlinks.  ``shutil.copytree`` with ``symlinks=False``
                # would *follow* any symlink in the source tree and copy
                # the target's contents into the destination — so a
                # poisoned model dir with a symlink to ``~/.ssh/id_rsa``
                # would silently copy the SSH key into the app's HF
                # cache.  Later, ``verify_model_integrity()`` follows
                # symlinks via ``rglob("*")``, so the leak would persist
                # and be readable by any code that walks the cache.
                # HuggingFace hub cache dirs never legitimately contain
                # symlinks at the *source* side (the hub's symlinks live
                # inside its own cache, not in user-supplied import
                # dirs), so rejecting up-front is safe.
                symlink = _find_symlink_in_tree(src_path)
                if symlink is not None:
                    log.warning(
                        "[SERVICE] import_model: refusing to import %s — "
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
                # check above is the primary gate; this ensures that even
                # if a symlink slips through (e.g. a race condition where
                # a symlink is created after the check), copytree will
                # follow it rather than preserve it as a symlink in the
                # destination cache.  Combined with the check above, this
                # means symlinks are never silently preserved.
                shutil.copytree(src_path, dest, symlinks=False)
                imported_models.append(model_name)
            except Exception as exc:
                # redact str(exc) before appending to per-model
                # errors list. shutil.Error enumerates source/dest file paths,
                # leaking cache layout to renderer. Sister IPC methods all
                # wrap str(exc) with redact_secret(redact_url(...)).
                redacted = redact_secret(redact_url(str(exc)))
                log.warning(
                    "[SERVICE] import_model: per-model import failed for '%s': %s",
                    model_name,
                    redacted,
                )
                errors.append({"model": model_name, "error": redacted})

        # Invalidate the tray models cache so the next right-click
        # reflects the newly-imported models.
        if imported_models:
            try:
                from voice_typer.server.tray_models import (
                    invalidate_model_availability_cache,
                )

                invalidate_model_availability_cache()
            except Exception:
                # sister calls log cache-invalidation failures at
                # DEBUG. Previous pass silently swallowed with no log entry,
                # making stale tray-cache bugs invisible.
                log.debug(
                    "[SERVICE] import_model: invalidate_model_availability_cache failed",
                    exc_info=True,
                )

        if imported_models:
            log.info(
                "[SERVICE] Model import: %d found, %d imported, %d errors",
                len(found_models),
                len(imported_models),
                len(errors),
            )
        elif found_models:
            log.warning(
                "[SERVICE] Model import: %d found, 0 imported, %d errors — all imports failed",
                len(found_models),
                len(errors),
            )

        return {
            "success": True,
            "imported": imported_models,
            "found": found_models,
            "errors": errors,
        }

    # ── Download model () ─────────────────────────────────────
