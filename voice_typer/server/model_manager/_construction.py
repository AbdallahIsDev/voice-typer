"""Engine construction via AsrBackendRegistry (single chokepoint)."""

from __future__ import annotations

import logging

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME

log = logging.getLogger("voice_typer.server.model_manager")


class ConstructionMixin:
    def _ensure_engine(self, backend_name: str) -> None:
        """Ensure the engine object for ``backend_name`` exists (no load)."""
        if self._registry.get(backend_name) is not None:
            return
        try:
            if backend_name == "parakeet":
                self._registry.create(
                    "parakeet",
                    parakeet_kwargs=dict(
                        device=self._app.config.device,
                        language=self._app.config.language,
                    ),
                )
            elif backend_name == "qwen":
                self._registry.create(
                    "qwen",
                    qwen_kwargs=dict(
                        model_path=self._app.config.qwen_model_path,
                        device=self._app.config.device,
                        language=self._app.config.language,
                    ),
                )
            else:
                self._registry.create(
                    "whisper",
                    whisper_kwargs=dict(
                        model_size=self._app.config.model_size,
                        device=self._app.config.device,
                        language=self._app.config.language,
                        beam_size=self._app.config.beam_size,
                        best_of=self._app.config.best_of,
                        condition_on_previous_text=self._app.config.condition_on_previous_text,
                        # pass the live Config reference so the engine
                        config=self._app.config,
                    ),
                )
        except Exception as exc:
            log.exception("[MODEL] Failed to initialize %s engine: %s", backend_name, exc)
            # surface to user via tray notification so they
            try:
                hint = ""
                if backend_name == "qwen":
                    hint = " Check that the Qwen model path is set correctly in Settings."
                elif backend_name == "parakeet":
                    hint = " Check that Parakeet weights are downloaded."
                self._app.tray.notify(
                    APP_NAME,
                    i18n.t(
                        "notify.model_manager.backend_init_failed",
                        backend=backend_name.title(),
                        hint=hint,
                    ),
                )
            except Exception:
                # previously a bare ``except Exception: pass``.
                log.error(
                    "[MODEL] tray.notify ALSO failed for backend init error",
                    exc_info=True,
                )
            # Re-raise so callers (load_background, ensure_active_engine_loaded)
            raise
