"""Status IPC handler mixin: get_status, get_audio_status, get_model_status,
get_volume_backend_status, get_rms_level.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.ipc_server import log
from voice_typer.server.platform_utils import is_windows


class StatusHandlersMixin:
    """Mixin: status-query IPC handlers (get_status / get_audio_status / ...)."""

    def _handle_get_status(self, data, resp) -> dict | None:
        """Handle the ``get_status`` IPC command."""
        resp["type"] = "status"
        # ERR-021: get_status() now returns a dict with status +
        # xruns_since_start. Preserve backward-compat by passing
        # the whole dict through.
        status_data = self.service.get_status()
        if isinstance(status_data, dict):
            resp["data"] = status_data
        else:
            # Backward-compat: older service.get_status() returned a string.
            resp["data"] = {"status": status_data}
        return resp

    def _handle_get_rms_level(self, data, resp) -> dict | None:
        """Handle the ``get_rms_level`` IPC command."""
        # AUDIO-RMS: return the current RMS level from the recorder.
        # Allows the Electron UI to show real-time audio level
        # without depending on the waveform bubble callback.
        try:
            result = self.service.get_rms_level()
            resp["type"] = "rms_level"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] get_rms_level failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_volume_backend_status(self, data, resp) -> dict | None:
        """Handle the ``get_volume_backend_status`` IPC command."""
        # Returns the active volume backend's name + capability flags
        # ARCH-005: delegates to service layer
        try:
            status = self.service.get_volume_backend_status()
            status["is_windows"] = is_windows()
            resp["type"] = "volume_backend_status"
            resp["data"] = status
        except Exception as e:
            log.error("[IPC] get_volume_backend_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_audio_status(self, data, resp) -> dict | None:
        """Handle the ``get_audio_status`` IPC command."""
        # ADR 0007: returns the current audio filter chain status
        # (filter names, degraded flags, VAD backend, sample rate).
        try:
            app = self.service._app
            processor = getattr(app, "_audio_processor", None)
            if processor is not None:
                resp["type"] = "audio_status"
                resp["data"] = {
                    "filter_chain": processor.filter_names,
                    "degraded": processor.is_degraded,
                    "degraded_reasons": processor.degraded_reasons,
                    "latency_ms": processor.total_latency_ms,
                    "vad_backend": "silero" if getattr(app.config, "use_silero_vad", True) else "rms",
                    "sample_rate": getattr(app.config, "sample_rate", 16000),
                }
            else:
                resp["type"] = "audio_status"
                resp["data"] = {
                    "filter_chain": [],
                    "degraded": False,
                    "degraded_reasons": [],
                    "latency_ms": 0.0,
                    "vad_backend": "rms",
                    "sample_rate": 16000,
                }
        except Exception as e:
            log.error("[IPC] get_audio_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_model_status(self, data, resp) -> dict | None:
        """Handle the ``get_model_status`` IPC command."""
        # Item 10/11: check which models are actually on disk.
        # Returns a dict mapping model name → {downloaded: bool, deps_ok: bool}.
        # ARCH-005: delegates to service layer
        try:
            status = self.service.get_model_status()
            resp["type"] = "model_status"
            resp["data"] = status
        except Exception as e:
            log.error("[IPC] get_model_status failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
