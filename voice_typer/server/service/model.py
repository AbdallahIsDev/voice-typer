"""Model domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Model download / delete / status / import,
per-download cancellation, deps probes, and the HuggingFace
consent gate.
"""

import logging
import secrets
import threading
import time

from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.branding import APP_NAME
from voice_typer.server.service._base import ServiceMixinBase
from voice_typer.server.service._helpers import _find_symlink_in_tree

log = logging.getLogger(__name__)

# PERF-10 / SVC-9: TTL (seconds) for the get_model_status cache.  The IPC
# renderer polls ~every 2s; a 5s TTL cuts filesystem syscall rate ~60% with
# no user-visible staleness (cache is invalidated on download/delete).
_MODEL_STATUS_CACHE_TTL_S = 5.0

# XA-13-C1: user-facing messages for each ``download_parakeet_weights``
# reason code. The service layer unpacks the ``(success, reason, exc_info)``
# 3-tuple and maps the short reason code to a human-readable message so
# the renderer's error toast / tray notification tells the user WHAT
# went wrong ("not enough disk space") rather than the raw code
# ("disk_space_insufficient").
#
# Keys mirror the reason codes documented in
# ``asr_setup.download_parakeet_weights`` (see that function's docstring
# for the full list). Unknown reason codes fall back to a generic
# "Download failed: <reason>" message at the call site.
_PARAKEET_REASON_MESSAGES: dict[str, str] = {
    "huggingface_consent_false": (
        "HuggingFace consent not given. Enable HuggingFace downloads in Settings to download the Parakeet model."
    ),
    "huggingface_hub_missing": (
        "huggingface_hub is not installed. Install it with `pip install huggingface_hub` and try again."
    ),
    "disk_space_insufficient": (
        "Not enough disk space to download the Parakeet model (~2.5 GB). Free up space and try again."
    ),
    "download_retry_exhausted": (
        "Download failed after multiple retries. Check your network connection and try again."
    ),
    "integrity_check_failed": (
        "Downloaded model failed integrity verification. The cached "
        "files may be corrupt — the cache was cleared; please retry."
    ),
}


class ModelMixin(ServiceMixinBase):
    """Model-domain service methods.

    Covers download/delete/import/status, per-download cancellation
    (HIGH-8 / SERVICE-1), dependency probes, and the HuggingFace
    consent gate (CR-11 / NEW-PRIV-005).
    """

    # ── Download cancellation helpers (HIGH-8 / SERVICE-1) ──────────

    def _register_download(self, model_name: str) -> str:
        """Create a per-download cancellation Event and return its id.

        Generates a unique ``download_id`` so two concurrent
        ``download_model`` calls don't share state. Stores the Event in
        ``self._download_cancel_events`` under the lock and marks it as
        the active download. ``download_model`` must call
        :meth:`_unregister_download` (in a ``finally`` or at each
        return point) to avoid leaking entries in the dict.
        """
        download_id = f"{model_name}:{secrets.token_hex(8)}"
        event = threading.Event()
        with self._download_cancel_lock:
            self._download_cancel_events[download_id] = event
            self._active_download_id = download_id
        return download_id

    def _unregister_download(self, download_id: str) -> None:
        """Remove the per-download Event from the dict and clear
        ``_active_download_id`` if it still points at us.

        Safe to call from any ``download_model`` exit path (success,
        failure, cancellation). The lookup is under the lock so a
        concurrent ``cancel_model_download`` doesn't see a half-removed
        entry.
        """
        with self._download_cancel_lock:
            self._download_cancel_events.pop(download_id, None)
            if self._active_download_id == download_id:
                self._active_download_id = None

    def _is_download_cancelled(self, download_id: str) -> bool:
        """Return True if the download identified by ``download_id``
        has been cancelled.

        HIGH-8 / SERVICE-1: looks up the Event in the per-download dict
        (under the lock) so a concurrent ``download_model`` call's
        cancel signal doesn't bleed into this download. Returns False
        if the entry is missing (already cleaned up, or never
        registered) — the None-guard prevents the AttributeError that
        the previous single-attribute design raised when a sibling
        download set the attribute to ``None``.
        """
        with self._download_cancel_lock:
            event = self._download_cancel_events.get(download_id)
        return event.is_set() if event is not None else False

    # ── Volume / Model status (ARCH-005) ────────────────────────

    def get_model_status(self) -> dict[str, object]:
        """Return the model download/dependency status for each ASR backend.

        PERF-10 / SVC-9: results are cached for ``_MODEL_STATUS_CACHE_TTL_S``
        seconds so the renderer's ~2s poll doesn't re-stat the filesystem for
        every model on every call. The cache is invalidated immediately on any
        download/delete that changes on-disk model state via
        :meth:`_invalidate_model_status_cache`, so correctness is preserved
        (a completed download or deletion is reflected on the next poll, well
        within the TTL window). Returns the *same* cached dict object within
        the TTL to satisfy callers that compare identity.
        """
        now = time.monotonic()
        with self._model_status_cache_lock:
            if self._model_status_cache is not None and (now - self._model_status_cache_ts) < _MODEL_STATUS_CACHE_TTL_S:
                return self._model_status_cache
        status = self._compute_model_status()
        with self._model_status_cache_lock:
            self._model_status_cache = status
            self._model_status_cache_ts = now
        return status

    def _compute_model_status(self) -> dict[str, object]:
        """Compute the model status from the filesystem (no caching).

        PERF-10 / SVC-9: extracted from :meth:`get_model_status` so the
        expensive per-model directory checks + dependency probes run at most
        once per TTL window.
        """
        import os

        from voice_typer.server.config import _config_dir

        config = self._app.config
        status = {}

        # Whisper models — check ALL models from the registry, using
        # the same cache directory that download_model writes to.
        from voice_typer.server.model_registry import MODEL_REGISTRY, get_model_metadata

        cache_dir = os.path.join(str(_config_dir()), "huggingface", "hub")
        # SVC-9 / PERF-10: stat the cache_dir ROOT once (hoisted above the
        # loop) instead of re-statting it on every model iteration.
        cache_dir_exists = os.path.isdir(cache_dir)
        for meta in MODEL_REGISTRY.values():
            if meta.backend not in ("whisper", "distil-whisper"):
                continue
            repo_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
            downloaded = cache_dir_exists and os.path.isdir(os.path.join(cache_dir, repo_dir_name))
            status[meta.name] = {
                "downloaded": downloaded,
                "deps_ok": True,  # faster-whisper is always available
            }

        # Qwen model — check both the configured path AND the HF cache dir.
        qwen_path = getattr(config, "qwen_model_path", None)
        qwen_in_cache = False
        qwen_meta = get_model_metadata("qwen")
        if qwen_meta is not None:
            qwen_repo_dir = f"models--{qwen_meta.repo_id.replace('/', '--')}"
            qwen_in_cache = cache_dir_exists and os.path.isdir(os.path.join(cache_dir, qwen_repo_dir))
        status["qwen"] = {
            "downloaded": bool(qwen_path and os.path.isdir(qwen_path)) or qwen_in_cache,
            "deps_ok": self._check_qwen_deps(),
        }

        # Parakeet model
        parakeet_path = getattr(config, "parakeet_model_path", None)
        parakeet_in_cache = False
        parakeet_meta = get_model_metadata("parakeet")
        if parakeet_meta is not None:
            parakeet_repo_dir = f"models--{parakeet_meta.repo_id.replace('/', '--')}"
            parakeet_in_cache = cache_dir_exists and os.path.isdir(os.path.join(cache_dir, parakeet_repo_dir))
        status["parakeet"] = {
            "downloaded": bool(parakeet_path and os.path.isdir(parakeet_path)) or parakeet_in_cache,
            "deps_ok": self._check_parakeet_deps(),
        }

        return status

    def _invalidate_model_status_cache(self) -> None:
        """PERF-10 / SVC-9: drop the cached model-status dict.

        Called whenever on-disk model state may have changed (model
        downloaded or deleted). The next :meth:`get_model_status` call
        recomputes from the filesystem and re-arms the TTL cache. Safe to
        call when no cache is populated yet.
        """
        with self._model_status_cache_lock:
            self._model_status_cache = None
            self._model_status_cache_ts = 0.0

    def delete_model(self, model_name: str) -> dict[str, object]:
        """Delete a downloaded model from the HuggingFace cache.

        LOG-001: logs success/failure with model name and repo ID.

        NEW-UX-005: previously the Models page only removed the model
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
            # Qwen is registered in MODEL_REGISTRY (repo_id="Qwen/Qwen-Audio");
            # fall through to the same cache-dir check so absent models
            # report "not downloaded" rather than "Unknown model".
            repo_id = "Qwen/Qwen-Audio"
        else:
            repo_id = None

        if not repo_id:
            return {"success": False, "message": f"Unknown model: {model_name}"}

        # Don't allow deleting the active model.
        current_backend = getattr(self._app.config, "asr_backend", "whisper")
        current_model = getattr(self._app.config, "model_size", "tiny.en")
        is_active = (
            (model_name == current_model and current_backend in ("whisper", "distil-whisper"))
            or (model_name == "parakeet" and current_backend == "parakeet")
            or (model_name == "qwen" and current_backend == "qwen")
        )
        if is_active:
            return {
                "success": False,
                "message": "Cannot delete the active model. Switch to another model first.",
            }

        model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
        if not model_dir.exists():
            return {"success": False, "message": f"Model '{model_name}' is not downloaded."}

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

    def test_llm_connection(self) -> dict[str, object]:
        """Test the LLM polish API connection.

        NEW-DEAD-015: ``LLMPolisher.test_connection`` was previously
        dead — no IPC route or UI button invoked it.  We now expose
        it via the service layer so the renderer can wire up a "Test
        connection" button on the Settings page (where the user
        configures llm_api_key / llm_api_url / llm_model).

        Returns ``{"success": bool, "message": str}``.
        """
        cfg = getattr(self._app, "config", None)
        if cfg is None:
            return {"success": False, "message": "Config not loaded"}

        # CR-43 fix: gate on consent BEFORE sending any test request.
        # The polish production path (dictation_pipeline.py:650) requires
        # BOTH `llm_polish` AND `llm_polish_consent` to be True before
        # sending any HTTP request to the LLM endpoint. The previous
        # implementation of test_llm_connection bypassed the consent gate
        # — a user who explicitly denied consent (llm_polish_consent=False)
        # but had an API key configured could trigger an outbound HTTP POST
        # to llm_api_url (with Authorization: Bearer <key> header + the
        # literal "Hello" body) by clicking "Test Connection" in Settings.
        # The request leaks the user's IP, the existence of an active API
        # key, and a Python urllib User-Agent to the configured LLM
        # endpoint, despite explicit user opt-out.
        if not getattr(cfg, "llm_polish_consent", False):
            return {
                "success": False,
                "message": "LLM polish consent not given. Enable LLM polish in Settings to test the connection.",
            }

        # Use the same consent + key-resolution logic as the polish path
        # (dictation_pipeline.py:288-300).
        effective_key = getattr(cfg, "llm_api_key", "") or ""
        if not effective_key:
            return {"success": False, "message": "API key not configured"}

        try:
            from voice_typer.server.llm_polish import LLMPolisher

            polisher = LLMPolisher(
                api_key=effective_key,
                api_url=getattr(cfg, "llm_api_url", "") or None,
                model=getattr(cfg, "llm_model", "") or None,
                preset=getattr(cfg, "llm_preset", "professional"),
                enabled=True,
            )
            success, message = polisher.test_connection()
            return {"success": success, "message": message}
        except Exception as exc:
            log.warning("[SERVICE] test_llm_connection failed: %s", exc)
            return {"success": False, "message": redact_secret(redact_url(str(exc)))}

    def _check_qwen_deps(self) -> bool:
        """Check if qwen_asr package is importable.

        Uses :func:`importlib.util.find_spec` so the package's top-level
        code is NOT executed (qwen_asr pulls in heavy transitive deps
        that allocate memory on import). The probe only resolves the
        module spec — it doesn't run ``qwen_asr.__init__``.

        ``find_spec`` raises :class:`ValueError` when the module is
        already in ``sys.modules`` but its ``__spec__`` is ``None``
        (some wheel layouts / namespace packages hit this). In that
        case the module IS available, so we fall back to a
        ``sys.modules`` membership check rather than reporting
        ``deps_ok=False``.
        """
        import importlib.util
        import sys

        try:
            return importlib.util.find_spec("qwen_asr") is not None
        except ValueError:
            return "qwen_asr" in sys.modules

    def _check_parakeet_deps(self) -> bool:
        """Check if the Parakeet engine's key runtime dependency is importable.

        The Parakeet engine (``parakeet_engine.py``) defers its heavy imports
        (``torch``, ``transformers``, ``psutil``) inside ``_ensure_imports``.
        The most critical of these is ``torch`` — without it the engine cannot
        initialise.  Previously this method checked for ``nemo_toolkit`` which
        is not a dependency of the Parakeet engine in this codebase, causing
        ``deps_ok`` to always be ``False`` and blocking the "Select" button
        in the Models page even when the user was actively using Parakeet.

        Uses :func:`importlib.util.find_spec` so ``torch`` is not actually
        imported — torch's ``__init__`` allocates hundreds of MB of memory
        and inits CUDA contexts just to probe presence. ``find_spec`` only
        resolves the module's file path via the import machinery, so it
        has no side effects.

        ``find_spec`` raises :class:`ValueError` when ``torch`` is already
        in ``sys.modules`` but its ``__spec__`` is ``None`` (observed with
        some torch wheel layouts in test envs). In that case torch IS
        importable, so fall back to a ``sys.modules`` membership check.
        """
        import importlib.util
        import sys

        try:
            return importlib.util.find_spec("torch") is not None
        except ValueError:
            return "torch" in sys.modules

    # ── Model import ──────────────────────────────────────────────────────

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
                # RW-5: refuse to import a model cache that contains
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
                # RW-5: symlinks=False as defense-in-depth.  The explicit
                # check above is the primary gate; this ensures that even
                # if a symlink slips through (e.g. a race condition where
                # a symlink is created after the check), copytree will
                # follow it rather than preserve it as a symlink in the
                # destination cache.  Combined with the check above, this
                # means symlinks are never silently preserved.
                shutil.copytree(src_path, dest, symlinks=False)
                imported_models.append(model_name)
            except Exception as exc:
                errors.append({"model": model_name, "error": str(exc)})

        # Invalidate the tray models cache so the next right-click
        # reflects the newly-imported models.
        if imported_models:
            try:
                from voice_typer.server.tray_models import (
                    invalidate_model_availability_cache,
                )

                invalidate_model_availability_cache()
            except Exception:
                pass

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

    # ── Download model (UX-005) ─────────────────────────────────────

    def cancel_model_download(self) -> dict:
        """Cancel an in-progress model download.

        NEW-PRIV-011: sets the cancellation event so the download_model
        polling loop stops waiting and returns a "cancelled" result.

        HIGH-8 / SERVICE-1: signals the active download's per-download
        Event (looked up in ``self._download_cancel_events`` under the
        lock). Without the per-download lookup, two concurrent
        ``download_model`` calls would each overwrite a shared attribute
        and only one would actually get cancelled.

        EC-FIX-15 / EC-24: the legacy single-instance
        ``self._download_cancel_event`` fallback branch has been REMOVED.
        All cancel signals now flow through the per-download dict.
        """
        cancelled_any = False
        # HIGH-8 / SERVICE-1: per-download dict path — signal the
        # currently-active download's Event, if any.
        with self._download_cancel_lock:
            active_id = self._active_download_id
            active_event = self._download_cancel_events.get(active_id) if active_id is not None else None
        if active_event is not None:
            active_event.set()
            cancelled_any = True
        if cancelled_any:
            log.info("[SERVICE] Model download cancellation requested")
            return {"cancelled": True}
        return {"cancelled": False}

    def pause_model_download(self) -> dict:
        """Pause an in-progress model download.

        NEW-PAUSE-001: delegates to :func:`asr_setup.set_download_paused`,
        which sets a module-level flag that the download polling loop
        checks between iterations.  While paused, the polling loop
        stops pushing progress updates (and the renderer shows a
        "paused" indicator).  The underlying HuggingFace transfer
        continues in the background; if the user wants to stop the
        network transfer entirely they should use Cancel.
        """
        from voice_typer.server.asr_setup import set_download_paused

        paused = set_download_paused(True)
        if paused:
            log.info("[SERVICE] Model download pause requested")
        return {"paused": paused}

    def resume_model_download(self) -> dict:
        """Resume a paused model download.

        NEW-PAUSE-001: clears the module-level pause flag set by
        :meth:`pause_model_download`.  The polling loop picks up where
        it left off on the next iteration.
        """
        from voice_typer.server.asr_setup import set_download_paused

        set_download_paused(False)
        log.info("[SERVICE] Model download resume requested")
        return {"resumed": True}

    def _require_huggingface_consent(self, model_name: str) -> dict | None:
        """CR-11: Gate IPC-triggered HuggingFace downloads on explicit consent.

        Mirrors the consent gate in
        :meth:`voice_typer.server.transcription.TranscriptionEngine._pre_download_model`
        (transcription.py:835-849).  The IPC download path previously
        had NO consent check, so clicking "Download" on the Models page
        phoned home to huggingface.co (revealing the user's IP to a
        US-headquartered third party) without the explicit GDPR
        Art. 13/44 consent that ``config.huggingface_consent`` was
        specifically designed to gate (NEW-PRIV-005).

        Returns ``None`` when consent has been given — the caller
        proceeds with the download.  Returns a failure dict AND
        publishes a ``consent_required`` event when consent is missing;
        the renderer is responsible for showing the consent dialog and
        retrying the download after the user accepts.

        Defensive: ``self._app.config`` may be ``None`` in degenerate
        paths (test stubs, benchmark harness).  Treat missing config
        as NOT consented — safe default per GDPR Art. 6/13.
        """
        from voice_typer.server import event_bus

        cfg = getattr(self._app, "config", None)
        consent = False if cfg is None else bool(getattr(cfg, "huggingface_consent", False))
        if not consent:
            log.warning(
                "[SERVICE] HuggingFace consent not given — refusing to download "
                "model '%s' via IPC. The renderer should show the consent dialog.",
                model_name,
            )
            try:
                event_bus.publish(
                    {
                        "type": "consent_required",
                        "data": {
                            "provider": "huggingface",
                            "model": model_name,
                            "message": "HuggingFace consent required before downloading model.",
                        },
                    }
                )
            except Exception:
                log.debug("[SERVICE] consent_required event push failed", exc_info=True)
            return {
                "success": False,
                "error": "HuggingFace consent required",
                "consent_required": True,
                "model": model_name,
            }
        return None

    def download_model(self, model_name: str) -> dict[str, object]:
        """Download a model weight file via HuggingFace.

        UX-005: Downloads the specified model (tiny.en, small.en, medium.en,
        large-v3, qwen, parakeet) to the local HF cache. Pushes
        ``download_progress`` events to the renderer so the Models page
        can update its progress bar and status text in real time, and
        fires a tray notification on completion / failure.
        Returns a result dict with success status.

        DT-49: the return annotation is widened from the
        ``DownloadResult`` TypedDict union (removed) to
        ``dict[str, object]`` to match the actual runtime shape. The
        implementation returns plain ``dict`` literals (not TypedDict
        instances); the TypedDict union gave no real protection and
        caused 3 baselined ``bad-return`` pyrefly errors. The runtime
        shape is verified by ``tests/test_service_fixes.py``.

        NEW-MODEL-001: now supports the turbo + distilled variants via
        :mod:`voice_typer.server.model_registry`.  The repo_id is
        resolved from the registry instead of being hard-coded.

        NEW-PAUSE-001: the polling loop checks
        :func:`asr_setup.is_download_paused` between iterations.  When
        paused, progress updates freeze and a ``paused: True`` event is
        pushed once per transition.  Resume clears the flag and pushes
        a ``resumed: True`` event.

        CR-11: the Whisper and Parakeet branches now gate on
        :meth:`_require_huggingface_consent` before any HuggingFace
        network call, mirroring the consent gate that already lived in
        ``TranscriptionEngine._pre_download_model`` (transcription.py:835-849).
        The Qwen branch uses a local file path and does not phone home,
        so it is exempt from the consent gate.

        RACE-008: daemon=True is acceptable because _do_download only
        writes to the HF cache dir — no critical cleanup. The download
        completes or fails naturally; on force-kill the partial
        download is resumed on next start via HF's resume_download=True.
        """
        import os

        # UX-005: helper to push progress events to the renderer.
        from voice_typer.server import event_bus

        def _push_progress(
            progress: int,
            status: str,
            *,
            downloaded_bytes: int | None = None,
            total_bytes: int | None = None,
            speed_bytes_per_sec: float | None = None,
            eta_seconds: float | None = None,
            paused: bool | None = None,
            resumed: bool | None = None,
        ) -> None:
            """Push a download_progress event with rich metadata.

            ``progress`` (0-100) and ``status`` (human-readable) are
            always present (backward compat with UX-005 tests).  The
            remaining fields are optional and only included when
            meaningful (e.g. during active transfer, not for "cached"
            or "cancelled" events).
            """
            data: dict = {
                "model": model_name,
                "progress": max(0, min(100, int(progress))),
                "status": status,
            }
            if downloaded_bytes is not None:
                data["downloaded_bytes"] = int(downloaded_bytes)
            if total_bytes is not None:
                data["total_bytes"] = int(total_bytes)
            if speed_bytes_per_sec is not None:
                data["speed_bytes_per_sec"] = float(speed_bytes_per_sec)
            if eta_seconds is not None:
                data["eta_seconds"] = float(eta_seconds)
            if paused is not None:
                data["paused"] = bool(paused)
            if resumed is not None:
                data["resumed"] = bool(resumed)
            event_bus.publish({"type": "download_progress", "data": data})

        def _notify(title: str, message: str) -> None:
            try:
                self._app.tray.notify(title, message)
            except Exception:
                log.debug("[SERVICE] tray notify failed", exc_info=True)

        # WR-14: pyrefly unbound-name — initialize download_id BEFORE the
        # outer ``try:`` block so the ``except Exception`` handler below
        # can safely reference it even if the very first statement inside
        # the try (the ``from voice_typer.server.model_registry import
        # get_model_metadata`` import) raises ImportError before the
        # previous (in-try) initialization at the old line 709 executed.
        # The HIGH-8 / SERVICE-1 comment below still applies — this
        # initialization is the safety net for the outer handler.
        download_id: str | None = None

        try:
            # NEW-MODEL-001: consult the model registry so we support
            # turbo + distilled variants without hard-coding name-to-repo
            # mappings.  Falls back to the legacy hard-coded tuple for
            # any registry drift.
            from voice_typer.server.model_registry import get_model_metadata

            # HIGH-8 / SERVICE-1: download_id is initialized above the
            # outer ``try:`` so the outer ``except Exception`` handler
            # can safely reference it (and call _unregister_download)
            # even when the exception was raised before the inner
            # _register_download call was reached.

            model_meta = get_model_metadata(model_name)
            is_whisper_family = model_meta is not None and model_meta.backend in ("whisper", "distil-whisper")
            if is_whisper_family:
                # CR-11: HuggingFace consent gate.  Without this check,
                # clicking "Download" on the Models page would phone
                # home to huggingface.co before the user had explicitly
                # opted in via the consent dialog (NEW-PRIV-005).
                # Mirrors TranscriptionEngine._pre_download_model
                # (transcription.py:835-849).  The gate must fire BEFORE
                # any snapshot_download call (including the
                # local_files_only cache probe) so that a user who has
                # NOT consented cannot trigger any HuggingFace Hub
                # interaction from the IPC path.
                consent_err = self._require_huggingface_consent(model_name)
                if consent_err is not None:
                    return consent_err
                log.info(
                    "[SERVICE] Starting download for '%s' (repo=%s, backend=%s)",
                    model_name,
                    model_meta.repo_id if model_meta else "unknown",
                    model_meta.backend if model_meta else "unknown",
                )
                # NEW-PAUSE-001: reset the pause flag at the start of
                # every fresh download so a stale ``paused=True`` from
                # a previous download doesn't carry over.
                from voice_typer.server.asr_setup import (
                    clear_download_pause_state,
                    is_download_paused,
                    reset_download_pause_state,
                    wait_while_paused,
                )

                reset_download_pause_state()

                _push_progress(0, f"Starting download for {model_name}...")
                # UX-005: pre-download via snapshot_download so we can
                # poll the HF cache file size for progress reporting.
                # TranscriptionEngine.load() blocks with no progress
                # callback; doing the snapshot_download first lets us
                # emit progress events, then load() just reads from
                # the local cache.
                try:
                    from huggingface_hub import snapshot_download

                    from voice_typer.server.config import _config_dir

                    # NEW-MODEL-001: use the registry's repo_id so
                    # distilled variants (Systran/faster-distil-whisper-*)
                    # resolve correctly.
                    assert model_meta is not None  # narrowed by is_whisper_family
                    repo_id = model_meta.repo_id
                    cache_dir = _config_dir() / "huggingface" / "hub"

                    # SEC-audit-005: Allowlist of file patterns permitted in downloads
                    _service_allow_patterns = [
                        "*.safetensors",
                        "*.bin",
                        "config.json",
                        "tokenizer.json",
                        "tokenizer_config.json",
                        "special_tokens_map.json",
                        "preprocessor_config.json",
                        "feature_extractor_config.json",
                        "generation_config.json",
                        "model.safetensors.index.json",
                        "*.model",
                    ]
                    # SEC-audit-005: Use pinned revision from MODEL_HASHES manifest
                    from voice_typer.server.security import MODEL_HASHES

                    _service_revision = MODEL_HASHES.get(repo_id, {}).get("revision", "main")

                    _push_progress(5, f"Checking cache for {model_name}...")
                    # Try local-only first; if cached, skip the polling.
                    try:
                        snapshot_download(
                            repo_id=repo_id,
                            revision=_service_revision,
                            allow_patterns=_service_allow_patterns,
                            local_files_only=True,
                        )
                        log.info(
                            "[SERVICE] Model '%s' already cached (repo=%s) — skipping download",
                            model_name,
                            repo_id,
                        )
                        _push_progress(100, f"{model_name} already cached")
                    except Exception:
                        # NEW-MODEL-001: pull target size from the
                        # registry instead of the hard-coded size_targets
                        # table.  Falls back to 500 MB if missing.
                        target_mb = model_meta.download_size_mb if model_meta.download_size_mb else 500
                        target_bytes = target_mb * 1024 * 1024
                        _push_progress(
                            10,
                            f"Downloading {model_name} from HuggingFace...",
                            total_bytes=target_bytes,
                        )
                        # Start the download in a thread so we can poll
                        # the cache directory size while it runs.
                        import threading
                        import time

                        # HIGH-8 / SERVICE-1: register a per-download
                        # cancellation Event in the dict (under the
                        # lock) instead of overwriting the shared
                        # ``self._download_cancel_event`` attribute.
                        # Two concurrent download_model calls now each
                        # get their own Event keyed by download_id, so
                        # neither can clobber the other's reference.
                        download_id = self._register_download(model_name)
                        download_err: list = []

                        def _do_download():
                            try:
                                # PROD-004: use retry-with-backoff wrapper
                                from voice_typer.server.transcription import _download_with_retry

                                _download_with_retry(
                                    snapshot_download,
                                    repo_id=repo_id,
                                    revision=_service_revision,
                                    allow_patterns=_service_allow_patterns,
                                    resume_download=True,
                                    cache_dir=str(cache_dir),
                                )
                            except Exception as e:
                                download_err.append(e)

                        # RACE-008: daemon=True is acceptable because
                        # _do_download only writes to the HF cache dir —
                        # no critical cleanup. The download completes or
                        # fails naturally; on force-kill the partial
                        # download is resumed on next start via HF's
                        # resume_download=True.
                        t = threading.Thread(target=_do_download, daemon=True)
                        t.start()
                        log.info(
                            "[SERVICE] Download thread started for '%s' (target=%d MB)",
                            model_name,
                            target_mb,
                        )
                        # Poll cache size until download thread exits OR
                        # the user cancels OR the user pauses.
                        cancelled = False
                        # NEW-PAUSE-001: track pause/resume transitions
                        # so we only push the event once per state
                        # change (not once per 1-second poll iteration).
                        last_paused_state = False
                        # NEW-PAUSE-001: track timing for speed / ETA.
                        last_progress_time = time.monotonic()
                        last_total_bytes_seen = 0
                        while t.is_alive():
                            # HIGH-8 / SERVICE-1: check for cancellation
                            # via the per-download helper so a sibling
                            # download_model call's cancel signal (or
                            # cleanup) doesn't bleed into this loop. The
                            # helper does a None-guarded dict lookup
                            # under the lock and returns False if our
                            # entry has already been removed.
                            if self._is_download_cancelled(download_id):
                                cancelled = True
                                log.info(
                                    "[SERVICE] Download of %s cancelled by user",
                                    model_name,
                                )
                                _push_progress(0, "Download cancelled")
                                break
                            # NEW-PAUSE-001: check for pause.  When
                            # paused, block for up to 1s (replacing the
                            # normal ``t.join(timeout=1.0)``), then
                            # continue the loop.  We push a single
                            # ``paused: True`` event on transition and a
                            # single ``resumed: True`` event when the
                            # pause clears.
                            currently_paused = is_download_paused()
                            if currently_paused != last_paused_state:
                                # State transition — push the event.
                                transition_pct = max(
                                    0, min(95, int(10 + (last_total_bytes_seen / max(1, target_bytes)) * 85))
                                )
                                if currently_paused:
                                    _push_progress(
                                        transition_pct,
                                        f"Download of {model_name} paused",
                                        downloaded_bytes=last_total_bytes_seen,
                                        total_bytes=target_bytes,
                                        paused=True,
                                    )
                                else:
                                    _push_progress(
                                        transition_pct,
                                        f"Download of {model_name} resumed",
                                        downloaded_bytes=last_total_bytes_seen,
                                        total_bytes=target_bytes,
                                        resumed=True,
                                    )
                                last_paused_state = currently_paused
                            if currently_paused:
                                # Wait for resume (or cancel), then loop.
                                wait_while_paused(timeout_s=1.0)
                                continue
                            t.join(timeout=1.0)
                            try:
                                # PERF-21 / XV-2 / PVT-025: scope the filesystem
                                # walk to the in-progress model's HF cache subdir,
                                # NOT the entire HF hub cache root. Previously
                                # ``cache_dir.rglob("*")`` ran once per second
                                # and stat'd every file in every cached model
                                # dir (thousands of stat() syscalls/s, 10-40%
                                # CPU). Now we only walk the downloading
                                # model's own directory.
                                model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"
                                if model_dir.exists():
                                    total_bytes_seen = sum(
                                        f.stat().st_size for f in model_dir.rglob("*") if f.is_file()
                                    )
                                    total_mb_seen = total_bytes_seen // (1024 * 1024)
                                    pct = min(95, int(10 + (total_mb_seen / target_mb) * 85))
                                    # Log progress at whole-number percentage thresholds
                                    if pct >= 25 and pct % 25 == 0:
                                        log.info(
                                            "[SERVICE] Download of '%s': %d%% (%d MB / ~%d MB)",
                                            model_name,
                                            pct,
                                            total_mb_seen,
                                            target_mb,
                                        )
                                    # NEW-PAUSE-001: compute speed & ETA.
                                    now = time.monotonic()
                                    elapsed = now - last_progress_time
                                    delta_bytes = total_bytes_seen - last_total_bytes_seen
                                    speed_bps: float | None = None
                                    eta_s: float | None = None
                                    if elapsed > 0 and delta_bytes >= 0:
                                        speed_bps = delta_bytes / elapsed
                                        if speed_bps > 0:
                                            eta_s = max(
                                                0.0,
                                                (target_bytes - total_bytes_seen) / speed_bps,
                                            )
                                    last_progress_time = now
                                    last_total_bytes_seen = total_bytes_seen
                                    _push_progress(
                                        pct,
                                        f"Downloading {model_name}: {total_mb_seen} MB / ~{target_mb} MB",
                                        downloaded_bytes=total_bytes_seen,
                                        total_bytes=target_bytes,
                                        speed_bytes_per_sec=speed_bps,
                                        eta_seconds=eta_s,
                                    )
                            except Exception:
                                pass
                        # NEW-PRIV-011: if cancelled, return early.
                        # HIGH-8 / SERVICE-1: remove our per-download
                        # Event from the dict so a sibling
                        # download_model call's cancel signal can't
                        # reach us after we've already exited the
                        # polling loop.
                        self._unregister_download(download_id)
                        # NEW-PAUSE-001: also clear the pause flag so
                        # a subsequent download starts unpaused.
                        clear_download_pause_state()
                        if cancelled:
                            return {
                                "success": False,
                                "cancelled": True,
                                "message": f"Download of {model_name} cancelled. "
                                "Partial files remain in cache; "
                                "retry to resume.",
                            }
                        if download_err:
                            # B904: suppress context from the failed
                            # cache-only snapshot_download attempt above.
                            raise download_err[0] from None
                        log.info(
                            "[SERVICE] Download of '%s' complete (%d MB)",
                            model_name,
                            last_total_bytes_seen // (1024 * 1024),
                        )
                        _push_progress(100, f"{model_name} download complete")
                except ImportError:
                    log.debug("[SERVICE] huggingface_hub not available, falling back to engine.load()")

                # VERIFY-LIGHT: skip the expensive full-model load verification.
                # Previously this loaded a TranscriptionEngine and called
                # engine.load() which allocated GPU/CPU memory and disrupted
                # the currently active model (Parakeet).  The model files are
                # already verified by HuggingFace's snapshot_download hash
                # checks — there's no need to load the entire model just to
                # confirm the files exist.
                log.info("[SERVICE] Download of '%s' verified via HF cache (no full model load)", model_name)
                _push_progress(100, f"Download of {model_name} complete")
                # NEW-PERF-004: invalidate the tray models submenu cache
                # so the next right-click reflects the newly-downloaded
                # model without waiting for the 5-second TTL.
                try:
                    from voice_typer.server.tray_models import (
                        invalidate_model_availability_cache,
                    )

                    invalidate_model_availability_cache()
                except Exception:
                    log.debug(
                        "[SERVICE] failed to invalidate tray model cache",
                        exc_info=True,
                    )
                # NEW-PRIV-011: clear cancel event on successful completion.
                # HIGH-8 / SERVICE-1: unregister the per-download Event
                # from the dict (no-op if download_id is None, e.g. the
                # model was already cached and we never entered the
                # polling-loop branch).
                if download_id is not None:
                    self._unregister_download(download_id)
                # NEW-PAUSE-001: clear the pause flag so subsequent
                # pause calls return False (no active download).
                clear_download_pause_state()
                _notify(APP_NAME, f"Model '{model_name}' downloaded successfully")
                # PERF-10 / SVC-9: on-disk model state changed — force the
                # next get_model_status() poll to recompute so the freshly
                # downloaded model shows as available immediately.
                self._invalidate_model_status_cache()
                return {"success": True, "model": model_name}
            elif model_name == "qwen":
                log.info("[SERVICE] Download requested for '%s' (Qwen backend)", model_name)
                qwen_path = getattr(self._app.config, "qwen_model_path", None)
                if qwen_path and os.path.isdir(qwen_path):
                    _push_progress(100, "Qwen model already cached")
                    return {"success": True, "model": model_name, "message": "Qwen model already cached"}
                _notify(APP_NAME, "Qwen model path not configured")
                return {"success": False, "error": "Qwen model path not configured. Set qwen_model_path in Settings."}
            elif model_name == "parakeet":
                # CR-11: HuggingFace consent gate.  Parakeet weights
                # are fetched from huggingface.co via
                # download_parakeet_weights(); gate the network call
                # on explicit user consent (NEW-PRIV-005).  Mirrors
                # TranscriptionEngine._pre_download_model
                # (transcription.py:835-849).  Must fire BEFORE the
                # asr_setup import + call so a user who has NOT
                # consented cannot trigger any HuggingFace Hub
                # interaction from the IPC path.
                consent_err = self._require_huggingface_consent(model_name)
                if consent_err is not None:
                    return consent_err
                log.info("[SERVICE] Download requested for '%s' (Parakeet backend, ~2.5 GB)", model_name)
                _push_progress(0, "Starting Parakeet download (~2.5 GB)...")
                from voice_typer.server.asr_setup import download_parakeet_weights

                # XA-13-C1: surface silent failures. Previously the
                # service called ``download_parakeet_weights()`` with no
                # arguments and discarded the return value, so every
                # failure (consent gate, missing huggingface_hub, disk
                # space, retry exhaustion, integrity check) was logged
                # as "complete" and pushed to the UI as 100% progress +
                # "downloaded successfully". The user saw a green
                # success toast but no model files were fetched.
                #
                # Now we:
                #   1. Forward ``config=self._app.config`` so the consent
                #      gate inside ``download_parakeet_weights`` passes
                #      (the upstream ``_require_huggingface_consent``
                #      check above already verified consent; this is
                #      defense-in-depth).
                #   2. Forward a ``progress_callback`` that bridges the
                #      function's progress messages to the renderer's
                #      ``download_progress`` event bus.
                #   3. Unpack the ``(success, reason, exc_info)`` 3-tuple
                #      and short-circuit to a structured error return on
                #      failure, mapping the reason code to a
                #      user-facing message via ``_PARAKEET_REASON_MESSAGES``.
                #
                # The unpack is defensive: some legacy / test fakes
                # return a bare ``bool`` rather than the 3-tuple. Treat
                # truthy → success, falsy → failure with reason
                # "unknown" so the test fakes don't break.
                def _parakeet_progress(message: str) -> None:
                    # Map the function's textual progress messages to
                    # the renderer's ``download_progress`` event. We
                    # don't know the byte-count, so we leave the rich
                    # metadata fields unset and just forward the status.
                    _push_progress(50, message)

                _push_progress(50, "Downloading Parakeet weights from HuggingFace...")
                dpw_result = download_parakeet_weights(
                    config=self._app.config,
                    progress_callback=_parakeet_progress,
                )
                # Defensive unpack: handle both the documented 3-tuple
                # and the legacy/test bare-bool shape.
                if isinstance(dpw_result, tuple):
                    success, reason, _exc_info = dpw_result
                else:
                    success = bool(dpw_result)
                    reason = "" if success else "unknown"
                if not success:
                    msg = _PARAKEET_REASON_MESSAGES.get(reason, f"Download failed: {reason}")
                    log.error(
                        "[SERVICE] Parakeet download failed (reason=%s): %s",
                        reason,
                        msg,
                    )
                    _push_progress(0, msg)
                    _notify(APP_NAME, f"Failed to download {model_name}: {msg}")
                    return {
                        "success": False,
                        "error": msg,
                        "reason": reason,
                        "model": model_name,
                    }
                log.info("[SERVICE] Parakeet download complete")
                _push_progress(100, "Parakeet download complete")
                # NEW-PERF-004: invalidate the tray models submenu cache.
                try:
                    from voice_typer.server.tray_models import (
                        invalidate_model_availability_cache,
                    )

                    invalidate_model_availability_cache()
                except Exception:
                    log.debug(
                        "[SERVICE] failed to invalidate tray model cache",
                        exc_info=True,
                    )
                _notify(APP_NAME, "Parakeet model downloaded successfully")
                return {"success": True, "model": model_name}
            else:
                log.warning("[SERVICE] Unknown model requested for download: '%s'", model_name)
                return {"success": False, "error": f"Unknown model: {model_name}"}
        except Exception as exc:
            log.error("download_model failed for %s: %s", model_name, exc)
            # NEW-PRIV-011: clear cancel event on failure too.
            # HIGH-8 / SERVICE-1: unregister the per-download Event
            # from the dict (no-op if download_id is None, e.g. the
            # failure happened before _register_download was called).
            if download_id is not None:
                self._unregister_download(download_id)
            # NEW-PAUSE-001: clear the pause flag on failure too.
            try:
                from voice_typer.server.asr_setup import clear_download_pause_state

                clear_download_pause_state()
            except Exception:
                log.debug("[SERVICE] could not clear pause flag on failure", exc_info=True)
            _push_progress(0, f"Download failed: {exc}")
            _notify(APP_NAME, f"Failed to download {model_name}: {exc}")
            return {"success": False, "error": str(exc)}
