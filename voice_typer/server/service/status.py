"""Service status/metrics mixin."""

import logging
import time
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._base import ServiceMixinBase

if TYPE_CHECKING:
    # ``StatusResponse`` is a TypedDict defined in
    from voice_typer.server.service import StatusResponse

log = logging.getLogger(__name__)


class StatusMixin(ServiceMixinBase):
    """Status / health-check service methods."""

    # notify-once guard for volume_ducker.initialize failures.
    _volume_ducker_init_warned: bool = False

    # Per-instance cache for :meth:`get_volume_backend_status`.
    _volume_backend_status_cache: dict[str, object] | None = None

    # TTL for the volume-backend status cache (fixes the
    _VOLUME_BACKEND_STATUS_TTL_S = 30.0
    _volume_backend_status_cached_at: float = 0.0

    # Offline-pack state cache for :meth:`_get_offline_pack_status`.
    _OFFLINE_PACK_STATUS_TTL_S = 15.0
    _pack_status_cache: dict[str, object] | None = None
    _pack_status_cached_at: float = 0.0

    def _get_offline_pack_status(self) -> dict[str, object]:
        """Cheap, cached offline-pack state for the degradation matrix (§8.10).

        Returns ``{"installed_version": <str|None>, "available": bool,
        """
        now = time.monotonic()
        cached = self._pack_status_cache
        if cached is not None and (now - self._pack_status_cached_at) < self._OFFLINE_PACK_STATUS_TTL_S:
            return cached
        state: dict[str, object] = {
            "installed_version": None,
            "available": False,
            "consent_granted": False,
        }
        try:
            from voice_typer.server.service import update_check

            local = update_check._local_offline_pack_version()
            state["installed_version"] = local
            state["available"] = local is not None
            cfg = getattr(self._app, "config", None)
            state["consent_granted"] = bool(getattr(cfg, "offline_pack_consent", False))
        except Exception:  # fail-safe: degraded state, never raise
            log.debug("[SERVICE] offline pack status unavailable", exc_info=True)
        self._pack_status_cache = state
        self._pack_status_cached_at = now
        return state

    def get_status(self) -> "StatusResponse":
        """Return the current app state plus audio-quality telemetry."""
        app = self._app
        status_str = app.tray.state.value
        # The tray-tooltip reason accompanying the current state (e.g.
        message = ""
        try:
            raw_message = getattr(app.tray, "_message", "")
            if isinstance(raw_message, str):
                message = raw_message
        except Exception:
            log.debug("[SERVICE] could not read tray message", exc_info=True)
        # Best-effort: xruns counter exists on the Recorder instance.
        xruns = 0
        try:
            xruns = int(getattr(app.recorder, "_xruns", 0) or 0)
        except Exception:
            log.debug("[SERVICE] could not read xrun counter", exc_info=True)
        # read the active engine's loaded_via property.
        loaded_via = ""
        try:
            active = app.models._registry.get_active() if hasattr(app, "models") and app.models else None
            if active is not None and hasattr(active, "loaded_via"):
                loaded_via = str(active.loaded_via)
        except Exception:
            log.debug("[SERVICE] could not read loaded_via", exc_info=True)
        # expose the resolved config directory so the About page's
        config_dir = ""
        try:
            config_dir = str(app.config.config_dir)
        except Exception:
            log.debug("[SERVICE] could not read config_dir", exc_info=True)
        return {
            "status": status_str,
            "message": message,
            "xruns_since_start": xruns,
            "loaded_via": loaded_via,
            "config_dir": config_dir,
            "offline_pack": self._get_offline_pack_status(),
        }

    def get_volume_backend_status(self, *, _force_refresh: bool = False) -> dict[str, object]:
        """Return the volume ducking backend status."""
        ducker = getattr(self._app, "_volume_ducker", None)
        if ducker is None:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
            }

        # Fast path: serve from cache when available and the caller
        cache = self._volume_backend_status_cache
        cache_fresh = (time.monotonic() - self._volume_backend_status_cached_at) < self._VOLUME_BACKEND_STATUS_TTL_S
        if cache is not None and not _force_refresh and cache_fresh:
            return dict(cache)

        try:
            # Trigger initialize() so the backend name reflects
            init_ok = False
            try:
                ducker.initialize()
                # reset notify-once guard on success.
                StatusMixin._volume_ducker_init_warned = False
                init_ok = True
            except Exception:
                # notify-once, log first failure at WARNING,
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
            status = {
                "available": bool(ducker.is_available),
                "name": ducker.backend_name,
                "supports_per_session": bool(ducker.supports_per_session),
                "backend": type(ducker).__name__,
            }
            # Cache the status only when initialize() succeeded OR
            if init_ok or _force_refresh:
                self._volume_backend_status_cache = status
                self._volume_backend_status_cached_at = time.monotonic()
            return dict(status)
        except Exception as exc:
            # redact exc string before returning to IPC layer.
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
        """Return the audio filter chain status (ADR 0007)."""
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
