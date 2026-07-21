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
    from voice_typer.server.ipc_server import IPCServer


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
    config: Any
    """Configuration dataclass (``voice_typer.server.config.Config``)."""

    history_db: Any
    """Transcription history DB (``voice_typer.server.history_db.HistoryDB``)."""

    models: Any
    """Model manager (``voice_typer.server.model_manager.ModelManager``).

    Accessed by ``ServiceProtocol.set_active_backend`` (which wraps
    ``self._app.models.set_active_backend()``); no IPC handler reads
    ``self.app.models`` directly post-ADR-0008-§3.1.
    """

    recording: Any
    """Recording controller (``voice_typer.server.recording_controller.RecordingController``)."""

    hotkeys: Any
    """Hotkey dispatcher (``voice_typer.server.hotkey_dispatcher.HotkeyDispatcher``)."""

    recorder: Any
    """Audio recorder (``voice_typer.server.recording.Recorder``)."""

    tray: Any
    """Tray icon controller (``voice_typer.server.tray_icon.TrayIcon``)."""

    # ── Private attributes still accessed by ipc_server / handlers ─
    # These are private on VoiceTyperApp (leading underscore) but are
    # part of the IPC layer's effective contract.  Declaring them
    # here keeps the introspection test honest: if a handler starts
    # reading ``self.app._foo``, the test fails until ``_foo`` is
    # added here, forcing an explicit decision about whether the new
    # access is a smell or an accepted widening of the surface.
    #
    # TASK-2 (ADR 0008 §3.1) removed ``_audio_processor``,
    # ``_volume_ducker``, and ``_config_mutation_lock`` from this
    # list — the service layer now wraps those accesses via
    # ``get_audio_status``, ``get_volume_backend_status``, and
    # ``apply_config`` respectively, so handlers no longer need to
    # reach into them directly.

    _ipc_server: Any
    """Back-reference set by ``IPCServer.start()`` so other modules
    (waveform bubble, streaming partials) can push events without an
    explicit reference being threaded through every call site.
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
    """

    _waveform_bubble: Any
    """Waveform-bubble controller (or ``None`` if not initialised).

    CR-59: promoted to ``AppProtocol`` (typed ``Any``) because
    :mod:`voice_typer.server.handlers.config_handlers` reads it via
    ``getattr(self.app, "_waveform_bubble", None)`` in the
    ``apply_config`` side-effect path (so the bubble can be redrawn
    when the user toggles the waveform feature).  Same rationale as
    ``_vocabulary_automation`` above.
    """

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
    def get_status(self) -> dict: ...
    def get_rms_level(self) -> dict: ...
    def get_volume_backend_status(self) -> dict: ...
    def get_model_status(self) -> dict: ...
    def get_audio_status(self) -> dict: ...

    # ── Dictation ──────────────────────────────────────────────────
    def toggle_dictation(self) -> None: ...
    def undo_last(self) -> None: ...
    def repaste_last(self) -> None: ...
    def force_cancel_transcription(self) -> dict: ...

    # ── Config ─────────────────────────────────────────────────────
    def get_config(self) -> dict: ...
    def get_defaults(self) -> dict: ...
    def set_config(self, updates: dict) -> tuple: ...
    def save_config(self) -> bool: ...
    def apply_config_side_effects(self, updates: dict) -> None: ...
    def apply_config(self, updates: dict) -> None: ...
    def change_model(self, model_size: str) -> None: ...
    def set_active_backend(self, backend: str) -> None: ...

    # ── History ────────────────────────────────────────────────────
    def get_history(self, limit: int = 50, offset: int = 0) -> list: ...
    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list: ...
    def get_today_stats(self) -> dict: ...
    def delete_history(self, rec_id: int) -> bool: ...
    def restore_history(self, record: dict) -> int: ...
    def clear_history(self) -> bool: ...
    def toggle_favorite(self, rec_id: int) -> bool: ...
    def get_favorites(self, limit: int = 50, offset: int = 0) -> list: ...

    # ── Microphone ─────────────────────────────────────────────────
    def get_microphones(self) -> list: ...
    def refresh_microphones(self) -> list: ...

    # ── Microphone test ────────────────────────────────────────────
    def microphone_test_start(self, mic_id: Any = None, duration: Any = None, filters: Any = None) -> dict: ...
    def microphone_test_stop(self) -> dict: ...
    def microphone_test_cancel(self) -> dict: ...
    def microphone_test_status(self) -> dict: ...
    def microphone_test_get_level(self) -> dict: ...

    # ── Level monitor ──────────────────────────────────────────────
    def level_monitor_start(self, mic_id: Any = None) -> dict: ...
    def level_monitor_stop(self) -> dict: ...
    def level_monitor_status(self) -> dict: ...

    # ── Models ─────────────────────────────────────────────────────
    def import_model(self, dir_path: str) -> dict: ...
    def download_model(self, model_name: str) -> dict: ...
    def cancel_model_download(self) -> dict: ...
    def pause_model_download(self) -> dict: ...
    def resume_model_download(self) -> dict: ...
    def delete_model(self, model_name: str) -> dict: ...
    def test_llm_connection(self) -> dict: ...

    # ── Vocabulary / Templates ─────────────────────────────────────
    def get_vocabulary(self) -> dict: ...
    def save_vocabulary_with_diff(self, data: dict) -> dict: ...
    def get_templates(self) -> list: ...
    def save_templates(self, templates: list) -> bool: ...

    # ── Onboarding ────────────────────────────────────────────────
    def onboarding_is_first_run(self) -> dict: ...
    def onboarding_start(self) -> dict: ...
    def onboarding_get_step(self) -> dict: ...
    def onboarding_next_step(self) -> dict: ...
    def onboarding_prev_step(self) -> dict: ...
    def onboarding_set_microphone(self, mic_id: Any) -> dict: ...
    def onboarding_set_hotkey(self, hotkey: str) -> dict: ...
    def onboarding_set_model(self, model: str) -> dict: ...
    def onboarding_skip(self) -> dict: ...
    def onboarding_apply(self) -> dict: ...
    def onboarding_get_microphones(self) -> dict: ...
    def onboarding_get_model_options(self) -> dict: ...
    def onboarding_get_model_catalog(self) -> dict: ...
    def onboarding_get_hotkey_presets(self) -> dict: ...

    # ── System ─────────────────────────────────────────────────────
    def restart(self) -> None: ...
    def quit(self) -> None: ...
    def export_diagnostics(self) -> dict: ...


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
    """
    # Imported lazily to avoid an import cycle: ipc_server.py imports
    # from providers (via build_ipc_server) at call time, and importing
    # IPCServer eagerly here would create a top-level cycle.  Doing the
    # import inside the function means the cycle only resolves when the
    # factory is actually called, which is always after both modules
    # are fully loaded.
    from voice_typer.server.ipc_server import IPCServer

    return IPCServer(app)


__all__ = [
    "AppProtocol",
    "ServiceProtocol",
    "build_ipc_server",
]
