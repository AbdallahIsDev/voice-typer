"""Provider registry entries for cloud engines."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

log = logging.getLogger(__name__)

# Avoid hard imports at module load time, these are only needed for
if TYPE_CHECKING:  # pragma: no cover - type-checker-only
    # ``SideEffectStatus`` is the TypedDict contract of the
    from voice_typer.server.config import Config
    from voice_typer.server.config_applier import SideEffectStatus
    from voice_typer.server.correction_usage import CorrectionUsageTracker
    from voice_typer.server.history_db import HistoryDB
    from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
    from voice_typer.server.ipc_server import IPCServer
    from voice_typer.server.model_manager import ModelManager
    from voice_typer.server.recording.recorder import Recorder
    from voice_typer.server.recording_controller import RecordingController

    # ``TrayIcon`` lives in ``voice_typer.server.tray``
    from voice_typer.server.tray import TrayIcon


@runtime_checkable
class AppProtocol(Protocol):
    """Structural type for the ``app`` object consumed by ``IPCServer``."""

    # The big five: every handler that touches ``self.app.X`` reads
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

    @property
    def correction_usage(self) -> CorrectionUsageTracker:
        """Per-correction usage tracker (``voice_typer.server.correction_usage.CorrectionUsageTracker``).

        Shared with the live ``VocabularyManager`` so dictation records
        corrections + dictations into ONE counter; the vocabulary service
        reads it for the ``get_correction_usage`` IPC path and calls
        ``prune_entries`` after a vocabulary save.

        Declared as a read-only property to match ``LausuApp``'s
        ``@property`` accessor, a settable-attribute declaration makes
        the concrete app fail structural assignability (mypy: "expected
        settable variable, got read-only attribute").
        """
        ...

    # ── Private attributes still accessed by ipc_server / handlers ─

    # (ADR 0008 §3.1) removed ``_audio_processor``,

    _ipc_server: IPCServer | None
    """Back-reference set by ``IPCServer.start()`` so other modules
    (waveform bubble, streaming partials) can push events without an
    explicit reference being threaded through every call site.

    Widened from ``IPCServer`` to ``IPCServer | None`` to
    match the runtime, :class:`voice_typer.server.app.LausuApp`
    declares ``_ipc_server: Any | None = None`` (the attr is ``None``
    until :meth:`IPCServer.start` runs ``self.app._ipc_server = self``).
    The pre-fix ``IPCServer`` annotation caused pyrefly to flag
    ``build_ipc_server(app)`` at ``ipc_server.py:2734`` with
    ``LausuApp._ipc_server has type Any | None, which is not
    consistent with IPCServer in AppProtocol._ipc_server`` because
    read-write attributes cannot change type. ``IPCServer | None``
    matches both the initial ``None`` and the post-``start()`` value.
    """

    _shutting_down: bool
    """``True`` once ``quit()`` begins; the IPC ``_send`` path checks
    this to skip non-critical push events to a half-closed socket
    during shutdown.
    """

    _esc_cancel_paused: bool
    """``True`` while the frontend HotkeyPicker is in capture mode; the
    ESC cancel handler checks this to avoid stealing the Escape key
    while the user is assigning it in the Settings UI.
    """

    _vocabulary_automation: Any
    """Vocabulary-automation controller (or ``None`` if not initialised).

    Promoted to ``AppProtocol`` (typed ``Any``) because four
    handler sites in
    :mod:`voice_typer.server.handlers.vocabulary_automation_handlers`
    read it via ``getattr(self.app, "_vocabulary_automation", None)``
    (apply / dismiss / list-pending paths).  Declaring it here keeps
    the introspection test honest once the ``getattr`` string-form
    access is also covered by the AST walk (see ADR 0010 §2.5).

    Fix-G will follow up by converting those ``getattr`` reads to
    direct ``self.app._vocabulary_automation`` access, at which
    point the existing ``ast.Attribute`` walk would have caught the
    access even without the ``getattr`` AST inspection.  Either way,
    the name belongs on the protocol.

    Reverted from ``VocabularyAutomation | None`` (the prior
    tightening) back to ``Any`` because :class:`LausuApp` does
    NOT declare ``_vocabulary_automation`` as a class attribute, it
    is dynamically injected by
    :meth:`voice_typer.server.dictation_pipeline.DictationPipeline._maybe_init_vocabulary_automation`
    (``self._app._vocabulary_automation = automation``). With the
    narrowed type, pyrefly flagged ``build_ipc_server(app)`` at
    ``ipc_server.py:2734`` with ``Protocol AppProtocol requires
    attribute _vocabulary_automation`` because LausuApp's
    structural type doesn't expose it. ``Any`` (the pre-fix state)
    is the correct annotation for a dynamically-injected attr.
    """

    _waveform_bubble: Any
    """Waveform-bubble controller (or ``None`` if not initialised).

    Promoted to ``AppProtocol`` (typed ``Any``) because
    :mod:`voice_typer.server.handlers.config_handlers` reads it via
    ``getattr(self.app, "_waveform_bubble", None)`` in the
    ``apply_config`` side-effect path (so the bubble can be redrawn
    when the user toggles the waveform feature).  Same rationale as
    ``_vocabulary_automation`` above.

    Reverted from ``WaveformBubbleWiring | None`` (the prior
    tightening) back to ``Any`` because :class:`LausuApp` assigns
    ``self._waveform_bubble = WaveformBubble()`` (a DIFFERENT class
    than :class:`WaveformBubbleWiring`). With the narrowed type,
    pyrefly flagged the ``LausuApp not assignable to
    AppProtocol`` structural check. ``Any`` (the pre-fix state)
    accommodates both ``WaveformBubble`` and ``WaveformBubbleWiring``
    (and ``None``).
    """

    # The 4 private service-injected

    #   4. Doesn't require ``# type: ignore`` markers.

    # The service layer delegates these to the app.  Declaring them

    def change_model(self, model_size: str) -> None:
        """Switch the active ASR model to ``model_size``."""
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
        """Restart the application (signals predecessor to relaunch)."""
        ...

    def quit_app(self) -> None:
        """Initiate application shutdown via the tray controller path."""
        ...

    def quit(self) -> None:
        """Run the audited cleanup path and exit."""
        ...

    def start(self) -> None:
        """Start the application (typically blocks on the tray event loop)."""
        ...

    def push_bubble_config(self, config: Any) -> None:
        """Push a config-changed event to the waveform bubble renderer.

        Public replacement for the private ``getattr(self.app,
                "_waveform_bubble", None)`` access in
                :mod:`voice_typer.server.handlers.config_handlers` with a
                public method on the app. The implementation on
                :class:`voice_typer.server.app.LausuApp` preserves the
                exact behavior of the prior inline block: it reads
                ``self._waveform_bubble`` (which may be ``None`` before
                ``_wire_waveform_bubble`` runs) and, if both the bubble and
                its ``on_config`` callback are non-None, invokes
                ``bubble.on_config(config)`` so the sandboxed bubble renderer
                re-reads ``bubble_behavior`` / ``bubble_click_to_toggle`` /
                ``bubble_mic_button`` and redraws. The ``config`` argument is
                the app's :class:`Config` object (the same value the prior
                inline block passed as ``self.app.config``).
        """
        ...


# These replace the bare ``list`` return annotations on


class HistoryEntry(TypedDict):
    """One row of ``get_history`` / ``search_history`` / ``get_favorites``."""

    id: int
    text: str
    text_full_length: int
    text_truncated: bool
    timestamp: str
    duration: float
    model: str
    device: str
    word_count: int
    char_count: int
    favorite: int
    language: str


class MicrophoneEntry(TypedDict):
    """One input device dict from ``get_microphones`` / ``refresh_microphones``."""

    id: str
    index: int
    name: str
    host_api: str
    channels: int
    default: bool
    is_bluetooth: bool


class TemplateEntry(TypedDict):
    """One saved template from ``get_templates``."""

    trigger: str
    output: str
    match_mode: str


@runtime_checkable
class ServiceProtocol(Protocol):
    """Structural type for the service object consumed by ``IPCServer``."""

    def get_status(self) -> dict[str, object]: ...
    def get_rms_level(self) -> dict[str, object]: ...
    def get_volume_backend_status(self) -> dict[str, object]: ...
    def get_model_status(self) -> dict[str, object]: ...
    def get_audio_status(self) -> dict[str, object]: ...

    def toggle_dictation(self) -> None: ...
    def undo_last(self) -> None: ...
    def repaste_last(self) -> None: ...
    def force_cancel_transcription(self) -> dict[str, object]: ...

    # ``set_config`` and ``save_config`` REMOVED from
    def get_config(self) -> dict[str, object]: ...
    def get_defaults(self) -> dict[str, object]: ...
    # and forced callers into ``# type: ignore`` or silent-discards.
    def apply_config_side_effects(self, updates: dict) -> SideEffectStatus: ...
    def apply_config(self, updates: dict) -> SideEffectStatus: ...
    def change_model(self, model_size: str) -> None: ...
    def set_active_backend(self, backend: str) -> None: ...

    def get_history(self, limit: int = 50, offset: int = 0) -> list[HistoryEntry]: ...
    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list[HistoryEntry]: ...
    def get_today_stats(self) -> dict[str, object]: ...
    def delete_history(self, rec_id: int) -> bool: ...
    def restore_history(self, record: dict) -> int: ...
    def clear_history(self) -> bool: ...
    def toggle_favorite(self, rec_id: int) -> bool: ...
    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[HistoryEntry]: ...
    def get_history_count(self) -> int: ...
    def get_transcription_text(self, transcription_id: int) -> dict[str, object]: ...

    def get_microphones(self) -> list[MicrophoneEntry]: ...
    def refresh_microphones(self) -> list[MicrophoneEntry]: ...

    # narrowed from ``Any`` to concrete unions matching the
    def microphone_test_start(
        self,
        mic_id: str | None = None,
        duration: float = 10.0,
        filters: dict | None = None,
    ) -> dict[str, object]: ...
    def microphone_test_stop(self) -> dict[str, object]: ...
    def microphone_test_read_audio(self, path: str, offset: int, length: int) -> dict[str, object]: ...
    def microphone_test_cancel(self) -> dict[str, object]: ...
    def microphone_test_status(self) -> dict[str, object]: ...
    def microphone_test_get_level(self) -> dict[str, object]: ...

    def level_monitor_start(self, mic_id: str | None = None) -> dict[str, object]: ...
    def level_monitor_stop(self) -> dict[str, object]: ...
    def level_monitor_status(self) -> dict[str, object]: ...

    def import_model(self, dir_path: str) -> dict[str, object]: ...
    def download_model(self, model_name: str) -> dict[str, object]: ...
    def cancel_model_download(self) -> dict[str, object]: ...
    def pause_model_download(self) -> dict[str, object]: ...
    def resume_model_download(self) -> dict[str, object]: ...
    def get_download_queue(self) -> dict[str, object]: ...
    def delete_model(self, model_name: str) -> dict[str, object]: ...
    def test_llm_connection(self) -> dict[str, object]: ...

    def get_vocabulary(self) -> dict[str, object]: ...
    def save_vocabulary_with_diff(self, data: dict) -> dict[str, object]: ...
    def get_correction_usage(self) -> dict[str, object]: ...
    def test_vocabulary_correction(self, text: str) -> dict[str, object]: ...
    def get_templates(self) -> list[TemplateEntry]: ...
    def save_templates(self, templates: list[dict]) -> bool: ...

    def onboarding_is_first_run(self) -> dict[str, object]: ...
    def onboarding_start(self) -> dict[str, object]: ...
    def onboarding_get_step(self) -> dict[str, object]: ...
    def onboarding_next_step(self) -> dict[str, object]: ...
    def onboarding_prev_step(self) -> dict[str, object]: ...
    def onboarding_set_microphone(self, mic_id: str | None) -> dict[str, object]: ...
    def onboarding_set_hotkey(self, hotkey: str) -> dict[str, object]: ...
    def onboarding_set_model(self, model: str) -> dict[str, object]: ...
    def onboarding_set_backend(self, backend: str) -> dict[str, object]: ...
    def onboarding_skip(self) -> dict[str, object]: ...
    def onboarding_apply(self) -> dict[str, object]: ...
    def onboarding_get_microphones(self) -> dict[str, object]: ...
    def onboarding_get_model_options(self) -> dict[str, object]: ...
    def onboarding_get_model_catalog(self) -> dict[str, object]: ...
    def onboarding_get_hotkey_presets(self) -> dict[str, object]: ...

    def restart(self) -> None: ...
    def quit(self) -> None: ...

    # ``export_diagnostics`` was removed: every IPC/Rust/TS surface for

    # (GDPR Art. 17 right-to-erasure) and  (Art. 20
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
    :class:`LausuService` is constructed over ``app`` and stored
    on the returned server as ``server.service``.  Tests that want to
    inject a fake service should call ``IPCServer(app, service=fake)``
    directly rather than this factory, :func:`build_ipc_server` is the
    production path.

    Parameters
    ----------
    app :
        Any object satisfying :class:`AppProtocol`.  In production this
        is a :class:`voice_typer.server.app.LausuApp`; in tests it
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
    ``MagicMock.__getattr__``, so a warning here is informational, not
    a hard failure.  We log a ``WARNING`` listing the missing annotated
    attributes and continue: production ``LausuApp`` always
    satisfies the protocol, and tests that pass a ``MagicMock`` are
    still allowed (the warning is a hint for the test author, not a
    gate).  The check is intentionally non-fatal so a Protocol-shape
    drift bug cannot take down ``build_ipc_server`` at startup.
    """
    # Imported lazily to avoid an import cycle: ipc_server.py imports
    from voice_typer.server.ipc_server import IPCServer

    # Soft AppProtocol validation.  ``runtime_checkable``
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
