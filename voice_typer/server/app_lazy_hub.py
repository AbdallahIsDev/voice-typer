"""AppLazyHub — the lazy-@property mixin extracted from VoiceTyperApp.

Owns every lazily-constructed subsystem accessor on ``VoiceTyperApp``:

- 3 legacy private-state back-compat delegates (``_busy_event`` / ``_lock`` /
  ``_microphones``) that forward to ``BusynessCoordinator`` /
  ``MicrophoneRegistry``;
- 15 lazy @property pairs (plus the read-only ``correction_usage``) covering
  the recorder/recording subsystem, clipboard, waveform bubble, undo /
  audio-quality / duck-crash-recovery / volume-ducker / history-db
  controllers, the audio-processor proxy, and the passive template /
  vocabulary manager views.

Previously all of this lived on ``VoiceTyperApp`` in ``app.py`` (~640 LOC).
The behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp(AppLazyHub)`` inherits every property, so each attribute
name keeps resolving on instances and every existing monkeypatch seam
(``app.undo = MagicMock()``, ``app.recorder = ...``, ``app.history_db =
...`` via the setters) works unchanged. Plain @property pairs over
sentinel attributes — NO descriptors, NO changed setattr semantics.

Sentinels + TTL (``_RECORDER_MISSING`` / ``_LAZY_FAILED`` /
``RETRY_TTL_SECONDS``) and the ``_LazyAudioProcessorProxy`` live here now
and are re-exported from ``voice_typer.server.app`` so existing imports
(and identity checks like ``backing is _LAZY_FAILED``) keep working.

A note on logging (mirrors the convention in ``app_lifecycle.py`` and
``app_undo.py``): this module uses
``logging.getLogger("voice_typer.server.app")`` rather than the
conventional ``__name__``. Tests capture the lazy-init WARNING lines
(e.g. "AudioQualityController lazy-init failed") at
``logger="voice_typer.server.app"`` — using ``__name__`` would route
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
from typing import Any

from voice_typer.server._busyness import BusynessCoordinator
from voice_typer.server._microphone_registry import MicrophoneRegistry

# Tests capture lazy-init failures at this logger name — see module
# docstring.
log = logging.getLogger("voice_typer.server.app")


# Sentinel for the lazily-built ``recorder`` / ``recording`` backings.
# ``None`` is a legitimate test-set value, so a distinct sentinel
# distinguishes "not built yet" from "explicitly None".
_RECORDER_MISSING: object = object()

# Sentinel + TTL for the lazy ``@property`` accessors that wrap controller
# construction in ``try/except Exception`` (``undo``, ``audio_quality``,
# ``_duck_crash_recovery``, ``_volume_ducker``, ``history_db``).
#
# ``None`` is the *initial* state ("not yet attempted construction"), so it
# cannot also represent "construction already failed" — without a distinct
# sentinel, every subsequent access would re-enter the ``try`` block and
# re-attempt construction + re-log the WARNING (the ``audio_quality``
# property is on a ~94 Hz hot path; a single failure spams ~94 warnings/sec
# for the entire recording session). ``_LAZY_FAILED`` is cached in the
# backing on failure alongside a ``_<prop>_failed_at`` monotonic
# timestamp; the getter returns ``None`` silently (no log, no construction
# re-attempt) until ``RETRY_TTL_SECONDS`` elapses, then clears the sentinel
# and retries construction (transient failures can recover). This is the
# canonical E8 exception-clause case ("Define a sentinel only when None is
# itself a meaningful value") — ``None`` IS meaningful (initial state), so
# the failure state needs a distinct marker. Mirrors the
# ``_RECORDER_MISSING`` precedent in this module.
_LAZY_FAILED: object = object()
RETRY_TTL_SECONDS: float = 30.0


class _LazyAudioProcessorProxy:
    """Transparent lazy proxy for ``AudioProcessor``.

    ``VoiceTyperApp.__init__`` used to construct ``AudioProcessor``
    eagerly, which calls ``build_chain(config, sample_rate)``. That in
    turn imports the full ``audio_filters`` package (highpass ->
    ``scipy.signal.butter``, noise_suppressor -> RNNoise, etc.) on
    every cold start — even when the user never dictates.

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
    immediately after construction — this wiring used to live at
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
        # AudioProcessor) when storing state on the proxy itself.
        object.__setattr__(self, "_app_ref", weakref.ref(app))
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "_wired", False)

    def _resolve(self):
        real = object.__getattribute__(self, "_real")
        if real is None:
            app = object.__getattribute__(self, "_app_ref")()
            if app is None:
                # The owning VoiceTyperApp was garbage-collected —
                # should never happen in normal operation because the
                # Recorder (which holds the proxy) is owned by the app.
                # Defensive: raise AttributeError so the caller sees a
                # clear failure rather than a None dereference.
                raise AttributeError("_LazyAudioProcessorProxy: owning VoiceTyperApp was garbage-collected")
            # Deferred import — AudioProcessor pulls in the
            # ``audio_filters`` package (scipy.signal.butter, RNNoise).
            from voice_typer.server.audio_processor import AudioProcessor

            real = AudioProcessor(
                app.config,
                sample_rate=app.config.sample_rate,
            )
            object.__setattr__(self, "_real", real)
        # Wire the quality callback ONCE, immediately after construction
        # (whether just-constructed or pre-existing). The ``_wired`` flag
        # guards against re-wiring on every access (which would replace
        # the callback if a later caller manually called
        # ``set_quality_callback`` with a different cb).
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
        # normal lookup (i.e. for anything that isn't _app_ref / _real /
        # _wired / a class attribute). Every wrapped-processor attribute
        # goes through here.
        return getattr(self._resolve(), name)


class AppLazyHub:
    """Lazy-@property mixin for ``VoiceTyperApp``.

    Every property here reads/writes backing attributes that
    ``VoiceTyperApp.__init__`` (well: its ``_init_*`` builders) declares.
    The mixin deliberately declares NO ``__init__`` — construction order
    and attribute initialization stay entirely in ``app.py``; only the
    accessors live here.
    """

    # ─── Legacy private-state back-compat properties ──────────────────
    #
    # The raw state objects live in the coordinators constructed in
    # ``__init__`` (``_busyness`` / ``_microphone_registry``); these
    # read/write views keep every historical consumer of
    # ``app._busy_event`` / ``app._lock`` / ``app._microphones``
    # working unchanged. Setters rebind INTO the coordinator (never
    # shadowing it) so ``monkeypatch.setattr(app, "_busy_event", ...)``
    # style injection and plain ``app._microphones = [...]`` rebinding
    # keep their pre-extraction semantics.
    #
    # Declared here (not inferred from the ``__init__`` assignments)
    # because ``__init__`` is unannotated — mypy does not treat
    # assignments inside an untyped function body as attribute
    # declarations, so without these the delegating properties below
    # read the coordinators as ``Any``.
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

    # ─── Lazy @property accessors ─────────────────────────────────────
    #
    # Each property has both a getter (constructs on first access if
    # the backing is None) and a setter (stores directly into the
    # backing so existing tests that inject mocks via
    # ``app.<attr> = MagicMock()`` keep working transparently —
    # assignment bypasses the lazy construction).
    #
    # Construction failures (e.g. a corrupt ``templates.json``) are
    # logged at WARNING level with ``exc_info=True`` (mirrors the
    # pre- eager-init failure-logging contract). The backing is then
    # set to the ``_LAZY_FAILED`` sentinel (NOT ``None``) plus a
    # ``_<prop>_failed_at`` monotonic timestamp — subsequent accesses
    # within ``RETRY_TTL_SECONDS`` (30s) return ``None`` silently (no
    # log, no construction re-attempt) so the per-chunk hot path
    # (``audio_quality`` at ~94 Hz) does not spam ~94 warnings/sec for
    # the entire recording session. After the TTL elapses the sentinel
    # is cleared and construction is retried (transient failures can
    # recover). The WARNING fires exactly once per fresh failure
    # (per TTL window), not on every access. ``None`` IS a meaningful
    # value here (the initial "not yet attempted" state, and the value
    # returned by the getter to callers while the sentinel is in TTL),
    # so the sentinel qualifies for the E8 exception clause ("Define a
    # sentinel only when None is itself a meaningful value") — mirrors
    # the existing ``_RECORDER_MISSING`` precedent in this module.

    @property
    def _template_manager(self):
        # Return the backing directly. Construction is the caller's
        # responsibility — see ``service/template.py``'s lazy fallback
        # which constructs via ``TemplateManager()`` and assigns back
        # via the setter below. Returning ``None`` when uninitialised
        # lets tests verify the lazy contract without triggering
        # eager ``TemplateManager()`` construction on ``__init__``
        # (TemplateManager reads ``templates.json`` from disk; that's
        # hundreds of ms on a cold start).
        return self._template_manager_backing

    @_template_manager.setter
    def _template_manager(self, value) -> None:
        self._template_manager_backing = value

    @property
    def _vocabulary_manager(self):
        # Return the backing directly. Construction is the caller's
        # responsibility — see ``service/vocabulary.py``'s lazy
        # fallback which constructs via ``VocabularyManager()`` and
        # assigns back via the setter below. Returning ``None`` when
        # uninitialised lets tests verify the lazy contract without
        # triggering eager ``VocabularyManager()`` construction on
        # ``__init__`` (VocabularyManager reads ``vocabulary.json``
        # from disk; that's hundreds of ms on a cold start).
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
        tracker is built over the same config-dir file — read-only in
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
            # Deferred import — the clipboard package eagerly imports
            # ``pyperclip`` + the platform backends (``.windows`` /
            # ``.linux``, which pull in pywin32 / pynput) and the
            # ``manager`` submodule imports ``config`` at module top
            # (~13 ms of the cold-start import chain, measured). The
            # clipboard is only touched at dictation-stop paste time,
            # so the class import is deferred to first access — mirrors
            # the existing deferred-import pattern (undo, audio_quality,
            # _volume_ducker).
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
            # Deferred import — ``voice_typer.server.waveform``
            # transitively imports numpy, which is ~250-335ms on cold
            # start. Deferred to first access (which only happens when
            # the bubble is actually shown).
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

    # ─── Lazy recorder / recording properties (background build) ──────
    #
    # ``recorder`` / ``recording`` are built on a background thread in
    # ``__init__`` so the multi-second construction cost
    # (numpy/scipy/sounddevice + PortAudio init) never blocks the tray /
    # IPC startup. The getters block only if the background build is
    # still in flight (brief); the setters let tests inject mocks
    # transparently (assignment bypasses the lazy build).

    @property
    def recorder(self) -> Any:
        backing = self._recorder_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # Shutdown guard (mirrors ``history_db``): if the app is already
        # quitting and the background build never finished, return None
        # instead of blocking the teardown path on the still-in-flight
        # build. Shutdown teardowns check ``app.recorder is not None``,
        # so a never-built recorder is skipped cleanly; a built recorder
        # is returned by the fast path above.
        if self._shutting_down_event.is_set():
            return None
        self._recorder_build_ready.wait()
        if self._recorder_build_error is not None:
            raise self._recorder_build_error
        backing = self._recorder_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # The background build was short-circuited by a ``recorder``
        # setter (test mock injection) before it could build the real
        # Recorder — never construct one eagerly here (that would
        # re-introduce the multi-second startup stall). Return None;
        # callers treat a missing recorder the same as an unbuilt one.
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
        # Shutdown guard — see the ``recorder`` getter docstring.
        if self._shutting_down_event.is_set():
            return None
        self._recorder_build_ready.wait()
        if self._recorder_build_error is not None:
            raise self._recorder_build_error
        backing = self._recording_backing
        if backing is not _RECORDER_MISSING:
            return backing
        # The background build was short-circuited by a ``recorder``
        # setter (test mock injection) so ``_recording_backing`` was
        # never populated. Construct the controller on demand to
        # preserve the long-standing contract that ``app.recording`` is
        # always a RecordingController (RecordingController is cheap to
        # build — no audio/numpy imports — so this adds no startup cost).
        from voice_typer.server.recording_controller import RecordingController

        backing = RecordingController(self)
        self._recording_backing = backing
        return backing

    @recording.setter
    def recording(self, value: Any) -> None:
        self._recording_backing = value
        self._recorder_build_ready.set()

    # ─── Lazy controller / volume-subsystem properties ────────────────
    #
    # ``undo`` (UndoRepasteController), ``audio_quality``
    # (AudioQualityController), ``_duck_crash_recovery``
    # (DuckCrashRecovery), and ``_volume_ducker`` (VolumeDucker) used
    # to be constructed eagerly in ``__init__``. They are now
    # auto-constructing lazy properties — the first access triggers
    # construction and caches the instance in the backing attribute.
    # Tests that inject mocks via ``app.<attr> = MagicMock()`` use the
    # setter, which bypasses the lazy construction.

    @property
    def undo(self):
        backing = self._undo_backing
        if backing is _LAZY_FAILED:
            # Previous construction attempt failed. Within the retry TTL
            # return ``None`` silently (no construction re-attempt, no
            # WARNING log) so the hot path does not spam 94 failures/sec.
            # After TTL elapses, clear the sentinel and fall through to
            # retry construction (transient failures may recover).
            failed_at = self._undo_failed_at
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                self._undo_backing = None
                self._undo_failed_at = None
                backing = None
            else:
                return None
        if backing is None:
            try:
                from voice_typer.server.app_undo import UndoRepasteController

                backing = UndoRepasteController(self)
            except Exception:
                log.warning("[INIT] UndoRepasteController lazy-init failed", exc_info=True)
                self._undo_backing = _LAZY_FAILED
                self._undo_failed_at = time.monotonic()
                return None
            self._undo_backing = backing
            self._undo_failed_at = None
        return backing

    @undo.setter
    def undo(self, value) -> None:
        self._undo_backing = value

    @property
    def audio_quality(self):
        backing = self._audio_quality_backing
        if backing is _LAZY_FAILED:
            # Sentinel: previous construction attempt failed. Return
            # ``None`` silently within the retry TTL (this is the hot
            # path at ~94 Hz — without this guard a single failure
            # spams ~94 WARNING logs/sec + ~94 construction re-attempts
            # per second for the entire recording session). After TTL
            # elapses, clear the sentinel and retry construction.
            failed_at = self._audio_quality_failed_at
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                self._audio_quality_backing = None
                self._audio_quality_failed_at = None
                backing = None
            else:
                return None
        if backing is None:
            try:
                from voice_typer.server.audio_quality_controller import (
                    AudioQualityController,
                )

                backing = AudioQualityController(self)
            except Exception:
                log.warning("[INIT] AudioQualityController lazy-init failed", exc_info=True)
                self._audio_quality_backing = _LAZY_FAILED
                self._audio_quality_failed_at = time.monotonic()
                return None
            self._audio_quality_backing = backing
            self._audio_quality_failed_at = None
        return backing

    @audio_quality.setter
    def audio_quality(self, value) -> None:
        self._audio_quality_backing = value

    @property
    def _duck_crash_recovery(self):
        backing = self._duck_crash_recovery_backing
        if backing is _LAZY_FAILED:
            # Sentinel: previous construction attempt failed. See the
            # ``undo`` property above for the full TTL-retry rationale.
            failed_at = self._duck_crash_recovery_failed_at
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                self._duck_crash_recovery_backing = None
                self._duck_crash_recovery_failed_at = None
                backing = None
            else:
                return None
        if backing is None:
            try:
                # Deferred import — duck_crash_recovery pulls in
                # platform-specific volume backends (pyobjc on macOS,
                # ctypes-coreaudio on Windows). Deferred to first access
                # (which only happens when volume ducking is enabled).
                # The app-module helper does the call-time indirection so
                # patches on ``config._config_dir`` propagate (imported
                # here rather than duplicated — single source of truth).
                from voice_typer.server.app import _resolve_config_dir
                from voice_typer.server.duck_crash_recovery import DuckCrashRecovery

                backing = DuckCrashRecovery(config_dir=_resolve_config_dir())
            except Exception:
                log.warning("[INIT] DuckCrashRecovery lazy-init failed", exc_info=True)
                self._duck_crash_recovery_backing = _LAZY_FAILED
                self._duck_crash_recovery_failed_at = time.monotonic()
                return None
            self._duck_crash_recovery_backing = backing
            self._duck_crash_recovery_failed_at = None
        return backing

    @_duck_crash_recovery.setter
    def _duck_crash_recovery(self, value) -> None:
        self._duck_crash_recovery_backing = value

    @property
    def _volume_ducker(self):
        backing = self._volume_ducker_backing
        if backing is _LAZY_FAILED:
            # Sentinel: previous construction attempt failed. See the
            # ``undo`` property above for the full TTL-retry rationale.
            failed_at = self._volume_ducker_failed_at
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                self._volume_ducker_backing = None
                self._volume_ducker_failed_at = None
                backing = None
            else:
                return None
        if backing is None:
            try:
                # Deferred import — ``volume_ducker`` pulls in
                # platform-specific volume backends (pyobjc on macOS,
                # ctypes on Windows). Deferred to first access (which
                # only happens when volume ducking is enabled).
                from voice_typer.server.volume_ducker import VolumeDucker

                backing = VolumeDucker(
                    crash_recovery=self._duck_crash_recovery,
                    on_crash_restore=self._on_volume_crash_restore,
                )
            except Exception:
                log.warning("[INIT] VolumeDucker lazy-init failed", exc_info=True)
                self._volume_ducker_backing = _LAZY_FAILED
                self._volume_ducker_failed_at = time.monotonic()
                return None
            self._volume_ducker_backing = backing
            self._volume_ducker_failed_at = None
        return backing

    @_volume_ducker.setter
    def _volume_ducker(self, value) -> None:
        self._volume_ducker_backing = value

    # ─── lazy AudioProcessor property ───────────────────────────
    #
    # ``AudioProcessor`` construction is deferred to first attribute
    # access via a ``_LazyAudioProcessorProxy``. The proxy transparently
    # constructs the real ``AudioProcessor`` on the first
    # ``process_chunk`` / ``set_sample_rate`` / ``rebuild_from_config``
    # call (i.e. on the first recording or the first config-driven
    # rebuild). The proxy also wires ``set_quality_callback`` after
    # construction.
    #
    # Tests that inject mocks via ``app._audio_processor = MagicMock()``
    # use the setter, which bypasses the proxy entirely.

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

    # ─── lazy HistoryDB property ────────────────────────────────
    #
    # ``HistoryDB()`` construction is deferred to first access. The
    # eager construction that used to live in ``__init__`` blocked for
    # up to ``_WRITER_READY_TIMEOUT`` (30s) waiting for the writer
    # thread's schema-init to complete. The lazy property mirrors the
    # existing pattern (clipboard, undo, audio_quality) — construction
    # failure is logged at WARNING with ``exc_info=True`` and the
    # backing is set to the ``_LAZY_FAILED`` sentinel + a monotonic
    # timestamp; subsequent accesses within ``RETRY_TTL_SECONDS`` (30s)
    # return ``None`` silently, then retry construction after the TTL
    # elapses.
    #
    # The ``_shutting_down_event`` guard prevents the shutdown teardown
    # path (``shutdown/teardowns/history_db.py``) from triggering lazy
    # construction via its ``if app.history_db is not None:`` check — a
    # never-dictated session would otherwise pay the 30s writer-ready
    # wait on quit just to immediately close the DB it never used.

    @property
    def history_db(self):
        backing = self._history_db_backing
        if backing is _LAZY_FAILED:
            # Sentinel: previous construction attempt failed. See the
            # ``undo`` property above for the full TTL-retry rationale.
            # Don't lazy-retry during shutdown — return ``None`` silently
            # (mirrors the None-backing shutdown guard below).
            if self._shutting_down_event.is_set():
                return None
            failed_at = self._history_db_failed_at
            if failed_at is None or time.monotonic() - failed_at >= RETRY_TTL_SECONDS:
                self._history_db_backing = None
                self._history_db_failed_at = None
                backing = None
            else:
                return None
        if backing is None:
            # Don't lazy-construct during shutdown — the teardown path
            # checks ``app.history_db is not None`` to decide whether
            # to flush/close, and we don't want to construct a
            # HistoryDB during shutdown just to immediately close it.
            if self._shutting_down_event.is_set():
                return None
            try:
                # Resolve ``HistoryDB`` through the app module at call
                # time so the documented monkeypatch seam
                # (``voice_typer.server.app.HistoryDB``) keeps
                # intercepting construction — see the module docstring.
                from voice_typer.server import app as _app_module

                backing = _app_module.HistoryDB()
            except Exception:
                log.warning("[INIT] HistoryDB lazy-init failed", exc_info=True)
                self._history_db_backing = _LAZY_FAILED
                self._history_db_failed_at = time.monotonic()
                return None
            self._history_db_backing = backing
            self._history_db_failed_at = None
        return backing

    @history_db.setter
    def history_db(self, value) -> None:
        self._history_db_backing = value
