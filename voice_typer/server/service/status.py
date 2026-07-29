"""Status / health-check domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Read-only queries that surface app state
(tray status, xruns, audio filter chain, volume backend).
"""

import logging
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._base import ServiceMixinBase

if TYPE_CHECKING:
    # WR-14: ``StatusResponse`` is a TypedDict defined in
    # ``voice_typer/server/service/__init__.py`` (which imports this
    # mixin via ``from voice_typer.server.service.status import
    # StatusMixin``). Importing it at runtime would create a circular
    # import, so we resolve the forward-reference annotation only under
    # ``TYPE_CHECKING`` (pyrefly / mypy) and leave the runtime annotation
    # as a string.
    from voice_typer.server.service import StatusResponse

log = logging.getLogger(__name__)


class StatusMixin(ServiceMixinBase):
    """Status / health-check service methods.

    These are read-only queries over ``self._app`` state; they don't
    mutate config or trigger side effects.
    """

    # XZ-EH-021: notify-once guard for volume_ducker.initialize failures.
    # The status endpoint is polled ~every 2s; log first occurrence at
    # WARNING, subsequent at DEBUG.
    _volume_ducker_init_warned: bool = False

    # ── Status ──────────────────────────────────────────────────

    def get_status(self) -> "StatusResponse":  # noqa: F821 (forward ref resolved in __init__)
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

    # ── Volume / Model status (ARCH-005) ────────────────────────

    def get_volume_backend_status(self) -> dict[str, object]:
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
                # XZ-EH-021: reset notify-once guard on success.
                StatusMixin._volume_ducker_init_warned = False
            except Exception:
                # XZ-EH-021: notify-once — log first failure at WARNING,
                # subsequent at DEBUG (status endpoint polled ~every 2s).
                if not StatusMixin._volume_ducker_init_warned:
                    log.warning(
                        "[SERVICE] volume_ducker.initialize failed - subsequent failures will be logged at DEBUG",
                        exc_info=True,
                    )
                    StatusMixin._volume_ducker_init_warned = True
                else:
                    log.debug(
                        "[SERVICE] volume_ducker.initialize failed (repeat)",
                        exc_info=True,
                    )
            return {
                "available": bool(ducker.is_available),
                "name": ducker.backend_name,
                "supports_per_session": bool(ducker.supports_per_session),
                "backend": type(ducker).__name__,
            }
        except Exception as exc:
            # XZ-EH-001: redact exc string before returning to IPC layer.
            # Sister methods (delete_model, test_llm_connection, etc.) all
            # call redact_secret(redact_url(str(exc))) to avoid leaking
            # secrets / URLs / file paths via the renderer.
            log.warning(
                "[SERVICE] get_volume_backend_status failed: %s",
                redact_secret(redact_url(str(exc))),
            )
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
                "reason": redact_secret(redact_url(str(exc))),
            }

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
                "vad_backend": "silero" if app.config.use_silero_vad else "rms",
                "sample_rate": app.config.sample_rate,
            }
        return {
            "filter_chain": [],
            "degraded": False,
            "degraded_reasons": [],
            "latency_ms": 0.0,
            "vad_backend": "rms",
            "sample_rate": WHISPER_SAMPLE_RATE,
        }
