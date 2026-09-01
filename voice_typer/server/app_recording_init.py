"""AppRecordingInit — deferred recorder-subsystem construction mixin
extracted from VoiceTyperApp.

Owns the recording-subsystem startup slice of ``VoiceTyperApp``:

    - ``_init_recording`` — declare the ``_recorder_backing`` /
      ``_recording_backing`` sentinels (``_RECORDER_MISSING``), spawn the
      background ``recorder-init`` thread that imports
      ``voice_typer.server.recording`` + builds ``Recorder`` and
      ``RecordingController`` (with the setter-race guards so a test or
      caller that injects ``app.recorder = MagicMock()`` while the build
      is in flight is never clobbered), eagerly pre-import numpy/the
      recording package on the MAIN thread first (avoids the Python 3.13+
      ``_ModuleLock`` import-lock deadlock), and finally call
      ``_preload_vad_model``.
    - ``_preload_vad_model`` — spawn the background ``vad-preload``
      thread that eagerly loads + warms the Silero VAD model so the first
      recording's first audio chunk does not stall on
      ``torch.jit.load``.

Previously both lived on ``VoiceTyperApp`` in ``app.py``. The behaviour
is preserved verbatim — only the class boundary moved.
``VoiceTyperApp(AppRecordingInit)`` inherits every method, so
instance-level monkeypatching and direct calls keep working unchanged,
and ``inspect.getsource(VoiceTyperApp._init_recording)`` keeps returning
the same source text (getsource resolves through the MRO — the
deferred ``from voice_typer.server.recording import Recorder`` import
stays inside the method body, so ``Recorder`` never becomes a
module-top binding of ``voice_typer.server.app``; see
``tests/test_recorder_lazy_import_and_vad_cache_gates.py``).

``_RECORDER_MISSING`` is imported from ``app_lazy_hub`` so the sentinel
keeps ONE identity across modules: the re-export
``voice_typer.server.app._RECORDER_MISSING``, this module's binding, and
the ``is`` checks in the setter-race guards all observe the same object.

A note on logging (mirrors the convention in ``app_admin.py`` /
``app_dictation.py`` / ``app_lazy_hub.py`` / ``app_lifecycle.py``): this
module uses ``logging.getLogger("voice_typer.server.app")`` rather than
the conventional ``__name__`` so caplog captures in tests route to the
same logger as the original VoiceTyperApp methods.

A note on patch paths (C-ARCH-2): no test patches the app module's
binding for any name this module uses (verified by grepping the tests
tree for ``setattr("voice_typer.server.app.X"`` /
``setattr(app_module, "X"``) — the app-module seams live on the re-export
surface and on ``voice_typer.server.app_lazy_hub``, not here). The heavy
imports (``Recorder``, ``RecordingController``, ``vad``) stay deferred
INSIDE the method bodies, exactly where they lived in ``app.py``.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from voice_typer.server.app_lazy_hub import _RECORDER_MISSING

if TYPE_CHECKING:
    # Type-only import (no runtime cycle): this mixin only ever runs on
    # ``VoiceTyperApp``, whose ``__init__`` builders provide the
    # ``config`` / ``_audio_processor`` / ``_thread_registry``
    # attributes read below. ``from __future__ import annotations``
    # keeps the reference lazy at runtime.
    from voice_typer.server.app import VoiceTyperApp

# Tests capture recorder-build / vad-preload warnings at this logger
# name — see module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppRecordingInit:
    """Deferred recorder-subsystem construction mixin for ``VoiceTyperApp``.

    Declares NO ``__init__`` — construction order and the backing
    attributes stay entirely in ``app.py``; only the builders live here.
    """

    def _init_recording(self: VoiceTyperApp) -> None:
        """Spawn the background recorder build + VAD preload.

        ``Recorder`` + ``RecordingController`` construction is deferred
        to a background thread. The
        ``voice_typer.server.recording`` import + ``Recorder()`` build
        eagerly loads numpy/scipy/sounddevice (PortAudio) and can take
        1-8s on the main thread — measured ~5x slower under the system
        Python (the interpreter the packaged app runs on) than under the
        dev venv, and worse on cold cache. The tray and IPC server
        don't need the recorder, so blocking startup on it made the
        app look dead for seconds. The background build is registered
        with the ThreadRegistry (shutdown joins it) and ``app.recorder``
        / ``app.recording`` are lazy properties that block only briefly
        on first access if the build is still in flight — every existing
        call site keeps working unchanged.
        """
        self._recorder_backing: Any = _RECORDER_MISSING
        self._recording_backing: Any = _RECORDER_MISSING
        self._recorder_build_error: BaseException | None = None
        self._recorder_build_ready = threading.Event()

        def _build_recorder_subsystem() -> None:
            try:
                # Setter guard: a test (or a later caller) may have
                # injected ``app.recorder = MagicMock()`` via the setter
                # while this background build was in flight — never
                # clobber a caller-provided value with the real recorder.
                if self._recorder_backing is not _RECORDER_MISSING:
                    return
                from voice_typer.server.recording import Recorder

                recorder = Recorder(
                    self.config,
                    audio_processor=self._audio_processor,
                    thread_registry=self._thread_registry,
                )
                if self._recorder_backing is not _RECORDER_MISSING:
                    return  # setter raced us between import + construction
                self._recorder_backing = recorder
                # Recording lifecycle extracted to RecordingController.
                # Owns toggle/start/stop/cancel, silence/xrun callbacks,
                # and the streaming session.
                from voice_typer.server.recording_controller import RecordingController

                controller: Any = RecordingController(self)
                if self._recorder_backing is not recorder:
                    return  # setter raced us during controller construction
                self._recording_backing = controller
                # wire xrun threshold callback for tray
                # notification (was ``self.recorder.on_xrun_threshold =
                # self.recording.on_xrun_threshold`` on the main thread).
                recorder.on_xrun_threshold = controller.on_xrun_threshold
            except Exception as exc:  # noqa: BLE001 — surfaced on first access
                self._recorder_build_error = exc
                log.warning(
                    "[INIT] background recorder construction failed (%s)",
                    type(exc).__name__,
                    exc_info=True,
                )
            finally:
                self._recorder_build_ready.set()

        # Eagerly resolve numpy (and the recording package) on the MAIN
        # thread BEFORE spawning the recorder-init thread below.  The
        # recorder uses `lazy_module("numpy")` (see `_lazy_import.py`),
        # so its first numpy access triggers `importlib.import_module`
        # on the background thread.  If the main thread is concurrently
        # importing numpy (e.g. the audio-filter chain / scipy path),
        # Python 3.13+ raises `_DeadlockError: deadlock detected by
        # _ModuleLock('numpy._core._multiarray_umath')` — the classic
        # import-lock contention between two threads.  Pre-importing
        # numpy here (single-threaded, before any background thread
        # exists) makes the recorder-init thread's lazy resolve a
        # sys.modules cache hit with zero lock contention.
        try:
            import numpy  # noqa: F401

            from voice_typer.server.recording import Recorder as _RecorderType  # noqa: F401
        except Exception as exc:  # noqa: BLE001 — recorder still retries in its thread
            log.warning(
                "[INIT] eager numpy/recorder pre-import failed (%s) — recorder-init thread will retry on demand",
                type(exc).__name__,
            )

        self._thread_registry.spawn_and_register(
            "recorder-init",
            _build_recorder_subsystem,
            daemon=True,
            join_timeout=10.0,
        )
        # eagerly preload + warm the Silero VAD model on a
        # background thread so the first recording's first audio chunk
        # does not stall on torch.jit.load (~150-600ms cold load). The
        # model load previously happened lazily inside compute_vad_prob
        # on the audio worker thread; while the worker was stalled, the
        # SPSC ring buffer filled (94 chunks/sec at 48kHz/512) and
        # silently evicted the OLDEST chunks (the first syllables). The
        # preload moves the cost off the recording critical path. Safe
        # to call before the recorder's first start(); vad.preload() is
        # idempotent (cached on subsequent calls) and never raises — a
        # load failure falls through to lazy load on the first chunk
        # (preserving the pre-fix behavior as a fallback).
        self._preload_vad_model()

    def _preload_vad_model(self: VoiceTyperApp) -> None:
        """spawn a background thread to eagerly load + warm the
        Silero VAD model so the first recording's first audio chunk
        does not stall on ``torch.jit.load`` (~150-600ms cold load).

        The thread is registered with ``self._thread_registry`` so
        ``shutdown_all()`` joins it cleanly. Best-effort: any failure
        (torch missing, model file missing, OOM) is logged at DEBUG
        and the lazy-load fallback in ``compute_vad_prob`` is preserved.
        """
        try:
            from voice_typer.server import vad

            def _vad_preload_worker() -> None:
                try:
                    vad.preload()
                except Exception:
                    log.debug("[INIT] vad.preload() failed", exc_info=True)

            self._thread_registry.spawn_and_register(
                "vad-preload",
                _vad_preload_worker,
                daemon=True,
                join_timeout=2.0,
            )
        except Exception:
            log.debug("[INIT] could not spawn vad-preload thread", exc_info=True)
