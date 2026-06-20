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

    def get_status(self) -> str:
        """Return the current app state as a string."""
        return self._app.tray.state.value

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
        """Return recent transcriptions."""
        return self._app.history_db.get_recent(limit, offset)

    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Search transcriptions by text."""
        return self._app.history_db.search(query, limit, offset)

    def get_today_stats(self) -> dict:
        """Return today's transcription statistics."""
        return self._app.history_db.get_today_stats()

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID."""
        return self._app.history_db.delete(rec_id)

    def clear_history(self) -> bool:
        """Clear all history records."""
        return self._app.history_db.clear_all()

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record."""
        return self._app.history_db.toggle_favorite(rec_id)

    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return favorited transcriptions."""
        return self._app.history_db.get_favorites(limit, offset)

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
        """Return the current vocabulary entries."""
        from voice_typer.server.vocabulary import VocabularyManager
        vm = VocabularyManager(config_dir=self._app.config.config_dir)
        entries = vm.list_entries()
        return {
            "entries": [{"word": e.word, "replacement": e.replacement} for e in entries],
            "file": vm.path if hasattr(vm, "path") else None,
        }

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
        large-v3, qwen, parakeet) to the local HF cache.
        Returns a result dict with success status.
        """
        import os
        try:
            if model_name in ("tiny.en", "small.en", "medium.en", "large-v3"):
                from voice_typer.server.transcription import TranscriptionEngine
                engine = TranscriptionEngine(model_size=model_name, device="cpu")
                engine.load()
                engine.unload()
                return {"success": True, "model": model_name}
            elif model_name == "qwen":
                qwen_path = getattr(self._app.config, "qwen_model_path", None)
                if qwen_path and os.path.isdir(qwen_path):
                    return {"success": True, "model": model_name, "message": "Qwen model already cached"}
                return {"success": False, "error": "Qwen model path not configured. Set qwen_model_path in Settings."}
            elif model_name == "parakeet":
                from voice_typer.server.asr_setup import download_parakeet_weights
                download_parakeet_weights()
                return {"success": True, "model": model_name}
            else:
                return {"success": False, "error": f"Unknown model: {model_name}"}
        except Exception as exc:
            log.error("download_model failed for %s: %s", model_name, exc)
            return {"success": False, "error": str(exc)}