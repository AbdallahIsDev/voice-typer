"""Status / health-check domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Read-only queries that surface app state
(tray status, xruns, audio filter chain, volume backend).
"""

import logging
import time
from typing import TYPE_CHECKING

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._secrets import redact_secret, redact_url
from voice_typer.server.service._base import ServiceMixinBase

if TYPE_CHECKING:
    # ``StatusResponse`` is a TypedDict defined in
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

    # notify-once guard for volume_ducker.initialize failures.
    # The status endpoint is polled ~every 2s; log first occurrence at
    # WARNING, subsequent at DEBUG.
    _volume_ducker_init_warned: bool = False

    # Per-instance cache for :meth:`get_volume_backend_status`.
    #
    # The status endpoint is polled every ~2s by the renderer; the
    # previous implementation called ``ducker.initialize()`` on every
    # poll. ``initialize()`` is idempotent (it short-circuits on
    # ``self._initialized``), but each call still acquires the ducker's
    # internal lock and re-reads ``self._backend`` / ``self._ready`` /
    # ``self.supports_per_session`` — wasted work that adds up across
    # thousands of polls. We now compute the status dict ONCE (on the
    # first call), cache it here, and return the cached value on
    # subsequent polls. The cache is invalidated only on an explicit
    # ``_force_refresh=True`` call (the UI's "Refresh" button) — see
    # :meth:`get_volume_backend_status` for the invalidation contract.
    #
    # ``None`` means "no cache yet" (the very first poll); a dict
    # value means "cached status from a previous successful poll". The
    # cache is per-instance (each :class:`VoiceTyperService` gets its
    # own) because the underlying ``_volume_ducker`` is also
    # per-instance. Class-level default of ``None`` is safe — it is
    # an immutable singleton, so the class-attribute fallback doesn't
    # leak state across instances (the first ``self.X = {...}``
    # assignment shadows the class attribute with an instance
    # attribute on the same ``self``).
    _volume_backend_status_cache: dict[str, object] | None = None

    # ── Status ──────────────────────────────────────────────────

    # Offline-pack state cache for :meth:`_get_offline_pack_status`.
    # The status endpoint is polled ~every 2s; the pack scan (``iterdir``
    # + ``pack-manifest.json`` parse) must not run on every poll. The pack
    # only appears/disappears via download or AV-deletion — both rare vs.
    # the poll rate — so a 15s TTL cache is safe (mirrors the
    # ``_volume_backend_status_cache`` pattern in this class).
    _OFFLINE_PACK_STATUS_TTL_S = 15.0
    _pack_status_cache: dict[str, object] | None = None
    _pack_status_cached_at: float = 0.0

    def _get_offline_pack_status(self) -> dict[str, object]:
        """Cheap, cached offline-pack state for the degradation matrix (§8.10).

        Returns ``{"installed_version": <str|None>, "available": bool,
        "consent_granted": bool}``. Never raises — a broken pack root
        yields ``available: False`` (fail-safe: the renderer shows the
        "offline engine unavailable" state rather than a false ready).

        ``installed_version`` comes from
        ``update_check._local_offline_pack_version`` (existence check —
        no SHA-256 hashing; the full checksum runs in the background at
        launch via ``BackgroundChecksum``, §8.16).
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
        except Exception:  # noqa: BLE001 — fail-safe: degraded state, never raise
            log.debug("[SERVICE] offline pack status unavailable", exc_info=True)
        self._pack_status_cache = state
        self._pack_status_cached_at = now
        return state

    def get_status(self) -> "StatusResponse":  # noqa: F821 (forward ref resolved in __init__)
        """Return the current app state plus audio-quality telemetry.

        previously returned only the tray state string. The
                xrun counter was tracked in the recorder but never reached the
                IPC layer, so the UI couldn't warn the user of degraded audio.
                We now return a dict with ``status``, ``xruns_since_start``,
                ``offline_pack`` (Phase 2d degradation matrix, §8.10),
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
        # read the active engine's loaded_via property.
        loaded_via = ""
        try:
            active = app.models._registry.get_active() if hasattr(app, "models") and app.models else None
            if active is not None and hasattr(active, "loaded_via"):
                loaded_via = str(active.loaded_via)
        except Exception:
            log.debug("[SERVICE] could not read loaded_via", exc_info=True)
        # expose the resolved config directory so the About page's
        # "Config Directory" diagnostic resolves to a real path. The
        # renderer previously expected a ``config_dir`` field here that
        # the backend never sent, so the About page showed a permanent
        # "Loading…" placeholder.
        config_dir = ""
        try:
            config_dir = str(app.config.config_dir)
        except Exception:
            log.debug("[SERVICE] could not read config_dir", exc_info=True)
        return {
            "status": status_str,
            "xruns_since_start": xruns,
            "loaded_via": loaded_via,
            "config_dir": config_dir,
            "offline_pack": self._get_offline_pack_status(),
        }

    # Volume / Model status () ────────────────────────

    def get_volume_backend_status(self, *, _force_refresh: bool = False) -> dict[str, object]:
        """Return the volume ducking backend status.

        Performance contract: the renderer polls this method every ~2s.
        ``ducker.initialize()`` is invoked at most ONCE per instance
        (on the first call, when the cache is empty); subsequent polls
        return the cached status dict without re-running
        ``initialize()``. ``initialize()`` is idempotent on
        :class:`VolumeDucker` (it short-circuits on
        ``self._initialized``), but the call still acquires the ducker's
        internal lock and re-reads backend attributes — wasted work
        across thousands of polls.

        Cache invalidation: the cached ``backend_name`` /
        ``is_available`` / ``supports_per_session`` / ``backend`` values
        are invalidated ONLY on an explicit ``_force_refresh=True``
        call. Pass ``_force_refresh=True`` from the UI's "Refresh
        Volume Backend" button (or any caller that knows the underlying
        platform state has changed — e.g. after the user installs
        ``pyobjc-framework-CoreAudio`` mid-session, which switches the
        macOS backend from osascript to CoreAudio). The default
        ``_force_refresh=False`` is for the 2s status poll path.

        Note: ``_force_refresh`` is prefixed with an underscore because
        it is NOT yet wired through the IPC ``get_volume_backend_status``
        handler (the handler calls this method with no arguments, so the
        default ``False`` applies — preserving the poll-path caching
        contract). A separate task will add a ``refresh_volume_backend``
        IPC command that passes ``_force_refresh=True`` through.

        Args:
            _force_refresh: When ``True``, bypass the cache, re-run
                ``ducker.initialize()``, and refresh the cached status.
                Default ``False`` (use cache).

        Returns:
            A dict with ``available``, ``name``,
            ``supports_per_session``, and ``backend`` keys (plus a
            ``reason`` key on failure). The returned dict is a shallow
            copy of the cache so callers can freely mutate it without
            corrupting the cached state.
        """
        ducker = getattr(self._app, "_volume_ducker", None)
        if ducker is None:
            return {
                "available": False,
                "name": "disabled",
                "supports_per_session": False,
            }

        # Fast path: serve from cache when available and the caller
        # didn't ask for a refresh. Returning a copy so callers can't
        # mutate our cached dict (the IPC handler adds ``is_windows``
        # to the returned dict — without a copy that would leak into
        # the cache and show up on the next poll).
        cache = self._volume_backend_status_cache
        if cache is not None and not _force_refresh:
            return dict(cache)

        try:
            # Trigger initialize() so the backend name reflects
            # the actual platform backend (not "disabled"
            # merely because nothing has ducked yet).
            #
            # On the default poll path this branch runs at most ONCE
            # per instance (the cache is populated below and the next
            # poll takes the fast path above). With _force_refresh=True
            # the cache is bypassed and initialize() is re-invoked.
            init_ok = False
            try:
                ducker.initialize()
                # reset notify-once guard on success.
                StatusMixin._volume_ducker_init_warned = False
                init_ok = True
            except Exception:
                # notify-once — log first failure at WARNING,
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
            status = {
                "available": bool(ducker.is_available),
                "name": ducker.backend_name,
                "supports_per_session": bool(ducker.supports_per_session),
                "backend": type(ducker).__name__,
            }
            # Cache the status only when initialize() succeeded OR
            # the caller explicitly asked for a refresh. On the default
            # poll path with a failed initialize(), we DON'T cache so
            # the next poll retries initialize() — this preserves the
            # previous "retry every poll until init succeeds" behaviour
            # for users who install a missing dependency mid-session
            # without clicking the Refresh button. When the caller
            # passes _force_refresh=True, we cache the best-effort
            # status regardless of init outcome (the user explicitly
            # asked for the current state).
            if init_ok or _force_refresh:
                self._volume_backend_status_cache = status
            return dict(status)
        except Exception as exc:
            # redact exc string before returning to IPC layer.
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
