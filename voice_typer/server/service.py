"""VoiceTyperService: service layer between IPC and domain logic.

ARCH-005: previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.
"""

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


class VoiceTyperService:
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.
    """

    def __init__(self, app) -> None:
        self._app = app

    # ── Status ──────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return the current app state plus audio-quality telemetry.

        ERR-021: previously returned only the tray state string. The
        xrun counter was tracked in the recorder but never reached the
        IPC layer, so the UI couldn't warn the user of degraded audio.
        We now return a dict with ``status``, ``xruns_since_start``,
        and other useful fields.
        """
        app = self._app
        status_str = app.tray.state.value
        # Best-effort: xruns counter exists on the Recorder instance.
        xruns = 0
        try:
            xruns = int(getattr(app.recorder, "_xruns", 0) or 0)
        except Exception:
            log.debug("[SERVICE] could not read xrun counter", exc_info=True)
        return {
            "status": status_str,
            "xruns_since_start": xruns,
        }

    # ── Dictation ───────────────────────────────────────────────

    def toggle_dictation(self) -> None:
        """Start or stop dictation."""
        self._app.toggle_dictation()

    def undo_last(self) -> None:
        """Undo the last transcription via backspace keystrokes."""
        self._app.undo_last()

    def repaste_last(self) -> None:
        """Re-paste the last transcription."""
        self._app.repaste_last()

    # ── Config ──────────────────────────────────────────────────

    def get_config(self) -> dict:
        """Return the sanitized config (API keys redacted)."""
        from voice_typer.server.ipc_server import _sanitize_config_for_ipc
        return _sanitize_config_for_ipc(self._app.config)

    def get_defaults(self) -> dict:
        """Return default config values (sanitized)."""
        from voice_typer.server.config import Config
        from voice_typer.server.ipc_server import _sanitize_config_for_ipc
        return _sanitize_config_for_ipc(Config())

    def set_config(self, updates: dict) -> tuple[dict, list]:
        """Validate and apply config updates. Returns (validated, errors)."""
        from voice_typer.server.config import validate_config_update
        return validate_config_update(updates)

    def save_config(self) -> bool:
        """Persist config to disk."""
        return self._app.config.save()

    # ── History ─────────────────────────────────────────────────

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return recent transcriptions.

        ERR-013: raise_on_error=True so the IPC layer can distinguish
        "empty result" from "operation failed" and surface an error
        to the renderer.
        """
        return self._app.history_db.get_recent(limit, offset, raise_on_error=True)

    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Search transcriptions by text.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.search(query, limit, offset, raise_on_error=True)

    def get_today_stats(self) -> dict:
        """Return today's transcription statistics.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_today_stats(raise_on_error=True)

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.delete(rec_id, raise_on_error=True)

    def clear_history(self) -> bool:
        """Clear all history records.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.clear_all(raise_on_error=True)

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.toggle_favorite(rec_id, raise_on_error=True)

    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return favorited transcriptions.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_favorites(limit, offset, raise_on_error=True)

    # ── Microphones ─────────────────────────────────────────────

    def get_microphones(self) -> list[dict]:
        """Return available microphones."""
        return self._app._microphones

    # ── Lifecycle ───────────────────────────────────────────────

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()

    # ── Templates (#6) ─────────────────────────────────────────

    def get_templates(self) -> list[dict]:
        """Return saved templates from config."""
        templates = getattr(self._app.config, 'templates_data', None)
        return templates if isinstance(templates, list) else []

    def save_templates(self, templates: list[dict]) -> bool:
        """Save templates to config and persist to disk."""
        self._app.config.templates_data = templates
        return self._app.config.save()

    # ── Volume / Model status (ARCH-005) ────────────────────────

    def get_volume_backend_status(self) -> dict:
        """Return the volume ducking backend status."""
        ducker = getattr(self._app, "_volume_ducker", None)
        if ducker is None:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
            }
        try:
            # Trigger initialize() so the backend name reflects
            # the actual platform backend (not "disabled"
            # merely because nothing has ducked yet).
            try:
                ducker.initialize()
            except Exception:
                log.debug("volume_ducker.initialize failed", exc_info=True)
            return {
                "available": bool(ducker.is_available),
                "name": ducker.backend_name,
                "supports_per_session": bool(ducker.supports_per_session),
                "backend": type(ducker).__name__,
            }
        except Exception as exc:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
                "reason": str(exc),
            }

    def get_model_status(self) -> dict:
        """Return the model download/dependency status for each ASR backend."""
        import os
        from voice_typer.server.config import _config_dir

        config = self._app.config
        status = {}

        # Whisper models
        for model_size in ("tiny.en", "small.en", "medium.en", "large-v3"):
            cache_dir = os.path.expanduser(
                os.path.join("~", ".cache", "huggingface", "hub")
            )
            # Check if model exists in HF cache
            downloaded = os.path.isdir(cache_dir) and any(
                model_size.replace(".", "-") in d.lower()
                for d in os.listdir(cache_dir)
                if os.path.isdir(os.path.join(cache_dir, d))
            )
            status[model_size] = {
                "downloaded": downloaded,
                "deps_ok": True,  # faster-whisper is always available
            }

        # Qwen model
        qwen_path = getattr(config, "qwen_model_path", None)
        status["qwen"] = {
            "downloaded": bool(qwen_path and os.path.isdir(qwen_path)),
            "deps_ok": self._check_qwen_deps(),
        }

        # Parakeet model
        parakeet_path = getattr(config, "parakeet_model_path", None)
        status["parakeet"] = {
            "downloaded": bool(parakeet_path and os.path.isdir(parakeet_path)),
            "deps_ok": self._check_parakeet_deps(),
        }

        return status

    def delete_model(self, model_name: str) -> dict:
        """Delete a downloaded model from the HuggingFace cache.

        NEW-UX-005: previously the Models page only removed the model
        from the UI list without actually deleting the files.  A 1.5 GB
        model left on disk is a waste of space and confuses users who
        think they deleted it.  We now actually delete the cached files.

        Returns ``{"success": bool, "message": str}``.
        """
        import shutil
        from voice_typer.server.config import _config_dir

        cache_dir = _config_dir() / "huggingface" / "hub"

        # Map model name to HuggingFace repo ID.
        repo_map = {
            "tiny.en": "Systran/faster-whisper-tiny.en",
            "small.en": "Systran/faster-whisper-small.en",
            "medium.en": "Systran/faster-whisper-medium.en",
            "parakeet": "nvidia/parakeet-tdt-0.6b-v3",
        }
        repo_id = repo_map.get(model_name)
        if not repo_id:
            return {"success": False, "message": f"Unknown model: {model_name}"}

        # Don't allow deleting the active model.
        current_backend = getattr(self._app.config, "asr_backend", "whisper")
        current_model = getattr(self._app.config, "model_size", "tiny.en")
        is_active = (
            (model_name == current_model and current_backend == "whisper")
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
            # Invalidate the tray models submenu cache so the next
            # right-click reflects the deletion.
            try:
                from voice_typer.server.tray_models import (
                    invalidate_model_availability_cache,
                )
                invalidate_model_availability_cache()
            except Exception:
                pass
            return {
                "success": True,
                "message": f"Deleted model '{model_name}' ({repo_id}).",
            }
        except Exception as exc:
            log.warning("[SERVICE] delete_model failed: %s", exc)
            return {"success": False, "message": str(exc)}

    def test_llm_connection(self) -> dict:
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
            return {"success": False, "message": str(exc)}

    def _check_qwen_deps(self) -> bool:
        """Check if qwen_asr package is importable."""
        try:
            import importlib
            importlib.import_module("qwen_asr")
            return True
        except ImportError:
            return False

    def _check_parakeet_deps(self) -> bool:
        """Check if nemo_toolkit is importable."""
        try:
            import importlib
            importlib.import_module("nemo_toolkit")
            return True
        except ImportError:
            return False

    # ── Vocabulary (ARCH-005) ───────────────────────────────────

    def get_vocabulary(self) -> dict:
        """Return the current vocabulary entries.

        ERR-IPC-005 (fix): previously called ``vm.list_entries()`` which
        does not exist on VocabularyManager, causing a 500 error on
        every Vocabulary page load. The renderer's ``VocabularyData``
        type expects a dict keyed by category name (misspellings,
        technical_terms, names, products, phrase_corrections,
        extra_word_patterns) — same shape as ``VocabularyManager.get_all()``.
        We now delegate to ``get_all()`` and add the user-file path so
        the renderer can show "edited" indicators.
        """
        from voice_typer.server.vocabulary import VocabularyManager
        vm = VocabularyManager(config_dir=self._app.config.config_dir)
        data = vm.get_all()
        # Attach the user-file path so the renderer can surface it in
        # the UI (e.g. "edit the file directly at ...").
        data["_user_file"] = str(vm._user_path) if hasattr(vm, "_user_path") else None
        return data

    def save_vocabulary(self, entries: list[dict]) -> dict:
        """Save vocabulary entries and return result."""
        from voice_typer.server.vocabulary import VocabularyManager, VocabularyEntry
        vm = VocabularyManager(config_dir=self._app.config.config_dir)
        try:
            vm.set_entries([
                VocabularyEntry(word=e["word"], replacement=e["replacement"])
                for e in entries
            ])
            return {"success": True, "count": len(entries)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def save_vocabulary_with_diff(self, data: dict) -> dict:
        """Save vocabulary with bundled diff logic.

        ARCH-005: Moved from ipc_server.py.  Only saves user customizations
        (diff against bundled defaults) to the user file, preventing
        duplicate entries on next load.
        """
        from voice_typer.server.vocabulary import VocabularyManager, CATEGORIES, VOCAB_FILENAME
        from voice_typer.server.config import _config_dir
        import json

        mgr = VocabularyManager()
        bundled = mgr._load_bundled()

        user_only: dict[str, object] = {}
        for cat in CATEGORIES:
            incoming = (data or {}).get(cat)
            bundled_cat = bundled.get(cat)

            if cat in ("misspellings", "technical_terms", "names", "products"):
                if isinstance(incoming, dict):
                    bd = bundled_cat if isinstance(bundled_cat, dict) else {}
                    diff = {k: v for k, v in incoming.items() if bd.get(k) != v}
                    if diff:
                        user_only[cat] = diff
            elif cat in ("phrase_corrections", "extra_word_patterns"):
                if isinstance(incoming, list):
                    bs: set[tuple[str, str]] = set()
                    if isinstance(bundled_cat, list):
                        for item in bundled_cat:
                            if isinstance(item, (list, tuple)) and len(item) >= 2:
                                bs.add((item[0], item[1]))
                    diff = [
                        item for item in incoming
                        if isinstance(item, (list, tuple)) and len(item) >= 2
                        and (item[0], item[1]) not in bs
                    ]
                    if diff:
                        user_only[cat] = diff

        # Write only user customizations to the user file
        user_path = _config_dir() / VOCAB_FILENAME
        user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = user_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(user_only, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(user_path)

        return {"imported_categories": len(user_only)}

    # ── Config side effects (ARCH-005) ──────────────────────────

    def apply_config_side_effects(self, updates: dict) -> None:
        """Apply side effects after config changes.

        ARCH-005: Centralizes the post-config-update hooks that were
        previously scattered across ipc_server.py.
        """
        app = self._app
        config = app.config

        # Sync prewarm task if fast_startup changed
        if "fast_startup" in updates:
            try:
                app._sync_prewarm_task()
            except Exception as e:
                log.warning("Failed to sync prewarm task: %s", e)

        # Sync autostart if autostart setting changed
        if "autostart" in updates:
            try:
                app._sync_autostart()
            except Exception as e:
                log.warning("Failed to sync autostart: %s", e)

        # Register/unregister ESC hotkey
        if "esc_cancel_enabled" in updates:
            try:
                if updates["esc_cancel_enabled"]:
                    app._register_esc_hotkey()
                else:
                    app._unregister_esc_hotkey()
            except Exception as e:
                log.warning("Failed to sync ESC hotkey: %s", e)

        # Register/unregister repaste hotkey
        if "repaste_hotkey" in updates or "repaste_enabled" in updates:
            try:
                app._register_repaste_hotkey()
            except Exception as e:
                log.warning("Failed to sync repaste hotkey: %s", e)

    # ── Onboarding (#8) ─────────────────────────────────────────────

    def onboarding_is_first_run(self) -> dict:
        """Check if this is the first run (onboarding needed)."""
        from voice_typer.server.onboarding import OnboardingController
        ctrl = OnboardingController()
        return {"is_first_run": ctrl.is_first_run()}

    def onboarding_start(self) -> dict:
        """Start the onboarding wizard. Returns step info."""
        from voice_typer.server.onboarding import OnboardingController
        ctrl = OnboardingController()
        self._onboarding = ctrl
        return {
            "step": ctrl.current_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_get_step(self) -> dict:
        """Get current onboarding step info."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        return {
            "step": ctrl.current_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_next_step(self) -> dict:
        """Advance to next onboarding step."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        new_step = ctrl.next_step()
        return {
            "step": new_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_prev_step(self) -> dict:
        """Go back to previous onboarding step."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        new_step = ctrl.prev_step()
        return {
            "step": new_step,
            "total_steps": ctrl.total_steps,
            "step_name": ctrl.step_name,
        }

    def onboarding_set_microphone(self, mic_id: str | None) -> dict:
        """Set the microphone choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_microphone(mic_id)
        return {"ok": True}

    def onboarding_set_hotkey(self, hotkey: str) -> dict:
        """Set the hotkey choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_hotkey(hotkey)
        return {"ok": True}

    def onboarding_set_model(self, model: str) -> dict:
        """Set the model choice in the onboarding wizard."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.set_model(model)
        return {"ok": True}

    def onboarding_skip(self) -> dict:
        """Skip onboarding entirely."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        ctrl.skip()
        return {"ok": True}

    def onboarding_apply(self) -> dict:
        """Apply onboarding settings and mark complete."""
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        try:
            ctrl.apply_settings(self._app.config)
            self._app.config.onboarding_completed = True
            self._app.config.save()
            return {"ok": True}
        except Exception as exc:
            return {"error": str(exc)}

    def onboarding_get_microphones(self) -> dict:
        """Get available microphones for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController
        ctrl = getattr(self, "_onboarding", OnboardingController())
        return {"microphones": ctrl.get_microphones()}

    def onboarding_get_model_options(self) -> dict:
        """Get model options for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController
        return {"models": OnboardingController.MODEL_OPTIONS}

    def onboarding_get_hotkey_presets(self) -> dict:
        """Get hotkey presets for the onboarding wizard."""
        from voice_typer.server.onboarding import OnboardingController
        return {"presets": OnboardingController.HOTKEY_PRESETS}

    # ── Download model (UX-005) ─────────────────────────────────────

    def download_model(self, model_name: str) -> dict:
        """Download a model weight file via HuggingFace.

        UX-005: Downloads the specified model (tiny.en, small.en, medium.en,
        large-v3, qwen, parakeet) to the local HF cache. Pushes
        ``download_progress`` events to the renderer so the Models page
        can update its progress bar and status text in real time, and
        fires a tray notification on completion / failure.
        Returns a result dict with success status.
        """
        import os
        # UX-005: helper to push progress events to the renderer.
        from voice_typer.server.ipc_server import _push_event_now

        def _push_progress(progress: int, status: str) -> None:
            """progress is 0-100; status is a human-readable string."""
            _push_event_now({
                "type": "download_progress",
                "data": {
                    "model": model_name,
                    "progress": max(0, min(100, int(progress))),
                    "status": status,
                },
            })

        def _notify(title: str, message: str) -> None:
            try:
                self._app.tray.notify(title, message)
            except Exception:
                log.debug("[SERVICE] tray notify failed", exc_info=True)

        try:
            if model_name in ("tiny.en", "small.en", "medium.en", "large-v3"):
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
                    repo_id = f"Systran/faster-whisper-{model_name}"
                    cache_dir = _config_dir() / "huggingface" / "hub"
                    _push_progress(5, f"Checking cache for {model_name}...")
                    # Try local-only first; if cached, skip the polling.
                    try:
                        snapshot_download(repo_id=repo_id, local_files_only=True)
                        _push_progress(100, f"{model_name} already cached")
                    except Exception:
                        _push_progress(10, f"Downloading {model_name} from HuggingFace...")
                        # Start the download in a thread so we can poll
                        # the cache directory size while it runs.
                        import threading
                        download_err: list = []
                        def _do_download():
                            try:
                                snapshot_download(
                                    repo_id=repo_id,
                                    resume_download=True,
                                    cache_dir=str(cache_dir),
                                )
                            except Exception as e:
                                download_err.append(e)
                        t = threading.Thread(target=_do_download, daemon=True)
                        t.start()
                        # Poll cache size until download thread exits.
                        # Approximate total sizes (MB) for progress estimation.
                        size_targets = {
                            "tiny.en": 75, "small.en": 466,
                            "medium.en": 1500, "large-v3": 3000,
                        }
                        target_mb = size_targets.get(model_name, 500)
                        import time
                        while t.is_alive():
                            t.join(timeout=1.0)
                            try:
                                if cache_dir.exists():
                                    total = sum(
                                        f.stat().st_size
                                        for f in cache_dir.rglob("*")
                                        if f.is_file()
                                    ) // (1024 * 1024)
                                    pct = min(95, int(10 + (total / target_mb) * 85))
                                    _push_progress(
                                        pct,
                                        f"Downloading {model_name}: {total} MB / ~{target_mb} MB",
                                    )
                            except Exception:
                                pass
                        if download_err:
                            raise download_err[0]
                        _push_progress(100, f"{model_name} download complete")
                except ImportError:
                    log.debug("[SERVICE] huggingface_hub not available, falling back to engine.load()")

                # Now load + unload the engine to verify the download
                _push_progress(100, f"Verifying {model_name}...")
                from voice_typer.server.transcription import TranscriptionEngine
                engine = TranscriptionEngine(model_size=model_name, device="cpu")
                engine.load()
                engine.unload()
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
                _notify("Voice Typer", f"Model '{model_name}' downloaded successfully")
                return {"success": True, "model": model_name}
            elif model_name == "qwen":
                qwen_path = getattr(self._app.config, "qwen_model_path", None)
                if qwen_path and os.path.isdir(qwen_path):
                    _push_progress(100, "Qwen model already cached")
                    return {"success": True, "model": model_name, "message": "Qwen model already cached"}
                _notify("Voice Typer", "Qwen model path not configured")
                return {"success": False, "error": "Qwen model path not configured. Set qwen_model_path in Settings."}
            elif model_name == "parakeet":
                _push_progress(0, "Starting Parakeet download (~2.5 GB)...")
                from voice_typer.server.asr_setup import download_parakeet_weights
                # asr_setup.download_parakeet_weights() doesn't expose
                # progress; we emit start/finish events.
                _push_progress(50, "Downloading Parakeet weights from HuggingFace...")
                download_parakeet_weights()
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
                _notify("Voice Typer", "Parakeet model downloaded successfully")
                return {"success": True, "model": model_name}
            else:
                return {"success": False, "error": f"Unknown model: {model_name}"}
        except Exception as exc:
            log.error("download_model failed for %s: %s", model_name, exc)
            _push_progress(0, f"Download failed: {exc}")
            _notify("Voice Typer", f"Failed to download {model_name}: {exc}")
            return {"success": False, "error": str(exc)}