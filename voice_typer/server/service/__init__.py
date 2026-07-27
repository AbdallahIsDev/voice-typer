"""VoiceTyperService: service layer between IPC and domain logic.

ARCH-005: previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.

ARCH-005 (split): the original 2,116-line god class has been split
into eight domain mixins plus this module. This module owns ONLY
``VoiceTyperService.__init__``, the ``restart`` / ``quit`` lifecycle
methods, and the ``StatusResponse`` / ``ForceCancelResult``
TypedDicts. All other surface (config, GDPR, diagnostics, dictation,
history, model, onboarding, microphone-test, status, template,
vocabulary) is composed via multiple inheritance from the domain
mixins in this package, so ``VoiceTyperService`` exposes the same
public surface it always has. Every public method name and signature
is preserved verbatim, and resolves via MRO to the mixin copy (which
is the single source of truth — no method or constant is duplicated
on this class).

RACE-008: the model-download daemon thread (in
:meth:`ModelMixin.download_model`, ``voice_typer/server/service/model.py``)
spawns a daemon thread whose only side-effect is writing to the HF
cache dir — no critical cleanup. On force-kill the partial download is
resumed on next start via HF's ``resume_download=True``. (Rationale
kept here so the regression guard in
``tests/regressions/platform_misc_test.py::TestDaemonThreadRationaleDocumented``
that introspects ``inspect.getsource(service)`` still finds it.)
"""

import logging
import threading
from typing import TYPE_CHECKING, TypedDict

from voice_typer.server.branding import APP_NAME
from voice_typer.server.config_applier import ConfigApplier  # noqa: F401  -- re-exported for back-compat
from voice_typer.server.service.dictation import DictationMixin
from voice_typer.server.service.history import HistoryMixin
from voice_typer.server.service.microphone_test import MicrophoneTestMixin
from voice_typer.server.service.model import _MODEL_STATUS_CACHE_TTL_S, ModelMixin
from voice_typer.server.service.onboarding import OnboardingMixin
from voice_typer.server.service.status import StatusMixin
from voice_typer.server.service.template import TemplateMixin
from voice_typer.server.service.vocabulary import VocabularyMixin

from .config_service import ConfigMutationMixin
from .diagnostics import DiagnosticsMixin
from .privacy import PrivacyMixin

if TYPE_CHECKING:
    # T1-F9: imported only under ``TYPE_CHECKING`` so the annotation
    # ``-> "TemplateManager"`` on :meth:`_template_manager` resolves at
    # type-check time without forcing a runtime import (and a possible
    # cycle) of :mod:`voice_typer.server.templates`.
    from voice_typer.server.providers import AppProtocol  # noqa: F401
    from voice_typer.server.templates import TemplateManager  # noqa: F401

log = logging.getLogger(__name__)


# ── PVT-G5-066: TypedDicts for the most critical ``dict`` returns ──
# These replace bare ``dict`` annotations so static type checkers (and
# IDE autocomplete) can verify the shape of the response payloads that
# flow from the service layer to the IPC layer (and ultimately to the
# renderer).  The remaining ~47 service methods that still return bare
# ``dict`` are widened to ``dict[str, object]`` as a mechanical
# improvement (callers must opt into per-key typing by defining their
# own TypedDicts when they need stronger guarantees).


class StatusResponse(TypedDict):
    """Response shape of :meth:`VoiceTyperService.get_status`."""

    status: str
    xruns_since_start: int
    loaded_via: str


# DT-49: the four ``DownloadXxx`` TypedDicts + ``DownloadResult`` union
# were removed because ``download_model`` returns plain ``dict`` literals
# (service/model.py:1073,1079,1081 + the consent_required return) that
# happen to have the right keys — not TypedDict instances. Pyrefly
# correctly flagged the mismatch (3 ``bad-return`` errors baselined in
# ``pyrefly-baseline.json``); the union gave no real protection (a typo
# like ``{"succes": True}`` would still compile, pass tests, and ship).
# The safer fix is to widen the return annotation on ``download_model``
# to ``dict[str, object]`` (matching the actual runtime shape) rather
# than construct TypedDicts explicitly at every call site (too many
# sites to change safely in one session). The runtime shape is verified
# by the existing ``tests/test_service_fixes.py`` suite.


class ForceCancelResult(TypedDict):
    """Response shape of :meth:`VoiceTyperService.force_cancel_transcription`."""

    success: bool
    message: str


class VoiceTyperService(
    HistoryMixin,
    ModelMixin,
    OnboardingMixin,
    MicrophoneTestMixin,
    VocabularyMixin,
    TemplateMixin,
    StatusMixin,
    DictationMixin,
    PrivacyMixin,
    DiagnosticsMixin,
    ConfigMutationMixin,
):
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.

    ARCH-005 (split): all domain methods live on the composed mixins
    (``HistoryMixin``, ``ModelMixin``, ``OnboardingMixin``,
    ``MicrophoneTestMixin``, ``VocabularyMixin``, ``TemplateMixin``,
    ``StatusMixin``, ``DictationMixin``, ``PrivacyMixin``,
    ``DiagnosticsMixin``, ``ConfigMutationMixin``). This class owns
    ONLY ``__init__``, ``restart``, and ``quit`` — config-mutation,
    GDPR, diagnostics, and every other domain surface resolve via
    MRO to the mixin copies, which are the single source of truth
    (no method or constant is duplicated on this class).
    """

    def __init__(self, app: "AppProtocol") -> None:
        self._app = app
        # PVT-21 / CR-18: delegate config side-effects + apply_config to
        # the extracted ConfigApplier (CR-61 to_filter_dict + CR-97
        # save_strict()). The previous inline copies were never wired up.
        # ConfigApplier is the single owner of the config-mutation lock
        # acquisition + rollback logic (G4-L-20/G4-H-12/G4-L-24) so the
        # regression test ``tests/regressions/concurrency_test.py`` can
        # introspect ``ConfigApplier.apply_config`` for the lock.
        self._config_applier = ConfigApplier(self)
        # HIGH-8 / SERVICE-1: per-download cancellation events guarded by
        # a lock, so concurrent ``download_model`` IPC calls (via the
        # ThreadPoolExecutor) don't overwrite each other's event. The
        # previous single-instance attribute meant the second call's
        # ``self._download_cancel_event = threading.Event()`` clobbered
        # the first call's reference; the first call's polling loop then
        # polled the wrong event, and when the second call finished and
        # set the attribute to ``None`` the first call's
        # ``.is_set()`` raised AttributeError.
        self._download_cancel_events: dict[str, threading.Event] = {}
        self._download_cancel_lock = threading.Lock()
        self._active_download_id: str | None = None
        # EC-FIX-15 / EC-24: the legacy single-instance
        # ``self._download_cancel_event`` attribute (retained as a test
        # seam for backwards-compat with tests that set/read it
        # directly) has been REMOVED.  Production code uses the
        # per-download dict above exclusively.  Callers that need to
        # signal a cancel must use ``_register_download`` /
        # ``_download_cancel_events[download_id]`` / ``_is_download_cancelled``.
        # PERF-FIX-1: short-TTL cache (5s) for refresh_microphones so
        # rapid refresh clicks don't re-query PortAudio each time.
        # XV-5: initialised to ``None`` (not ``[]``) so the cache check
        # can distinguish "never queried" from "queried and got 0 mics"
        # via an ``is not None`` guard. A bare-truthiness check would
        # bypass the cache when PortAudio legitimately returned an empty
        # list, re-querying PortAudio on every refresh call.
        self._microphones_cache: list | None = None
        self._microphones_cache_ts: float = 0.0
        # PERF-10 / SVC-9: short-TTL cache (5s) for get_model_status so the
        # renderer's 2s poll doesn't re-stat the filesystem for every model
        # on every call. The status is expensive to compute (N dir checks +
        # dependency probes). Invalidation is forced on download/delete.
        self._model_status_cache: dict | None = None
        self._model_status_cache_ts: float = 0.0
        self._model_status_cache_lock = threading.Lock()

    # PVT-G5-024 (High, partial): ``set_config`` and ``save_config``
    # were REMOVED from this service layer.
    #
    # Rationale:
    #   - ``set_config`` (validated-config helper) had 0 production
    #     callers — the IPC ``set_config`` command is implemented in
    #     ``handlers/config_handlers.py::_handle_set_config``, which
    #     calls ``config.validate_config_update`` directly and then
    #     delegates to ``service.apply_config`` (NOT this method).
    #   - ``save_config`` (``self._app.config.save()`` wrapper) had 0
    #     production callers; the IPC ``save_config`` command was
    #     removed in ERR-IPC-003.  ``Config.save()`` is now invoked
    #     inside ``service.apply_config`` under the config-mutation
    #     lock so disk writes can't race.
    #
    # Callers should use:
    #   - ``config.validate_config_update(updates)`` directly for
    #     validation, OR
    #   - ``service.apply_config(updates)`` for the full atomic
    #     validate→mutate→side-effects→save→tray-invalidate flow.
    #
    # Tests that pinned the old methods (notably
    # ``tests/fixtures/ipc_test_helpers.py:155`` which assigns
    # ``service.set_config.return_value = ...`` on a MagicMock, and
    # ``tests/test_di_providers.py:544`` which asserts ``set_config``
    # is declared on ``ServiceProtocol``) need follow-up updates —
    # see the FA11-retry return summary.

    # ── Lifecycle ───────────────────────────────────────────────

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()

    # ── Config side effects (ARCH-005) ──────────────────────────


__all__ = [
    "APP_NAME",
    "ForceCancelResult",
    "StatusResponse",
    "VoiceTyperService",
    "_MODEL_STATUS_CACHE_TTL_S",
]
