"""AppLazyHub, the lazy-@property mixin extracted from VoiceTyperApp.

Owns every lazily-constructed subsystem accessor on ``VoiceTyperApp``:

- 3 legacy private-state back-compat delegates (``_busy_event`` / ``_lock`` /
  ``_microphones``) that forward to ``BusynessCoordinator`` /
  ``MicrophoneRegistry``;
- lazy subsystem accessors, plain @property pairs plus five
  ``LazyProperty`` descriptor accessors (plus the read-only
  ``correction_usage``) covering the recorder/recording subsystem,
  clipboard, waveform bubble, undo / audio-quality / duck-crash-recovery /
  volume-ducker / history-db controllers, the audio-processor proxy, and
  the passive template / vocabulary manager views.

Previously all of this lived on ``VoiceTyperApp`` in ``app.py`` (~640 LOC).
The behaviour is preserved verbatim, only the class boundary moved.
``VoiceTyperApp(AppLazyHub)`` inherits every property, so each attribute
name keeps resolving on instances and every existing monkeypatch seam
(``app.undo = MagicMock()``, ``app.recorder = ...``, ``app.history_db =
...`` via the setters) works unchanged. Heterogeneous accessors stay plain @property pairs; the
five identical sentinel+TTL accessors (``undo`` / ``audio_quality`` /
``_duck_crash_recovery`` / ``_volume_ducker`` / ``history_db``) collapse
into one ``LazyProperty`` data descriptor below. setattr semantics are
unchanged, the descriptor's ``__set__`` stores into the same backing
attribute the property setters used.

Sentinels + TTL (``_RECORDER_MISSING`` / ``_LAZY_FAILED`` /
``RETRY_TTL_SECONDS``) and the ``_LazyAudioProcessorProxy`` live here now
and are re-exported from ``voice_typer.server.app`` so existing imports
(and identity checks like ``backing is _LAZY_FAILED``) keep working.

A note on logging (mirrors the convention in ``app_lifecycle.py`` and
``app_undo.py``): this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__``. Tests capture the lazy-init WARNING lines
(e.g. "AudioQualityController lazy-init failed") at
``logger="voice_typer.server.app"``: using ``__name__`` would route
those logs to a different logger and break the caplog captures.

A note on the ``HistoryDB`` seam: the ``history_db`` getter resolves the
class through the ``voice_typer.server.app`` module at call time (not via
a module-top import) so the documented monkeypatch target
``voice_typer.server.app.HistoryDB`` (see
tests/test_app_lazy_properties.py) keeps intercepting construction.
"""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any

from voice_typer.server._busyness import BusynessCoordinator
from voice_typer.server._microphone_registry import MicrophoneRegistry

# Tests capture lazy-init failures at this logger name, see module
log = logging.getLogger("voice_typer.server.app")


# Sentinel for the lazily-built ``recorder`` / ``recording`` backings.
_RECORDER_MISSING: object = object()

# Sentinel + TTL for the lazy ``@property`` accessors that wrap controller
_LAZY_FAILED: object = object()
RETRY_TTL_SECONDS: float = 30.0


class _LazyAudioProcessorProxy:
    """Transparent lazy proxy for ``AudioProcessor``.

    ``VoiceTyperApp.__init__`` used to construct ``AudioProcessor``
    eagerly, which calls ``build_chain(config, sample_rate)``. That in
    turn imports the full ``audio_filters`` package (highpass ->
    ``scipy.signal.butter``, noise_suppressor -> RNNoise, etc.) on
    every cold start, even when the user never dictates.

    This proxy defers the real construction (and the transitive
    ``audio_filters`` import chain) to first attribute access. The
    proxy is what's passed to ``Recorder(audio_processor=...)`` —
    ``Recorder`` stores it as ``self._audio_processor``, and the
    audio-pipeline path (``recording/audio_pipeline.py``) checks
    ``recorder._audio_processor is not None`` before calling
    ``process_chunk``. The proxy is never ``None``, so the check
    passes; the real construction happens inside ``_resolve()`` on
    the first ``process_chunk`` / ``set_sample_rate`` /
    ``rebuild_from_config`` call.

    The proxy ALSO wires ``set_quality_callback(app._on_audio_quality_chunk)``
    immediately after construction: this wiring used to live at
    ``app.py:217`` (``self._audio_processor.set_quality_callback(
    self._on_audio_quality_chunk)``) but was moved here so the proxy
    doesn't have to be resolved eagerly just to install a callback.

    Tests that inject mocks via ``app._audio_processor = MagicMock()``
    use the ``_audio_processor`` setter, which bypasses the proxy
    entirely (the mock is stored directly in ``_audio_processor_backing``
    and the proxy is never created).
    """

    __slots__ = ("_app_ref", "_real", "_wired")

    def __init__(self, app: Any) -> None:
        # Bypass our own __setattr__ (which would delegate to the wrapped
        object.__setattr__(self, "_app_ref", weakref.ref(app))
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "_wired", False)

    def _resolve(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            app = object.__getattribute__(self, "_app_ref")()
            if app is None:
                # The owning VoiceTyperApp was garbage-collected —
                raise AttributeError("_LazyAudioProcessorProxy: owning VoiceTyperApp was garbage-collected")
            # Deferred import. AudioProcessor pulls in the
            from voice_typer.server.audio_processor import AudioProcessor

            real = AudioProcessor(
                app.config,
                sample_rate=app.config.sample_rate,
            )
            object.__setattr__(self, "_real", real)
        # Wire the quality callback ONCE, immediately after construction
        wired = object.__getattribute__(self, "_wired")
        if not wired:
            app = object.__getattribute__(self, "_app_ref")()
            if app is not None:
                try:
                    real.set_quality_callback(app._on_audio_quality_chunk)
                except Exception:
                    log.warning(
                        "[INIT] lazy AudioProcessor.set_quality_callback failed",
                        exc_info=True,
                    )
            object.__setattr__(self, "_wired", True)
        return real

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when the attribute is not found via
        return getattr(self._resolve(), name)


# Five accessors (``undo`` / ``audio_quality`` / ``_duck_crash_recovery``


def _build_undo(app: Any) -> Any:
    from voice_typer.server.app_undo import UndoRepasteController

    return UndoRepasteController(app)


def _build_audio_quality(app: Any) -> Any:
    from voice_typer.server.audio_quality_controller import AudioQualityController

    return AudioQualityController(app)


def _build_duck_crash_recovery(app: Any) -> Any:
    # Deferred import, duck_crash_recovery pulls in platform-specific
    from voice_typer.server.app import _resolve_config_dir
    from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

    return DuckCrashRecovery(config_dir=_resolve_config_dir())


def _build_volume_ducker(app: Any) -> Any:
    # Deferred import, ``volume_ducker`` pulls in platform-specific
    from voice_typer.server.volume_ducker import VolumeDucker

    return VolumeDucker(
        crash_recovery=app._duck_crash_recovery,
        on_crash_restore=app._on_volume_crash_restore,
    )


def _build_history_db(app: Any) -> Any:
    # Resolve ``HistoryDB`` through the app module at call time so the
    from voice_typer.server import app as _app_module

    return _app_module.HistoryDB()


class LazyProperty:
    """Data descriptor for the auto-constructing lazy accessors that
    share the ``_LAZY_FAILED`` sentinel + retry-TTL contract.

    Replaces the five verbatim copies of the identical getter/setter
    skeleton. Behavior is unchanged from the inline @property pairs it
    replaces:

    * getter, backing ``None``: construct via ``factory(app)`` and cache
      the instance in the backing attribute;
    * getter, backing ``_LAZY_FAILED``: return ``None`` silently (no
      construction re-attempt, no WARNING log) within
      ``RETRY_TTL_SECONDS`` of the recorded failure, the hot-path guard
      that keeps a single construction failure from spamming ~94
      warnings/sec for the whole recording session. After the TTL
      elapses the sentinel + timestamp are cleared and construction is
      retried (transient failures may recover);
    * on construction failure: WARNING with ``exc_info=True`` at the
      ``voice_typer.server.app`` logger, backing ← ``_LAZY_FAILED``,
      ``<backing minus _backing>_failed_at`` ← ``time.monotonic()``,
      return ``None`` (the sentinel is invisible to callers, see
      ``tests/test_app_none_guard.py``);
    * on construction success: backing ← instance, timestamp ← ``None``;
    * ``shutdown_guard=True`` (``history_db``): never construct, and
      never retry past the TTL, while ``app._shutting_down_event`` is
      set, so the shutdown teardown path's
      ``if app.history_db is not None:`` check cannot trigger the 30s
      writer-ready wait just to close a DB it never used;
    * setter: store into the backing attribute directly, so mock
      injection via ``app.<attr> = MagicMock()`` bypasses construction
      exactly like the previous property setters (a data descriptor's
      ``__set__`` and a property's ``fset`` observe the same assignment
      paths, including ``monkeypatch.setattr``).
    """

    def __init__(
        self,
        backing_attr: str,
        factory: Callable[[Any], Any],
        log_label: str,
        *,
        shutdown_guard: bool = False,
    ) -> None:
        self._backing_attr = backing_attr
        # The timestamp attribute is derived from the backing name —
        self._failed_at_attr = backing_attr[: -len("_backing")] + "_failed_at"
        self._factory = factory
        self._log_label = log_label
        self._shutdown_guard = shutdown_guard

    def _shutdown_in_progress(self, obj: Any) -> bool:
        return self._shutdown_guard and obj._shutting_down_event.is_set()

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        backing = getattr(obj, self._backing_attr)
        if backing is _LAZY_FAILED:
            if self._shutdown_in_progress(obj):
                return None
            failed_at = getattr(obj, self._failed_at_attr)
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                setattr(obj, self._backing_attr, None)
                setattr(obj, self._failed_at_attr, None)
                backing = None
            else:
                return None
        if backing is None:
            if self._shutdown_in_progress(obj):
                return None
            try:
                backing = self._factory(obj)
            except Exception:
                log.warning(f"[INIT] {self._log_label} lazy-init failed", exc_info=True)
                setattr(obj, self._backing_attr, _LAZY_FAILED)
                setattr(obj, self._failed_at_attr, time.monotonic())
                return None
            setattr(obj, self._backing_attr, backing)
            setattr(obj, self._failed_at_attr, None)
        return backing

    def __set__(self, obj: Any, value: Any) -> None:
        setattr(obj, self._backing_attr, value)


class AppLazyHub:
    """Lazy-@property mixin for ``VoiceTyperApp``.

    Every property here reads/writes backing attributes that
    ``VoiceTyperApp.__init__`` (well: its ``_init_*`` builders) declares.
    The mixin deliberately declares NO ``__init__``, construction order
    and attribute initialization stay entirely in ``app.py``; only the
    accessors live here.
    """

    # The raw state objects live in the coordinators constructed in
    _busyness: BusynessCoordinator
    _microphone_registry: MicrophoneRegistry

    @property
    def _busy_event(self) -> threading.Event:
        """The pipeline busy/ready event owned by :class:`BusynessCoordinator`.

        INVERTED legacy semantics preserved: ``is_set() == True`` means
        NOT busy (the event doubles as a ready signal). New code should
        prefer ``self._busyness.is_busy()`` / ``set_busy()`` /
        ``set_idle()`` / ``wait_idle()``.
        """
        return self._busyness.event

    @_busy_event.setter
    def _busy_event(self, event: threading.Event) -> None:
        self._busyness.adopt_event(event)

    @property
    def _lock(self) -> threading.Lock:
        """The companion coarse-grained lock owned by BusynessCoordinator."""
        return self._busyness.lock

    @_lock.setter
    def _lock(self, lock: threading.Lock) -> None:
        self._busyness.adopt_lock(lock)

    @property
    def _microphones(self) -> list[dict]:
        """Snapshot of the cached microphone list owned by MicrophoneRegistry."""
        return self._microphone_registry.list()

    @_microphones.setter
    def _microphones(self, mics: list[dict]) -> None:
        self._microphone_registry.replace(mics)

    # Each property has both a getter (constructs on first access if

    @property
    def _template_manager(self):
        # Return the backing directly. Construction is the caller's
        return self._template_manager_backing

    @_template_manager.setter
    def _template_manager(self, value) -> None:
        self._template_manager_backing = value

    @property
    def _vocabulary_manager(self):
        # Return the backing directly. Construction is the caller's
        return self._vocabulary_manager_backing

    @_vocabulary_manager.setter
    def _vocabulary_manager(self, value) -> None:
        self._vocabulary_manager_backing = value

    @property
    def correction_usage(self):
        """Shared per-correction usage tracker (``correction_usage.py``).

        Delegates to the live ``_vocabulary_manager``'s tracker when one
        exists (dictation records corrections + dictations through it,
        so there is exactly ONE writer). When the manager hasn't been
        constructed yet (cold start / test fixtures), a standalone
        tracker is built over the same config-dir file, read-only in
        practice (the ``get_correction_usage`` IPC path), so no
        cross-instance write interleaving can occur.
        """
        vm = self._vocabulary_manager
        if vm is not None:
            return vm.usage_tracker
        from voice_typer.server.correction_usage import CorrectionUsageTracker

        return CorrectionUsageTracker(self.config.config_dir)

    @property
    def clipboard(self):
        backing = self._clipboard_backing
        if backing is None:
            # Deferred import, the clipboard package eagerly imports
            from voice_typer.server.clipboard import ClipboardManager

            backing = ClipboardManager(
                paste_enabled=self.config.paste_on_stop,
            )
            self._clipboard_backing = backing
        return backing

    @clipboard.setter
    def clipboard(self, value) -> None:
        self._clipboard_backing = value

    @property
    def _waveform_bubble(self):
        backing = self._waveform_bubble_backing
        if backing is None:
            # Deferred import, ``voice_typer.server.waveform``
            from voice_typer.server.waveform import WaveformBubble

            backing = WaveformBubble()
            self._waveform_bubble_backing = backing
        return backing

    @_waveform_bubble.setter
    def _waveform_bubble(self, value) -> None:
        self._waveform_bubble_backing = value

    @property
    def waveform_wiring(self):
        backing = self._waveform_wiring_backing
        if backing is None:
            from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

            backing = WaveformBubbleWiring(self)
            self._waveform_wiring_backing = backing
        return backing

    @waveform_wiring.setter
    def waveform_wiring(self, value) -> None:
        self._waveform_wiring_backing = value

    # ``recorder`` / ``recording`` are built on a background thread in
    _recorder_backing: Any
    _recording_backing: Any
    _recorder_build_ready: threading.Event
    _recorder_build_error: BaseException | None

    @property
    def recorder(self) -> Any:
        backing = self._recorder_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # Shutdown guard (mirrors ``history_db``): if the app is already
        if self._shutting_down_event.is_set():
            return None
        self._recorder_build_ready.wait()
        if self._recorder_build_error is not None:
            raise self._recorder_build_error
        backing = self._recorder_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # The background build was short-circuited by a ``recorder``
        return None

    @recorder.setter
    def recorder(self, value: Any) -> None:
        self._recorder_backing = value
        self._recorder_build_ready.set()

    @property
    def recording(self) -> Any:
        backing = self._recording_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # Shutdown guard: see the ``recorder`` getter docstring.
        if self._shutting_down_event.is_set():
            return None
        self._recorder_build_ready.wait()
        if self._recorder_build_error is not None:
            raise self._recorder_build_error
        backing = self._recording_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # The background build was short-circuited by a ``recorder``
        from voice_typer.server.recording_controller import RecordingController

        backing = RecordingController(self)
        self._recording_backing = backing
        return backing

    @recording.setter
    def recording(self, value: Any) -> None:
        self._recording_backing = value
        self._recorder_build_ready.set()

    # ``undo`` (UndoRepasteController), ``audio_quality``

    undo = LazyProperty("_undo_backing", _build_undo, "UndoRepasteController")

    audio_quality = LazyProperty("_audio_quality_backing", _build_audio_quality, "AudioQualityController")

    _duck_crash_recovery = LazyProperty(
        "_duck_crash_recovery_backing",
        _build_duck_crash_recovery,
        "DuckCrashRecovery",
    )

    _volume_ducker = LazyProperty(
        "_volume_ducker_backing",
        _build_volume_ducker,
        "VolumeDucker",
    )

    # ``AudioProcessor`` construction is deferred to first attribute

    @property
    def _audio_processor(self):
        backing = self._audio_processor_backing
        if backing is None:
            backing = _LazyAudioProcessorProxy(self)
            self._audio_processor_backing = backing
        return backing

    @_audio_processor.setter
    def _audio_processor(self, value) -> None:
        self._audio_processor_backing = value

    # ``HistoryDB()`` construction is deferred to first access (the
    history_db = LazyProperty(
        "_history_db_backing",
        _build_history_db,
        "HistoryDB",
        shutdown_guard=True,
    )
