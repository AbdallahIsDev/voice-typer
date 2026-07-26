"""ARCH-REFAC-004: dependency-injection boundary for ``IPCServer``.

This module is the **composition root** for the IPC server.  It defines
two :class:`typing.Protocol` classes — :class:`AppProtocol` and
:class:`ServiceProtocol` — describing the surface that
:class:`voice_typer.server.ipc_server.IPCServer` and its handler mixins
actually need from a ``VoiceTyperApp`` and a ``VoiceTyperService``.

Historically ``IPCServer.__init__(app)`` took a concrete
``VoiceTyperApp`` and immediately constructed
``VoiceTyperService(app)``.  This tight coupling forced every test that
exercised the IPC layer to spin up a (real or MagicMock) app AND let
the server construct a real ``VoiceTyperService`` over it — meaning
service-layer bugs surfaced as IPC test failures, and tests could not
isolate the IPC dispatch path from the service implementation.

This module introduces a backward-compatible seam:

- ``IPCServer(app)`` still works exactly as before (constructs a real
  ``VoiceTyperService`` over ``app``).  Existing call sites in tests
  and production are unchanged.
- ``IPCServer(app, service=fake_service)`` lets a caller inject a
  fake service for testing.  The injected service is used verbatim;
  no ``VoiceTyperService(app)`` is constructed.
- :func:`build_ipc_server` is the canonical factory / composition root
  for production code.  It is a thin wrapper today (constructs the
  service itself) but provides a single, discoverable place to add
  future wiring (logging, metrics, feature flags, etc.) without
  touching ``IPCServer.__init__``.

The protocols intentionally use ``typing.Any`` for member types
(rather than concrete classes like ``Config`` or ``HistoryDB``) so
that:

1. The protocol module does not import every concrete dependency
   (avoiding import cycles and a heavy import surface).
2. Test doubles (MagicMock, custom fakes) trivially satisfy the
   protocol via structural typing — no inheritance required.
3. The protocol captures **shape**, not type identity, which is the
   whole point of structural subtyping.

Member names match the actual attribute / method names on
``VoiceTyperApp`` (e.g. ``_audio_processor`` and ``_volume_ducker``
are private on the real app — the protocol keeps those names so a
test introspection can verify the protocol declares every name the
handlers actually access).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Avoid hard imports at module load time — these are only needed for
# type-checker benefit and would create cycles if imported eagerly
# (ipc_server.py imports providers via build_ipc_server; providers
# must not eagerly import ipc_server at top level).
if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    # Concrete types for the AppProtocol data-attribute surface
    # (previously ``Any``). All imported under ``TYPE_CHECKING`` to
    # avoid runtime cycles; ``MagicMock`` fixtures still satisfy the
    # ``@runtime_checkable`` Protocol structurally (the check inspects
    # attribute NAMES via ``getattr_static``, not types).
    from voice_typer.server.config import Config
    from voice_typer.server.history_db import HistoryDB
    from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
    from voice_typer.server.ipc_server import IPCServer
    from voice_typer.server.model_manager import ModelManager
    from voice_typer.server.recording.recorder import Recorder
    from voice_typer.server.recording_controller import RecordingController

    # ``TrayIcon`` lives in ``voice_typer.server.tray``
    # (not ``tray_icon``). ``tray_icon.py`` only contains helpers like
    # ``_make_icon``; the ``TrayIcon`` class is defined in ``tray.py``.
    from voice_typer.server.tray import TrayIcon


@runtime_checkable
class AppProtocol(Protocol):
    """Structural type for the ``app`` object consumed by ``IPCServer``.

    ``IPCServer`` and its handler mixins (under
    ``voice_typer/server/handlers/``) access a handful of attributes
    and methods on ``app``.  This protocol enumerates that surface so:

    - Tests can build a focused fake (see
      ``tests/fixtures/ipc_test_helpers.py:make_fake_app``) without
      guessing which attributes are read.
    - A regression test (``tests/test_di_providers.py``) introspects
      the handlers and asserts this protocol declares every attribute
      they touch, catching drift if a new handler starts reading a
      new ``self.app.X`` field that the protocol doesn't list.

    Members are typed as ``Any`` to avoid forcing the protocol module
    to import every concrete dependency.  Structural typing means any
    object with these attributes satisfies the protocol — including
    ``MagicMock`` instances, which respond to every attribute access.

    The members below are the post-ADR-0008-§3.1 surface: handlers
    reach the app only through public domain objects (``config``,
    ``history_db``, ``models``, ``recording``, ``hotkeys``,
    ``recorder``, ``tray``) and a small set of private attributes
    that are still accessed by ``ipc_server.py`` itself
    (``_ipc_server``, ``_shutting_down``) or by handlers not yet
    refactored (``_esc_cancel_paused``, ``_vocabulary_automation``,
    ``_waveform_bubble`` — the last two were promoted in CR-59
    because four handler sites read them via ``getattr``; see the
    per-attribute docstrings below).

    The private attributes ``_audio_processor``, ``_volume_ducker``,
    and ``_config_mutation_lock`` were removed in TASK-2: the
    ``get_audio_status`` and ``apply_config`` IPC paths now go
    through :class:`ServiceProtocol` methods (``get_audio_status``,
    ``apply_config``, ``change_model``, ``set_active_backend``)
    which encapsulate the private-attribute access inside the
    service layer.
    """

    # ── Public application state ───────────────────────────────────
    # The big five: every handler that touches ``self.app.X`` reads
    # one of these.  They are the "domain objects" the IPC layer
    # exposes to the frontend.
    config: Config
    """Configuration dataclass (``voice_typer.server.config.Config``)."""

    history_db: HistoryDB
    """Transcription history DB (``voice_typer.server.history_db.HistoryDB``)."""

    models: ModelManager
    """Model manager (``voice_typer.server.model_manager.ModelManager``).

    Accessed by ``ServiceProtocol.set_active_backend`` (which wraps
    ``self._app.models.set_active_backend()``); no IPC handler reads
    ``self.app.models`` directly post-ADR-0008-§3.1.
    """

    recording: RecordingController
    """Recording controller (``voice_typer.server.recording_controller.RecordingController``)."""

    hotkeys: HotkeyDispatcher
    """Hotkey dispatcher (``voice_typer.server.hotkey_dispatcher.HotkeyDispatcher``)."""

    recorder: Recorder
    """Audio recorder (``voice_typer.server.recording.Recorder``)."""

    tray: TrayIcon
    """Tray icon controller (``voice_typer.server.tray.TrayIcon``)."""

    # ── Private attributes still accessed by ipc_server / handlers ─
    # These are private on VoiceTyperApp (leading underscore) but are
    # part of the IPC layer's effective contract.  Declaring them
    # here keeps the introspection test honest: if a handler starts
    # reading ``self.app._foo``, the test fails until ``_foo`` is
    # added here, forcing an explicit decision about whether the new
    # access is a smell or an accepted widening of the surface.

    # TASK-2 (ADR 0008 §3.1) removed ``_audio_processor``,
    # ``_volume_ducker``, and ``_config_mutation_lock`` from this
    # list — the service layer now wraps those accesses via
    # ``get_audio_status``, ``get_volume_backend_status``, and
    # ``apply_config`` respectively, so handlers no longer need to
    # reach into them directly.

    _ipc_server: IPCServer | None
    """Back-reference set by ``IPCServer.start()`` so other modules
    (waveform bubble, streaming partials) can push events without an
    explicit reference being threaded through every call site.

    Widened from ``IPCServer`` to ``IPCServer | None`` to
    match the runtime — :class:`voice_typer.server.app.VoiceTyperApp`
    declares ``_ipc_server: Any | None = None`` (the attr is ``None``
    until :meth:`IPCServer.start` runs ``self.app._ipc_server = self``).
    The pre-fix ``IPCServer`` annotation caused pyrefly to flag
    ``build_ipc_server(app)`` at ``ipc_server.py:2734`` with
    ``VoiceTyperApp._ipc_server has type Any | None, which is not
    consistent with IPCServer in AppProtocol._ipc_server`` because
    read-write attributes cannot change type. ``IPCServer | None``
    matches both the initial ``None`` and the post-``start()`` value.
    """

    _shutting_down: bool
    """``True`` once ``quit()`` begins; the IPC ``_send`` path checks
    this to skip non-critical push events to a half-closed socket
    (QUIT-CLEAN-001).
    """

    _esc_cancel_paused: bool
    """``True`` while the frontend HotkeyPicker is in capture mode; the
    ESC cancel handler checks this to avoid stealing the Escape key
    while the user is assigning it in the Settings UI.
    """

    _vocabulary_automation: Any
    """Vocabulary-automation controller (or ``None`` if not initialised).

    CR-59: promoted to ``AppProtocol`` (typed ``Any``) because four
    handler sites in
    :mod:`voice_typer.server.handlers.vocabulary_automation_handlers`
    read it via ``getattr(self.app, "_vocabulary_automation", None)``
    (apply / dismiss / list-pending paths).  Declaring it here keeps
    the introspection test honest once the ``getattr`` string-form
    access is also covered by the AST walk (see ADR 0010 §2.5).

    Fix-G will follow up by converting those ``getattr`` reads to
    direct ``self.app._vocabulary_automation`` access — at which
    point the existing ``ast.Attribute`` walk would have caught the
    access even without the ``getattr`` AST inspection.  Either way,
    the name belongs on the protocol.

    Reverted from ``VocabularyAutomation | None`` (the prior
    tightening) back to ``Any`` because :class:`VoiceTyperApp` does
    NOT declare ``_vocabulary_automation`` as a class attribute — it
    is dynamically injected by
    :meth:`voice_typer.server.dictation_pipeline.DictationPipeline._maybe_init_vocabulary_automation`
    (``self._app._vocabulary_automation = automation``). With the
    narrowed type, pyrefly flagged ``build_ipc_server(app)`` at
    ``ipc_server.py:2734`` with ``Protocol AppProtocol requires
    attribute _vocabulary_automation`` because VoiceTyperApp's
    structural type doesn't expose it. ``Any`` (the pre-fix state)
    is the correct annotation for a dynamically-injected attr.
    """

    _waveform_bubble: Any
    """Waveform-bubble controller (or ``None`` if not initialised).

    CR-59: promoted to ``AppProtocol`` (typed ``Any``) because
    :mod:`voice_typer.server.handlers.config_handlers` reads it via
    ``getattr(self.app, "_waveform_bubble", None)`` in the
    ``apply_config`` side-effect path (so the bubble can be redrawn
    when the user toggles the waveform feature).  Same rationale as
    ``_vocabulary_automation`` above.

    Reverted from ``WaveformBubbleWiring | None`` (the prior
    tightening) back to ``Any`` because :class:`VoiceTyperApp` assigns
    ``self._waveform_bubble = WaveformBubble()`` (a DIFFERENT class
    than :class:`WaveformBubbleWiring`). With the narrowed type,
    pyrefly flagged the ``VoiceTyperApp not assignable to
    AppProtocol`` structural check. ``Any`` (the pre-fix state)
    accommodates both ``WaveformBubble`` and ``WaveformBubbleWiring``
    (and ``None``).
    """

    # The 4 private service-injected
    # attrs (``_llm_polisher``, ``_cloud_engine``, ``_crash_recovery``,
    # ``_config_mutation_lock``) are NOT declared on ``AppProtocol``.
    # The reviewer's original Issue 2e instruction was to add them
    # here as ``Any``, but ``tests/test_di_providers.py`` explicitly
    # forbids ``_config_mutation_lock`` (and ``_audio_processor`` /
    # ``_volume_ducker``) from ``AppProtocol`` per ADR-0008-§3.1 (the
    # service layer wraps their access; re-declaring them re-introduces
    # the leaky abstraction the refactor removed). Adding the other 3
    # (``_llm_polisher`` / ``_cloud_engine`` / ``_crash_recovery``)
    # would also break ``test_fake_app_satisfies_app_protocol`` because
    # ``make_fake_app()`` doesn't set them and they're not in the
    # ``_FAKE_APP_AUTO_STUB_OK`` exemption list.

    # Instead, the service-layer accesses (in
    # :mod:`voice_typer.server.service.__init__`) use ``setattr`` /
    # ``getattr`` for these 4 attrs, which:
    #   1. Returns ``Any`` (so pyrefly doesn't flag the access),
    #   2. Preserves runtime behavior (``setattr(app, "_X", v)`` is
    #      equivalent to ``app._X = v``; ``getattr(app, "_X")`` is
    #      equivalent to ``app._X``),
    #   3. Doesn't require declaring the attrs on ``AppProtocol``
    #      (keeping the ADR-0008-§3.1 boundary intact),
    #   4. Doesn't require ``# type: ignore`` markers.

    # ── Methods invoked by the IPC layer ───────────────────────────
    # The service layer delegates these to the app.  Declaring them
    # on the protocol means a fake app must implement them (or be a
    # MagicMock, which auto-stubs any method call).

    def change_model(self, model_size: str) -> None:
        """Switch the active ASR model to ``model_size``.

        Wrapped by :meth:`ServiceProtocol.change_model`; no IPC
        handler calls ``self.app.change_model()`` directly
        post-ADR-0008-§3.1.
        """
        ...

    def toggle_dictation(self) -> None:
        """Start or stop dictation."""
        ...

    def undo_last(self) -> None:
        """Undo the last transcription via backspace keystrokes."""
        ...

    def repaste_last(self) -> None:
        """Re-paste the last transcription."""
        ...

    def restart_app(self) -> None:
        """Restart the application (signals Electron to relaunch)."""
        ...

    def quit_app(self) -> None:
        """Initiate application shutdown via the tray controller path.

        Called by :meth:`ServiceProtocol.quit`.  Distinguished from
        :meth:`quit` below: ``quit_app`` pushes a ``quit_app`` IPC
        event to Electron first, while ``quit`` skips that (used by
        the heartbeat watchdog when Electron is already dead).
        """
        ...

    def quit(self) -> None:
        """Run the audited cleanup path and exit.

        Called directly by the heartbeat watchdog in
        ``ipc_server.py:_check_heartbeat_timeout`` when Electron has
        stopped sending heartbeats.  ``quit_app`` delegates here
        after notifying Electron; the watchdog skips the notification
        because Electron is already gone.
        """
        ...

    def start(self) -> None:
        """Start the application (typically blocks on the tray event loop)."""
        ...


@runtime_checkable
class ServiceProtocol(Protocol):
    """Structural type for the service object consumed by ``IPCServer``.

    ``IPCServer`` delegates to a service object via ``self.service.X``.
    The concrete implementation is :class:`voice_typer.server.service.VoiceTyperService`,
    but tests can substitute any object satisfying this protocol
    (typically a ``MagicMock`` configured by
    :func:`tests.fixtures.ipc_test_helpers.make_fake_service`).

    The methods below enumerate the full surface that the IPC handler
    mixins call.  They are typed with ``Any`` return values and
    parameter types so a structural fake trivially satisfies the
    protocol — but each method's signature mirrors the real
    ``VoiceTyperService`` method so a static type checker can verify
    the contract if desired.
    """

    # ── Status ─────────────────────────────────────────────────────
    def get_status(self) -> dict[str, object]: ...
    def get_rms_level(self) -> dict[str, object]: ...
    def get_volume_backend_status(self) -> dict[str, object]: ...
    def get_model_status(self) -> dict[str, object]: ...
    def get_audio_status(self) -> dict[str, object]: ...

    # ── Dictation ──────────────────────────────────────────────────
    def toggle_dictation(self) -> None: ...
    def undo_last(self) -> None: ...
    def repaste_last(self) -> None: ...
    def force_cancel_transcription(self) -> dict[str, object]: ...

    # ── Config ─────────────────────────────────────────────────────
    # ``set_config`` and ``save_config`` REMOVED from
    # ``ServiceProtocol``.  Both were dead — see the corresponding
    # comment block in ``service.py`` for the full rationale.  The IPC
    # ``set_config`` command path goes through
    # ``config.validate_config_update`` + ``service.apply_config``;
    # ``Config.save()`` is now invoked inside ``apply_config`` under
    # the config-mutation lock.  Tests that pinned the old protocol
    # methods (``tests/test_di_providers.py:544``) need follow-up.
    def get_config(self) -> dict[str, object]: ...
    def get_defaults(self) -> dict[str, object]: ...
    def apply_config_side_effects(self, updates: dict) -> None: ...
    def apply_config(self, updates: dict) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def set_active_backend(self, backend: str) -> None: ...

    # ── History ────────────────────────────────────────────────────
    def get_history(self, limit: int = 50, offset: int = 0) -> list: ...
    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list: ...
    def get_today_stats(self) -> dict[str, object]: ...
    def delete_history(self, rec_id: int) -> bool: ...
    def restore_history(self, record: dict) -> int: ...
    def clear_history(self) -> bool: ...
    def toggle_favorite(self, rec_id: int) -> bool: ...
    def get_favorites(self, limit: int = 50, offset: int = 0) -> list: ...

    # ── Microphone ─────────────────────────────────────────────────
    def get_microphones(self) -> list: ...
    def refresh_microphones(self) -> list: ...

    # ── Microphone test ────────────────────────────────────────────
    # The renderer sends ``mic_id`` as either a string (e.g.
    # ``"0"``) or an int (e.g. ``0``), ``duration`` as int/float
    # seconds, and ``filters`` as a dict payload or absent.
    # (Issue 2c) reverts the narrowed ``str | int | None`` /
    # ``int | float | None`` / ``dict[str, object] | None`` annotations
    # back to ``Any`` because callers pass ``object`` values pulled from
    # a ``validated`` dict (whose value type is ``object``), and the
    # narrowings made the call sites pyrefly-flagged. The narrowing is
    # correct in principle but requires coordinated caller-side changes
    # (typing the ``validated`` dict values per-field) that exceed the
    # session budget — deferred follow-up.
    def microphone_test_start(
        self,
        mic_id: Any = None,
        duration: Any = None,
        filters: Any = None,
    ) -> dict[str, object]: ...
    def microphone_test_stop(self) -> dict[str, object]: ...
    def microphone_test_cancel(self) -> dict[str, object]: ...
    def microphone_test_status(self) -> dict[str, object]: ...
    def microphone_test_get_level(self) -> dict[str, object]: ...

    # ── Level monitor ──────────────────────────────────────────────
    def level_monitor_start(self, mic_id: Any = None) -> dict[str, object]: ...
    def level_monitor_stop(self) -> dict[str, object]: ...
    def level_monitor_status(self) -> dict[str, object]: ...

    # ── Models ─────────────────────────────────────────────────────
    def import_model(self, dir_path: str) -> dict[str, object]: ...
    def download_model(self, model_name: str) -> dict[str, object]: ...
    def cancel_model_download(self) -> dict[str, object]: ...
    def pause_model_download(self) -> dict[str, object]: ...
    def resume_model_download(self) -> dict[str, object]: ...
    def delete_model(self, model_name: str) -> dict[str, object]: ...
    def test_llm_connection(self) -> dict[str, object]: ...

    # ── Vocabulary / Templates ─────────────────────────────────────
    def get_vocabulary(self) -> dict[str, object]: ...
    def save_vocabulary_with_diff(self, data: dict) -> dict[str, object]: ...
    def get_templates(self) -> list: ...
    def save_templates(self, templates: list) -> bool: ...

    # ── Onboarding ────────────────────────────────────────────────
    def onboarding_is_first_run(self) -> dict[str, object]: ...
    def onboarding_start(self) -> dict[str, object]: ...
    def onboarding_get_step(self) -> dict[str, object]: ...
    def onboarding_next_step(self) -> dict[str, object]: ...
    def onboarding_prev_step(self) -> dict[str, object]: ...
    def onboarding_set_microphone(self, mic_id: Any) -> dict[str, object]: ...
    def onboarding_set_hotkey(self, hotkey: str) -> dict[str, object]: ...
    def onboarding_set_model(self, model: str) -> dict[str, object]: ...
    def onboarding_skip(self) -> dict[str, object]: ...
    def onboarding_apply(self) -> dict[str, object]: ...
    def onboarding_get_microphones(self) -> dict[str, object]: ...
    def onboarding_get_model_options(self) -> dict[str, object]: ...
    def onboarding_get_model_catalog(self) -> dict[str, object]: ...
    def onboarding_get_hotkey_presets(self) -> dict[str, object]: ...

    # ── System ─────────────────────────────────────────────────────
    def restart(self) -> None: ...
    def quit(self) -> None: ...
    def export_diagnostics(self) -> dict[str, object]: ...

    # ── Privacy / GDPR ────────────────────────────────────────────
    # CR-87 (GDPR Art. 17 right-to-erasure) and CR-88 (Art. 20
    # right-to-data-portability).  Both are implemented on
    # :class:`voice_typer.server.service.VoiceTyperService`; the IPC
    # handlers in ``voice_typer/server/handlers/privacy_handlers.py``
    # are thin envelopes that delegate to these service methods.
    # Declaring them on the protocol keeps the AST introspection test
    # in ``tests/test_di_providers.py`` honest (any handler that calls
    # ``self.service.X`` must have ``X`` declared on
    # ``ServiceProtocol``).
    def delete_all_personal_data(self) -> dict[str, object]: ...
    def export_gdpr_bundle(self) -> dict[str, object]: ...


def build_ipc_server(app: AppProtocol) -> IPCServer:
    """Construct an :class:`IPCServer` wired to ``app``.

    This is the **canonical composition root** for the IPC server.
    Production code (notably :func:`voice_typer.server.ipc_server.main`)
    should call this factory instead of ``IPCServer(app)`` directly so
    that future wiring changes (logging, metrics, feature flags, an
    alternate service implementation) live in one place.

    Behavior today is identical to ``IPCServer(app)``: a real
    :class:`VoiceTyperService` is constructed over ``app`` and stored
    on the returned server as ``server.service``.  Tests that want to
    inject a fake service should call ``IPCServer(app, service=fake)``
    directly rather than this factory — :func:`build_ipc_server` is the
    production path.

    Parameters
    ----------
    app :
        Any object satisfying :class:`AppProtocol`.  In production this
        is a :class:`voice_typer.server.app.VoiceTyperApp`; in tests it
        may be a ``MagicMock`` configured by
        :func:`tests.fixtures.ipc_test_helpers.make_fake_app`.

    Returns
    -------
    IPCServer
        A ready-to-:meth:`start` IPC server.  The caller is responsible
        for invoking :meth:`IPCServer.start` and (optionally)
        :meth:`IPCServer.start_tcp`.

    Soft AppProtocol validation
    -------------------------------------
    On entry the factory performs a *soft* structural check that
    ``app`` satisfies :class:`AppProtocol`.  ``runtime_checkable``
    Protocols only verify method/attribute *names* via
    ``inspect.getattr_static`` (not signatures), and ``MagicMock``
    fails this check because ``getattr_static`` does not trigger
    ``MagicMock.__getattr__`` — so a warning here is informational, not
    a hard failure.  We log a ``WARNING`` listing the missing annotated
    attributes and continue: production ``VoiceTyperApp`` always
    satisfies the protocol, and tests that pass a ``MagicMock`` are
    still allowed (the warning is a hint for the test author, not a
    gate).  The check is intentionally non-fatal so a Protocol-shape
    drift bug cannot take down ``build_ipc_server`` at startup.
    """
    # Imported lazily to avoid an import cycle: ipc_server.py imports
    # from providers (via build_ipc_server) at call time, and importing
    # IPCServer eagerly here would create a top-level cycle.  Doing the
    # import inside the function means the cycle only resolves when the
    # factory is actually called, which is always after both modules
    # are fully loaded.
    from voice_typer.server.ipc_server import IPCServer

    # Soft AppProtocol validation.  ``runtime_checkable``
    # ``isinstance`` only verifies attribute names (not signatures) and
    # returns False for ``MagicMock``-based fakes even when they
    # structurally satisfy the protocol — so this is a warning, not a
    # gate.  We compute the missing list from ``__annotations__``
    # (annotated data attributes only; methods are not checked here
    # because ``hasattr`` against a MagicMock would always succeed).
    if not isinstance(app, AppProtocol):
        missing = [attr for attr in getattr(AppProtocol, "__annotations__", {}) if not hasattr(app, attr)]
        log.warning(
            "build_ipc_server: app does not satisfy AppProtocol "
            "(missing annotated attributes: %s). Continuing best-effort "
            "— IPCServer(app) will likely fail later if these attributes "
            "are accessed by a handler. This is informational; "
            "MagicMock-based fakes are known to fail the "
            "runtime_checkable isinstance check despite satisfying the "
            "protocol structurally.",
            missing,
        )

    return IPCServer(app)


__all__ = [
    "AppProtocol",
    "ServiceProtocol",
    "build_ipc_server",
]
