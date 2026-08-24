"""VoiceTyperService: service layer between IPC and domain logic.

previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.

 (split): the original 2,116-line god class has been split
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

the model-download daemon thread (in
:meth:`ModelMixin.download_model`, ``voice_typer/server/service/model.py``)
spawns a daemon thread whose only side-effect is writing to the HF
cache dir — no critical cleanup. On force-kill the partial download is
resumed on next start via HF's ``resume_download=True``. (Rationale
kept here so the regression guard in
``tests/regressions/test_platform_misc.py::TestDaemonThreadRationaleDocumented``
that introspects ``inspect.getsource(service)`` still finds it.)
"""

import logging
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


# ── : TypedDicts for the most critical ``dict`` returns ──
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
    # The tray-tooltip reason for the current state. MUST stay in lockstep
    # with ``status``: renderer sync paths derive BOTH the ERROR pill and
    # the Home description line from this pair (diverging them re-opens the
    # intermittent "ERROR pill with normal dictate hint" bug).
    message: str
    xruns_since_start: int
    loaded_via: str
    config_dir: str
    offline_pack: dict[str, object]


# the four ``DownloadXxx`` TypedDicts + ``DownloadResult`` union
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

     (split): all domain methods live on the composed mixins
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
        #  delegate config side-effects + apply_config to
        # the extracted ConfigApplier ( to_filter_dict +
        # save_strict()). The previous inline copies were never wired up.
        # ConfigApplier is the single owner of the config-mutation lock
        # acquisition + rollback logic (//) so the
        # regression test ``tests/regressions/test_concurrency.py`` can
        # introspect ``ConfigApplier.apply_config`` for the lock.
        self._config_applier = ConfigApplier(self)
        # delegate state initialisation to the owning mixins
        # (instead of having the base class own state for 3 separate
        # concerns — ModelMixin's download-cancel + model-status-cache
        # state, MicrophoneTestMixin's microphones-cache state). Each
        # mixin's ``__init__`` initialises ONLY its own state, so the
        # base class is no longer a fat owner of mixin-specific fields.
        # The mixin ``__init__`` methods are called explicitly (rather
        # than via cooperative ``super().__init__()`` chaining) because
        # ``ServiceMixinBase`` in ``_base.py`` doesn't define an
        # ``__init__`` that accepts the ``app`` argument — cooperative
        # MI would require modifying ``_base.py``. Functionally
        # equivalent: the state ends up on the same instance via the
        # same MRO.
        #
        # the  fix was previously applied
        # INCONSISTENTLY — only ``MicrophoneTestMixin`` got its own
        # ``__init__`` extraction. ``ModelMixin``'s six state fields
        # (``_download_cancel_events``, ``_download_cancel_lock``,
        # ``_active_download_id``, ``_model_status_cache``,
        # ``_model_status_cache_ts``, ``_model_status_cache_lock``)
        # were still being initialised inline here. They are now owned
        # by ``ModelMixin.__init__`` so each mixin is the single source
        # of truth for its own state — mirroring the
        # ``MicrophoneTestMixin`` pattern.
        ModelMixin.__init__(self)
        # ``_onboarding`` holds the live :class:`OnboardingController`
        # between :meth:`OnboardingMixin.onboarding_start` and
        # :meth:`OnboardingMixin.onboarding_apply`. Initialise to
        # ``None`` so the ``getattr(self, "_onboarding", None)``
        # defensive reads in ``service/onboarding.py`` resolve to a
        # typed value (and so the ClassVar annotation on
        # :class:`ServiceMixinBase` is honoured at runtime).
        self._onboarding = None
        # ``_microphones_cache`` initialised to ``None``.
        MicrophoneTestMixin.__init__(self)

    #  (High, partial): ``set_config`` and ``save_config``
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
    #     removed in   ``Config.save()`` is now invoked
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

    # ── Config side effects () ──────────────────────────


__all__ = [
    "APP_NAME",
    "ConfigApplier",
    "ForceCancelResult",
    "StatusResponse",
    "VoiceTyperService",
    "_MODEL_STATUS_CACHE_TTL_S",
]
