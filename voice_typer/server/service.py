"""VoiceTyperService: service layer between IPC and domain logic.

ARCH-005: previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.
"""

import contextlib
import logging
from typing import Any

from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)


def _apply_audio_preset(preset: str) -> dict:
    """ADR 0007: Map an audio preset name to individual filter settings.

    Delegates to :mod:`voice_typer.server.audio_presets` (single source
    of truth). Presets:
        "auto"        — all filters ON, RNNoise (best for 90% of users)
        "studio"      — minimal processing (quiet room, good mic)
        "noisy_room"  — aggressive, DeepFilterNet
        "off"         — all filters OFF
        "custom"      — no automatic changes (user controls each toggle)

    Legacy preset names "recommended" and "none" are accepted for
    backward compat (mapped to "auto" and "off" respectively).

    Returns:
        dict of noise_filter_* settings to apply.
    """
    from voice_typer.server.audio_presets import (
        PRESET_AUTO,
        PRESET_OFF,
        get_preset_filters,
    )

    # Map legacy preset names
    legacy_map = {"recommended": PRESET_AUTO, "none": PRESET_OFF}
    normalized = legacy_map.get(preset, preset)
    return get_preset_filters(normalized)


def _find_symlink_in_tree(root):
    """RW-5: return the path of the first symlink found under ``root``,
    or ``None`` if there are none.

    Used by :meth:`VoiceTyperService.import_model` to reject poisoned
    model dirs that contain symlinks (e.g. a symlink to
    ``~/.ssh/id_rsa``).  HuggingFace hub cache dirs never legitimately
    contain symlinks at the *source* side — the hub uses symlinks
    inside its own cache (``snapshots/<rev>/...`` → ``blobs/<hash>``),
    but a user-supplied import directory is expected to contain real
    files only.

    ``os.walk`` with the default ``followlinks=False`` does NOT descend
    into symlinked directories, but it DOES include them in
    ``dirnames`` — so both symlinked files and symlinked directories
    are detected by this check.
    """
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + list(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                return full
    return None


class VoiceTyperService:
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.
    """

    def __init__(self, app) -> None:
        self._app = app
        # NEW-PRIV-011: cancellation event for in-progress model downloads.
        self._download_cancel_event: Any = None

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
        # NEW-UX-038: read the active engine's loaded_via property.
        loaded_via = ""
        try:
            active = app.models._registry.get_active() if hasattr(app, "models") and app.models else None
            if active is not None and hasattr(active, "loaded_via"):
                loaded_via = str(active.loaded_via)
        except Exception:
            log.debug("[SERVICE] could not read loaded_via", exc_info=True)
        return {
            "status": status_str,
            "xruns_since_start": xruns,
            "loaded_via": loaded_via,
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

    # ── Force cancel transcription (PR-2 Finding #3) ─────────────

    def force_cancel_transcription(self) -> dict:
        """Force-cancel a stuck transcription.

        PR-2 Finding #3: invokes ``_force_recover_from_stuck_transcription``
        with ``force=True`` so the busy flag and tray state are reset
        even if the transcription thread is still alive.  This gives
        the user a manual escape hatch when the 3×90s=4.5min auto-
        recovery is too slow.

        Returns ``{"success": bool, "message": str}``.
        """
        try:
            self._app.recording._force_recover_from_stuck_transcription(force=True)
            return {"success": True, "message": "Transcription cancelled."}
        except Exception as exc:
            log.warning("[SERVICE] force_cancel_transcription failed: %s", exc)
            return {"success": False, "message": str(exc)}

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

    def restore_history(self, record: dict) -> int:
        """Re-insert a previously-deleted history record.

        NEW-UX-004: supports the Undo-delete toast in the renderer.
        Returns the new row id (or -1 on failure — the renderer
        surfaces a "Failed to restore" toast in that case).
        """
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        # Require at least a non-empty text field — restoring an empty
        # record would silently succeed with a meaningless row.
        if not str(record.get("text", "")).strip():
            raise ValueError("record.text must be a non-empty string")
        return self._app.history_db.restore(record, raise_on_error=True)

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

    # AUDIO-MIC: refresh the microphone list by re-querying PortAudio.
    def refresh_microphones(self) -> list[dict]:
        """AUDIO-MIC: Re-query PortAudio for available microphones.

        Called when the user clicks "Refresh Microphones" in the UI
        after plugging in a new USB/BT device. Updates the cached list
        and the tray menu.
        """
        from voice_typer.server.server_platform import list_microphones
        try:
            mics = list_microphones()
            self._app._microphones = mics
            with contextlib.suppress(Exception):
                self._app.tray.set_microphones(mics)
            return mics
        except Exception as e:
            log.error("[SERVICE] refresh_microphones failed: %s", e)
            return self._app._microphones

    # AUDIO-RMS: IPC endpoint for real-time RMS level.
    def get_rms_level(self) -> dict:
        """AUDIO-RMS: Return the current RMS level from the recorder.

        Returns dict with 'rms' (float, 0.0 if not recording) and
        'recording' (bool).
        """
        try:
            recorder = getattr(self._app, "recorder", None)
            if recorder is None:
                return {"rms": 0.0, "recording": False}
            return {
                "rms": recorder.last_rms,
                "recording": recorder.recording,
            }
        except Exception as e:
            log.debug("[SERVICE] get_rms_level failed: %s", e)
            return {"rms": 0.0, "recording": False}

    def microphone_test_start(self, mic_id: str | None = None,
                               duration: float = 10.0,
                               filters: dict | None = None) -> dict:
        """Start a microphone test recording.

        Args:
            mic_id: Device index string or None for system default.
            duration: Recording duration in seconds (default 10).
            filters: Optional dict of audio enhancement filter overrides.

        Returns:
            dict with success, message, duration, sample_rate.
        """
        from voice_typer.server.microphone_test import start_test
        return start_test(mic_id=mic_id, duration=duration, filters=filters)

    def microphone_test_stop(self) -> dict:
        """Stop the microphone test and return audio data as base64 WAV.

        Also attempts to auto-transcribe the recorded audio (best-effort)
        so the frontend can show "You said: ..." with recognition confidence.

        Returns:
            dict with success, audio_base64, raw_audio_base64, duration_ms,
            sample_rate, quality, message, and optionally transcription and
            transcription_confidence.
        """
        from voice_typer.server.microphone_test import stop_test
        result = stop_test()

        # Best-effort auto-transcription of the test recording
        # Uses the already-loaded active engine (avoids loading a new
        # engine from scratch which can take 30+ seconds).
        if result.get("success") and result.get("audio_base64"):
            try:
                import base64
                import io
                import wave

                import numpy as np

                wav_bytes = base64.b64decode(result["audio_base64"])

                # Decode WAV to float32
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                    audio_f32 = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767

                # Use the app's already-loaded active engine
                models = getattr(self._app, "models", None)
                if models is not None:
                    engine = models.active_transcriber()
                    if engine is not None and getattr(engine, "is_loaded", False):
                        try:
                            transcription = engine.transcribe(audio_f32)
                            text = str(transcription) if transcription else ""
                            if text.strip():
                                result["transcription"] = text
                                result["transcription_confidence"] = None
                                log.info(
                                    "[SERVICE] Test transcription: %.60s...", text,
                                )
                            else:
                                log.debug("[SERVICE] Test transcription: no speech detected")
                        except Exception as tx_err:
                            log.debug("[SERVICE] Test transcription failed: %s", tx_err)
                    else:
                        log.debug("[SERVICE] Active engine not loaded — skipping transcription")
            except Exception as transcribe_err:
                log.debug("[SERVICE] Test transcription setup failed: %s", transcribe_err)

        return result

    def microphone_test_cancel(self) -> dict:
        """Cancel a running microphone test without returning audio."""
        from voice_typer.server.microphone_test import cancel_test
        return cancel_test()

    def microphone_test_status(self) -> dict:
        """Check if a microphone test is currently active."""
        from voice_typer.server.microphone_test import is_test_active
        return {"active": is_test_active()}

    def microphone_test_get_level(self) -> dict:
        """Get the current real-time audio level.

        Both the test and the level monitor use the same single PortAudio
        stream (see :mod:`level_monitor`), so there is a single source of
        truth.  The level is always from the level monitor.

        Returns dict with level (0-1), peak (0-1), and active (bool).
        """
        from voice_typer.server.level_monitor import get_level
        return get_level()

    def level_monitor_start(self, mic_id: str | None = None) -> dict:
        """Start continuous audio level monitoring.

        Also initialises the audio processor for the live level bar
        from the current noise-filter config so the bar reflects
        enabled filters immediately.

        Args:
            mic_id: Device index string or None for system default.

        Returns:
            dict with success, message, sample_rate.
        """
        from voice_typer.server.level_monitor import (
            start_monitoring,
            update_level_processor,
        )
        result = start_monitoring(mic_id=mic_id)
        # Seed the level processor from the current config
        try:
            cfg = self._app.config
            update_level_processor({
                "noise_filter_enabled": getattr(cfg, "noise_filter_enabled", True),
                "noise_filter_highpass": getattr(cfg, "noise_filter_highpass", True),
                "noise_filter_gate": getattr(cfg, "noise_filter_gate", True),
                "noise_filter_rnnoise": getattr(cfg, "noise_filter_rnnoise", False),
                "noise_filter_post_capture": getattr(cfg, "noise_filter_post_capture", True),
            })
        except Exception:
            pass
        return result

    def level_monitor_stop(self) -> dict:
        """Stop continuous audio level monitoring."""
        from voice_typer.server.level_monitor import stop_monitoring
        return stop_monitoring()

    def level_monitor_status(self) -> dict:
        """Check if continuous level monitoring is active."""
        from voice_typer.server.level_monitor import is_monitoring
        return {"active": is_monitoring()}

    # ── Lifecycle ───────────────────────────────────────────────

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()

    # ── Templates (#6, NEW-UX-008) ──────────────────────────────
    #
    # NEW-UX-008: previously this method read from a non-existent
    # ``config.templates_data`` attribute, so it always returned an
    # empty list — and ``save_templates`` set the attribute on the
    # dataclass instance but never persisted it (``dataclasses.asdict``
    # only serializes declared fields, so the dynamic attribute was
    # silently dropped on save).  As a result the renderer kept
    # templates ONLY in localStorage, and they were lost on reinstall
    # or app-data reset.
    #
    # The fix delegates to the existing ``TemplateManager`` which
    # already persists to ``voice-typer-templates.json`` in the
    # Python config dir (``~/.voice-typer`` on POSIX,
    # ``%APPDATA%\voice-typer`` on Windows).  This file survives
    # Electron userData resets and reinstalls.

    def _template_manager(self):
        """Lazily obtain (or create) the app's TemplateManager."""
        app = self._app
        tm = getattr(app, "_template_manager", None)
        if tm is None:
            from voice_typer.server.templates import TemplateManager
            tm = TemplateManager()
            app._template_manager = tm
        return tm

    def get_templates(self) -> list[dict]:
        """Return saved templates from the persistent template store.

        Returns a list of dicts with keys: trigger, output, match_mode,
        created_at (optional).
        """
        try:
            tm = self._template_manager()
            # Each template dict from TemplateManager has the shape
            # {trigger, output, match_mode, created_at?}.  Strip any
            # internal fields and return a plain list for IPC.
            return [
                {
                    "trigger": t.get("trigger", ""),
                    "output": t.get("output", ""),
                    "match_mode": t.get("match_mode", "exact"),
                }
                for t in tm.templates
            ]
        except Exception as exc:
            log.error("[SERVICE] get_templates failed: %s", exc, exc_info=True)
            return []

    def save_templates(self, templates: list[dict]) -> bool:
        """Replace all templates in the persistent store.

        NEW-UX-008: full-replace semantics — the renderer sends the
        complete list after each add/edit/delete, and we persist the
        whole list atomically via TemplateManager._save (which uses
        _secure_atomic_write — O_NOFOLLOW on POSIX, temp+rename).
        """
        try:
            tm = self._template_manager()
            # Normalize and replace.  We don't call tm.add/update/delete
            # individually because the renderer already has the full
            # list; doing N writes would be N× disk I/O for one user
            # action.  Direct list replacement is atomic and fast.
            normalized: list[dict] = []
            for t in templates or []:
                if not isinstance(t, dict):
                    continue
                trigger = str(t.get("trigger", "")).strip()
                output = str(t.get("output", ""))
                match_mode = str(t.get("match_mode", "exact"))
                if not trigger or not output:
                    continue
                if match_mode not in ("exact", "contains"):
                    match_mode = "exact"
                normalized.append({
                    "trigger": trigger,
                    "output": output,
                    "match_mode": match_mode,
                })
            # Use the manager's internal list + _save so the on-disk
            # format matches what TemplateManager._load expects (a
            # dict with a "templates" key).
            tm._templates = normalized
            tm._save()
            log.info("[SERVICE] Saved %d templates", len(normalized))
            return True
        except Exception as exc:
            log.error("[SERVICE] save_templates failed: %s", exc, exc_info=True)
            return False

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

        # Whisper models — check ALL models from the registry, using
        # the same cache directory that download_model writes to.
        from voice_typer.server.model_registry import MODEL_REGISTRY, get_model_metadata
        cache_dir = os.path.join(str(_config_dir()), "huggingface", "hub")
        for meta in MODEL_REGISTRY.values():
            if meta.backend not in ("whisper", "distil-whisper"):
                continue
            repo_dir_name = f"models--{meta.repo_id.replace('/', '--')}"
            downloaded = os.path.isdir(cache_dir) and os.path.isdir(
                os.path.join(cache_dir, repo_dir_name)
            )
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
            qwen_in_cache = os.path.isdir(cache_dir) and os.path.isdir(
                os.path.join(cache_dir, qwen_repo_dir)
            )
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
            parakeet_in_cache = os.path.isdir(cache_dir) and os.path.isdir(
                os.path.join(cache_dir, parakeet_repo_dir)
            )
        status["parakeet"] = {
            "downloaded": bool(parakeet_path and os.path.isdir(parakeet_path)) or parakeet_in_cache,
            "deps_ok": self._check_parakeet_deps(),
        }

        return status

    def delete_model(self, model_name: str) -> dict:
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
        meta = get_model_metadata(model_name)
        if meta is not None and meta.backend in ("whisper", "distil-whisper"):
            repo_id = meta.repo_id
        elif model_name == "parakeet":
            repo_id = "nvidia/parakeet-tdt-0.6b-v3"
        elif model_name == "qwen":
            # Qwen doesn't use the HF hub cache layout; handled below.
            repo_id = None
        else:
            repo_id = None

        if not repo_id:
            return {"success": False, "message": f"Unknown model: {model_name}"}

        # Don't allow deleting the active model.
        current_backend = getattr(self._app.config, "asr_backend", "whisper")
        current_model = getattr(self._app.config, "model_size", "tiny.en")
        is_active = (
            (model_name == current_model
             and current_backend in ("whisper", "distil-whisper"))
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
                "[SERVICE] Model '%s' deleted (repo=%s)", model_name, repo_id,
            )
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
        """Check if the Parakeet engine's key runtime dependency is importable.

        The Parakeet engine (``parakeet_engine.py``) defers its heavy imports
        (``torch``, ``transformers``, ``psutil``) inside ``_ensure_imports``.
        The most critical of these is ``torch`` — without it the engine cannot
        initialise.  Previously this method checked for ``nemo_toolkit`` which
        is not a dependency of the Parakeet engine in this codebase, causing
        ``deps_ok`` to always be ``False`` and blocking the "Select" button
        in the Models page even when the user was actively using Parakeet.
        """
        try:
            import importlib
            importlib.import_module("torch")
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

    def save_vocabulary_with_diff(self, data: dict) -> dict:
        """Save vocabulary with bundled diff logic.

        ARCH-005: Moved from ipc_server.py.  Only saves user customizations
        (diff against bundled defaults) to the user file, preventing
        duplicate entries on next load.
        """
        import json

        from voice_typer.server.config import _config_dir
        from voice_typer.server.vocabulary import CATEGORIES, VOCAB_FILENAME, VocabularyManager

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
            elif cat in ("phrase_corrections", "extra_word_patterns") and isinstance(incoming, list):
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
        # SEC-003: Use _secure_atomic_write to ensure 0o600 permissions
        user_path = _config_dir() / VOCAB_FILENAME
        user_path.parent.mkdir(parents=True, exist_ok=True)
        from voice_typer.server.config import _secure_atomic_write
        _secure_atomic_write(
            user_path,
            json.dumps(user_only, indent=2, ensure_ascii=False),
        )

        return {"imported_categories": len(user_only)}

    # ── Config side effects (ARCH-005) ──────────────────────────

    def apply_config_side_effects(self, updates: dict) -> None:
        """Apply side effects after config changes.

        ARCH-005: Centralizes the post-config-update hooks that were
        previously scattered across ipc_server.py.
        """
        app = self._app
        config = app.config

        # Sync autostart if autostart setting changed
        if "autostart" in updates:
            try:
                # RW-9 Phase 2: call controller directly (the
                # ``app._sync_autostart`` facade is kept for test seams).
                from voice_typer.server import startup_tasks
                startup_tasks.sync_autostart(app)
            except Exception as e:
                log.warning("Failed to sync autostart: %s", e)

        # Register/unregister ESC hotkey
        if "esc_cancel_enabled" in updates:
            try:
                if updates["esc_cancel_enabled"]:
                    app.hotkeys.register_esc()
                else:
                    app.hotkeys.unregister_esc()
            except Exception as e:
                log.warning("Failed to sync ESC hotkey: %s", e)

        # Register/unregister repaste hotkey
        if "repaste_hotkey" in updates or "repaste_enabled" in updates:
            try:
                app.hotkeys.register_repaste()
            except Exception as e:
                log.warning("Failed to sync repaste hotkey: %s", e)

        # NEW-UX-027: re-register the dictation hotkey when recording_mode
        # or hotkey changes.
        if "recording_mode" in updates or "hotkey" in updates or "push_to_talk_hotkey" in updates:
            try:
                app.hotkeys.restart(getattr(config, "hotkey", "<f2>"))
                log.info(
                    "[SERVICE] Re-registered hotkey after recording_mode/hotkey "
                    "change (mode=%s)",
                    getattr(config, "recording_mode", "toggle"),
                )
            except Exception as e:
                log.warning("Failed to re-register hotkey after mode change: %s", e)

        # BUGFIX: tray_left_click_action was never handled — the tray
        # hardcoded "Toggle Dictation" as the left-click default, so the
        # Settings page choice was completely ignored.
        if "tray_left_click_action" in updates:
            try:
                app.tray.invalidate_menu_cache()
                log.info(
                    "[SERVICE] Tray left-click action updated to: %s",
                    updates["tray_left_click_action"],
                )
            except Exception as e:
                log.warning("Failed to update tray left-click action: %s", e)

        # BUGFIX: show_notifications changes were not applied until restart.
        if "show_notifications" in updates:
            try:
                app.tray.set_notifications_enabled(bool(updates["show_notifications"]))
                log.info(
                    "[SERVICE] Notifications %s",
                    "enabled" if updates["show_notifications"] else "disabled",
                )
            except Exception as e:
                log.warning("Failed to update notifications: %s", e)

        # BUGFIX: bubble_behavior changes were not applied until restart.
        if "bubble_behavior" in updates:
            try:
                behavior = updates["bubble_behavior"]
                if behavior == "always_visible":
                    try:
                        if hasattr(app, "_waveform_bubble"):
                            app._waveform_bubble.show()
                    except Exception:
                        pass
                elif behavior == "show_on_record":
                    # Hide bubble immediately when switching away from always_visible
                    try:
                        if hasattr(app, "_waveform_bubble") and app._waveform_bubble.visible:
                            app._waveform_bubble.hide()
                    except Exception:
                        pass
                log.info("[SERVICE] Bubble behavior updated to: %s", behavior)
            except Exception as e:
                log.warning("Failed to update bubble behavior: %s", e)

        # BUGFIX: volume_duck_smart changes were not applied until restart.
        if "volume_duck_smart" in updates:
            try:
                if hasattr(app, "_volume_ducker"):
                    app._volume_ducker.set_smart_duck_enabled(
                        bool(updates["volume_duck_smart"])
                    )
            except Exception as e:
                log.warning("Failed to update smart duck: %s", e)

        # BUGFIX: volume_duck_smart_poll_interval_ms changes not applied until restart.
        if "volume_duck_smart_poll_interval_ms" in updates:
            try:
                if hasattr(app, "_volume_ducker"):
                    app._volume_ducker.set_smart_duck_poll_interval(
                        int(updates["volume_duck_smart_poll_interval_ms"])
                    )
            except Exception as e:
                log.warning("Failed to update smart duck poll interval: %s", e)

        # Apply the audio enhancement preset: map preset name to filter toggles.
        if "audio_preset" in updates:
            try:
                preset = updates["audio_preset"]
                preset_filters = _apply_audio_preset(preset)
                # Set individual filter toggles from the preset
                for k, v in preset_filters.items():
                    setattr(config, k, v)
                log.info("[SERVICE] Applied audio preset '%s': %s", preset, preset_filters)
            except Exception as e:
                log.warning("Failed to apply audio preset: %s", e)

        # ADR 0007 §6.1: Rebuild the dictation AudioProcessor's filter
        # chain when any noise_filter_* / audio_preset / noise_suppression_method
        # config field changes. This fixes the bug where Settings UI
        # changes didn't take effect in dictation until app restart.
        # All filter-chain-related config keys (old + new per ADR 0007 §5).
        filter_chain_keys = {
            # Preset
            "audio_preset",
            # Individual filter toggles
            "noise_filter_enabled", "noise_filter_highpass",
            "noise_filter_gate", "noise_filter_rnnoise",
            "noise_filter_post_capture",
            "noise_filter_eq", "noise_filter_compressor",
            "noise_filter_limiter", "noise_filter_notch",
            # Noise suppressor backend
            "noise_suppression_method",
            # Filter parameters
            "noise_filter_highpass_cutoff_hz",
            "noise_filter_gate_hold_ms",
            "noise_filter_gate_open_threshold_db",
            "noise_filter_gate_close_threshold_db",
            "noise_filter_gate_attack_ms",
            "noise_filter_gate_release_ms",
            "noise_filter_eq_low_db", "noise_filter_eq_mid_db", "noise_filter_eq_high_db",
            "noise_filter_compressor_threshold_db", "noise_filter_compressor_ratio",
            "noise_filter_compressor_attack_ms", "noise_filter_compressor_release_ms",
            "noise_filter_compressor_output_gain_db",
            "noise_filter_limiter_ceiling_db", "noise_filter_limiter_release_ms",
            "noise_filter_notch_frequency_hz",
        }
        if filter_chain_keys & set(updates.keys()):
            # ADR 0007: rebuild the dictation processor (the main fix).
            try:
                if hasattr(app, "_rebuild_audio_processor"):
                    app._rebuild_audio_processor()
            except Exception as e:
                log.warning("Failed to rebuild dictation audio processor: %s", e)

            # Also sync the live level bar + mic test processors so
            # they reflect the new filters immediately.
            try:
                from voice_typer.server.level_monitor import (
                    update_level_processor,
                    update_test_filters,
                )
                filters_dict = {
                    "noise_filter_enabled": getattr(config, "noise_filter_enabled", True),
                    "noise_filter_highpass": getattr(config, "noise_filter_highpass", True),
                    "noise_filter_gate": getattr(config, "noise_filter_gate", True),
                    "noise_filter_rnnoise": getattr(config, "noise_filter_rnnoise", True),
                    "noise_filter_post_capture": getattr(config, "noise_filter_post_capture", True),
                }
                update_level_processor(filters_dict)
                update_test_filters(filters_dict)
            except Exception as e:
                log.warning("Failed to sync level bar processor: %s", e)

    # ── ADR 0008 §3.1: service-layer wrappers for private-attr access ──
    #
    # ARCH-REFAC-004 / TASK-2: the IPC handlers under
    # ``voice_typer/server/handlers/`` previously reached into
    # ``self.app._audio_processor``, ``self.app._config_mutation_lock``,
    # ``self.app.change_model``, and ``self.app.models.set_active_backend``
    # directly.  ADR 0008 §3.1 requires handlers to go through the
    # service layer; the methods below provide that path so the
    # handlers no longer tunnel through ``AppProtocol``'s private
    # attributes.

    def get_audio_status(self) -> dict:
        """Return the audio filter chain status (ADR 0007).

        Wraps access to ``self._app._audio_processor`` so the IPC
        ``get_audio_status`` handler doesn't tunnel through two
        private attributes (``self.service._app._audio_processor``).

        Returns a dict with ``filter_chain``, ``degraded``,
        ``degraded_reasons``, ``latency_ms``, ``vad_backend``, and
        ``sample_rate``.  When the audio processor is absent (e.g.
        during early startup or in test fixtures), a safe default
        status is returned.
        """
        app = self._app
        processor = getattr(app, "_audio_processor", None)
        if processor is not None:
            return {
                "filter_chain": processor.filter_names,
                "degraded": processor.is_degraded,
                "degraded_reasons": processor.degraded_reasons,
                "latency_ms": processor.total_latency_ms,
                "vad_backend": "silero" if getattr(app.config, "use_silero_vad", True) else "rms",
                "sample_rate": getattr(app.config, "sample_rate", 16000),
            }
        return {
            "filter_chain": [],
            "degraded": False,
            "degraded_reasons": [],
            "latency_ms": 0.0,
            "vad_backend": "rms",
            "sample_rate": 16000,
        }

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

    def apply_config(self, updates: dict) -> None:
        """Apply validated config updates atomically.

        ADR 0008 §3.1: wraps the config-mutation lock + setattr +
        side-effects + save + tray-cache invalidation sequence so the
        IPC ``set_config`` handler doesn't access
        ``self.app._config_mutation_lock``, ``self.app.config``, or
        ``self.app.tray.invalidate_menu_cache()`` directly.

        RACE-011: holds the app's config-mutation lock for the full
        read-modify-save sequence so a concurrent ``set_config`` IPC
        call can't interleave attribute writes with this update.

        AUDIO-PRESET-SAVE-FIX: runs :meth:`apply_config_side_effects`
        INSIDE the lock and saves AFTER it, so that any side-effect
        mutations (e.g. ``noise_filter_*`` toggles from the audio
        preset) are persisted to disk.  The previous order (save
        first, then apply side effects outside the lock) meant that
        when the user set ``audio_preset: "off"``, only the preset
        name was saved; the individual ``noise_filter_*`` toggles
        were NOT persisted.

        ARCH-043: invalidates the tray menu cache after the save so
        the next menu build picks up the new config values (model
        size, hotkey, etc.).

        Parameters
        ----------
        updates :
            Validated config updates dict (allowlisted keys only).
            The caller is responsible for validating the payload —
            typically via :func:`voice_typer.server.config.validate_config_update`.
        """
        app = self._app
        with app._config_mutation_lock:
            for k, v in updates.items():
                setattr(app.config, k, v)
            # Apply side effects inside the lock so Config mutations
            # from the preset are visible to save().
            self.apply_config_side_effects(updates)
            app.config.save()
        # ARCH-043: invalidate the tray menu cache so the next menu
        # build picks up the new config values.
        try:
            app.tray.invalidate_menu_cache()
        except Exception:
            log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)

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
        """Apply onboarding settings and mark complete.

        17-H-FIX-1: previously this only called ``ctrl.apply_settings``
        (which does ``config.save()``) — it never invoked
        ``apply_config_side_effects``, so the user's hotkey and model
        choices made in the first-run wizard didn't take effect until
        app restart. We now mirror the canonical ``set_config`` flow
        in ``config_handlers.py``: hold the config-mutation lock,
        invalidate the tray menu cache, re-register the dictation
        hotkey via ``apply_config_side_effects``, optionally reload
        the model, and push a ``config_changed`` event so the
        renderer doesn't need its bespoke re-fetch in
        ``handleOnboardingComplete``.
        """
        ctrl = getattr(self, "_onboarding", None)
        if ctrl is None:
            return {"error": "Onboarding not started"}
        app = self._app
        try:
            # Capture the previous model_size so we can skip the
            # (potentially expensive) model reload when the user kept
            # the default. Hotkey/mic changes are always safe to
            # re-apply.
            prev_model_size = getattr(app.config, "model_size", None)

            # RACE-011: hold the app's config-mutation lock for the
            # full apply+save sequence so a concurrent set_config call
            # can't interleave attribute writes with our onboarding
            # update. Parity with config_handlers.py:_handle_set_config.
            with app._config_mutation_lock:
                ctrl.apply_settings(app.config)
                app.config.onboarding_completed = True
                app.config.save()

            # Build the updates dict for apply_config_side_effects.
            # Only include keys that were actually set by the wizard.
            updates: dict = {
                "hotkey": ctrl.selected_hotkey,
                "model_size": ctrl.selected_model,
            }
            if ctrl.selected_microphone is not None:
                updates["microphone"] = ctrl.selected_microphone

            # ARCH-043: invalidate the tray menu cache so the next
            # menu build picks up the new hotkey/model/mic.
            try:
                app.tray.invalidate_menu_cache()
            except Exception:
                log.debug("[SERVICE] tray.invalidate_menu_cache failed", exc_info=True)

            # ARCH-005: re-register the dictation hotkey (and any
            # other side effects keyed off the updates dict) so the
            # user's choice takes effect immediately, without restart.
            self.apply_config_side_effects(updates)

            # 17-H-FIX-1: reload the model if the user picked a
            # different one. ModelManager.change_model internally
            # handles the case where the background loader hasn't
            # finished yet — it queues the change via
            # _pending_model_change (model_manager.py:456) and
            # applies it on the next _start_dictation. If the loader
            # HAS finished, the full unload/load cycle runs
            # immediately.
            new_model = ctrl.selected_model
            if new_model != prev_model_size:
                try:
                    app.models.change_model(new_model)
                except Exception as e:
                    log.warning("[SERVICE] onboarding model change failed: %s", e)

            # Push a config_changed event so the renderer (App.tsx)
            # can update UI-local state (theme, font-scale, hotkey
            # label, etc.) immediately instead of waiting for the
            # next mount or issuing a bespoke get_config round-trip.
            # Parity with set_config in config_handlers.py.
            try:
                from voice_typer.server.ipc_server import _push_event_now
                _push_event_now({
                    "type": "config_changed",
                    "data": updates,
                })
            except Exception:
                log.debug("[SERVICE] onboarding config_changed push failed", exc_info=True)

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
                        model_name, symlink,
                    )
                    errors.append({
                        "model": model_name,
                        "error": (
                            f"Refusing to import model containing a symlink "
                            f"({symlink}). Symlinks are not permitted in "
                            f"imported model cache directories."
                        ),
                    })
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
                len(found_models), len(imported_models), len(errors),
            )
        elif found_models:
            log.warning(
                "[SERVICE] Model import: %d found, 0 imported, %d errors — "
                "all imports failed",
                len(found_models), len(errors),
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
        """
        if self._download_cancel_event is not None:
            self._download_cancel_event.set()
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

    def download_model(self, model_name: str) -> dict:
        """Download a model weight file via HuggingFace.

        UX-005: Downloads the specified model (tiny.en, small.en, medium.en,
        large-v3, qwen, parakeet) to the local HF cache. Pushes
        ``download_progress`` events to the renderer so the Models page
        can update its progress bar and status text in real time, and
        fires a tray notification on completion / failure.
        Returns a result dict with success status.

        NEW-MODEL-001: now supports the turbo + distilled variants via
        :mod:`voice_typer.server.model_registry`.  The repo_id is
        resolved from the registry instead of being hard-coded.

        NEW-PAUSE-001: the polling loop checks
        :func:`asr_setup.is_download_paused` between iterations.  When
        paused, progress updates freeze and a ``paused: True`` event is
        pushed once per transition.  Resume clears the flag and pushes
        a ``resumed: True`` event.
        """
        import os

        # UX-005: helper to push progress events to the renderer.
        from voice_typer.server.ipc_server import _push_event_now

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
            _push_event_now({"type": "download_progress", "data": data})

        def _notify(title: str, message: str) -> None:
            try:
                self._app.tray.notify(title, message)
            except Exception:
                log.debug("[SERVICE] tray notify failed", exc_info=True)

        try:
            # NEW-MODEL-001: consult the model registry so we support
            # turbo + distilled variants without hard-coding name-to-repo
            # mappings.  Falls back to the legacy hard-coded tuple for
            # any registry drift.
            from voice_typer.server.model_registry import get_model_metadata
            model_meta = get_model_metadata(model_name)
            is_whisper_family = (
                model_meta is not None
                and model_meta.backend in ("whisper", "distil-whisper")
            )
            if is_whisper_family:
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
                        "*.safetensors", "*.bin", "config.json", "tokenizer.json",
                        "tokenizer_config.json", "special_tokens_map.json",
                        "preprocessor_config.json", "feature_extractor_config.json",
                        "generation_config.json", "model.safetensors.index.json", "*.model",
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
                            model_name, repo_id,
                        )
                        _push_progress(100, f"{model_name} already cached")
                    except Exception:
                        # NEW-MODEL-001: pull target size from the
                        # registry instead of the hard-coded size_targets
                        # table.  Falls back to 500 MB if missing.
                        target_mb = (
                            model_meta.download_size_mb
                            if model_meta.download_size_mb
                            else 500
                        )
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
                        # NEW-PRIV-011: create a cancellation event for
                        # this download.
                        self._download_cancel_event = threading.Event()
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
                            model_name, target_mb,
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
                            # NEW-PRIV-011: check for cancellation.
                            if self._download_cancel_event.is_set():
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
                                transition_pct = max(0, min(95, int(
                                    10 + (last_total_bytes_seen
                                          / max(1, target_bytes))
                                    * 85
                                )))
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
                                if cache_dir.exists():
                                    total_bytes_seen = sum(
                                        f.stat().st_size
                                        for f in cache_dir.rglob("*")
                                        if f.is_file()
                                    )
                                    total_mb_seen = total_bytes_seen // (1024 * 1024)
                                    pct = min(95, int(10 + (total_mb_seen / target_mb) * 85))
                                    # Log progress at whole-number percentage thresholds
                                    if pct >= 25 and pct % 25 == 0:
                                        log.info(
                                            "[SERVICE] Download of '%s': %d%% (%d MB / ~%d MB)",
                                            model_name, pct, total_mb_seen, target_mb,
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
                        self._download_cancel_event = None
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
                            model_name, last_total_bytes_seen // (1024 * 1024),
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
                self._download_cancel_event = None
                # NEW-PAUSE-001: clear the pause flag so subsequent
                # pause calls return False (no active download).
                clear_download_pause_state()
                _notify(APP_NAME, f"Model '{model_name}' downloaded successfully")
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
                log.info("[SERVICE] Download requested for '%s' (Parakeet backend, ~2.5 GB)", model_name)
                _push_progress(0, "Starting Parakeet download (~2.5 GB)...")
                from voice_typer.server.asr_setup import download_parakeet_weights
                # asr_setup.download_parakeet_weights() doesn't expose
                # progress; we emit start/finish events.
                _push_progress(50, "Downloading Parakeet weights from HuggingFace...")
                download_parakeet_weights()
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
            self._download_cancel_event = None
            # NEW-PAUSE-001: clear the pause flag on failure too.
            try:
                from voice_typer.server.asr_setup import clear_download_pause_state
                clear_download_pause_state()
            except Exception:
                log.debug("[SERVICE] could not clear pause flag on failure", exc_info=True)
            _push_progress(0, f"Download failed: {exc}")
            _notify(APP_NAME, f"Failed to download {model_name}: {exc}")
            return {"success": False, "error": str(exc)}

    # ── PROD-010: Export diagnostics ─────────────────────────────────

    def export_diagnostics(self) -> dict:
        """PROD-010: Create a diagnostic bundle for support.

        Delegates to CrashRecovery.create_diagnostic_bundle().
        Returns ``{"success": bool, "path": str}`` on success or
        ``{"success": False, "message": str}`` on failure.
        """
        try:
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
            return {"success": False, "message": str(exc)}
