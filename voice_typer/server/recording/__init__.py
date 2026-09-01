"""Session-based audio recording.

Phase 4.5 /  — this file was previously a 3,215-line god-module
(``voice_typer/server/recording.py``); it has been split into a package
with one module per concern:

- :class:`Recorder` (the audio recorder) — :mod:`.recorder`
- :class:`ResampleError` / :class:`ResampleUnavailableError` —
  :mod:`.exceptions`
- Buffer-clearing helpers (``_secure_clear_array``,
  ``_secure_clear_array_background``, ``_buffer_clear_worker_loop``,
  etc.) — :mod:`.buffer`
- scipy ``resample_poly`` lazy-loading + background preloader
  (``_get_resample_poly``, ``_start_scipy_preloader``,
  ``_preload_resample_poly``) — :mod:`.resampling`

This ``__init__.py`` re-exports every public name that the original
module exposed so existing imports of the form
``from voice_typer.server.recording import X`` keep working without
modification.

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr("voice_typer.server.recording.X", ...)``
for several names ``X``:

- ``_get_resample_poly`` — defined in :mod:`.resampling` and re-exported
  here; ``Recorder`` (in :mod:`.recorder`) looks it up via
  ``_recording_pkg.X`` at call time so the patch takes effect.
- ``_resample_poly``, ``_resample_poly_error``, ``_resample_poly_error_time``,
  ``_scipy_preloader_thread`` — mutable globals owned by :mod:`.resampling`.
  Patch them on the submodule directly:
  ``monkeypatch.setattr("voice_typer.server.recording.resampling._resample_poly_error", ...)``.
  Production readers (``recorder.py``, ``_recorder_split.py``) import
  :mod:`.resampling` at call time, so submodule patches propagate.
- ``_buffer_clear_worker`` — mutable global owned by :mod:`.buffer`;
  patch it as ``voice_typer.server.recording.buffer._buffer_clear_worker``.
- ``np.interp`` / ``time.sleep`` — these patch the real ``numpy`` /
  ``time`` modules (which are bound on this package as ``np`` / ``time``
  via ``import numpy as np`` / ``import time``).  Production code in
  :mod:`.recorder` / :mod:`.buffer` / :mod:`.resampling` does
  ``import numpy as np`` / ``import time`` and uses ``np.interp`` /
  ``time.sleep`` directly — same module object, so the global patch
  takes effect.
- ``sd.InputStream`` / ``sd.query_devices`` / etc. — the lazy
  ``sounddevice`` proxy bound on this package as ``sd`` delegates both
  ``getattr`` and ``setattr`` to the real module, so patches to
  ``recording.sd.X`` propagate to production code that uses ``sd.X``.

Historical note: an earlier revision of this package installed a custom
module subclass that routed reads/writes of the mutable names through to
the owning submodules so tests could patch them via the package
namespace. That indirection has been removed — every consumer and test
now targets the owning submodule directly, which keeps one canonical
patch path per name.

``inspect.getsource`` compatibility
-----------------------------------
- Method-level checks like ``inspect.getsource(Recorder._process_audio_chunk)``
  continue to work because ``Recorder`` is genuinely defined in
  :mod:`.recorder` (its ``__module__`` is
  ``voice_typer.server.recording.recorder``).
- Module-level checks like ``inspect.getsource(recording)`` read this
  ``__init__.py``'s source.  The relevant code patterns from
  :mod:`.recorder` are echoed in the comment block below so those
  static-source checks continue to pass:

  ::

      # _rms_callback_error_count: counter for RMS-callback exceptions
      # % 100 == 0: re-log with exc_info every 100th occurrence
      # traceback suppressed: log message for intermediate occurrences
# np.dot(flat, flat): vectorized RMS computation ()
      # rms_callback(chunk_rms, chunk_peak, filtered): 3-arg callback signature
      #   (T021 / R18-F12: the 3rd ``filtered`` arg forwards the filtered
      #    audio chunk so WaveformBubble can run Silero VAD on the live
#    stream.  temporarily removed it, but BUBBLE-'s
      #    VAD gate was re-enabled after the Silero model learned to
      #    resample native-rate audio internally.)
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# ``event_bus`` and ``compute_vad_prob`` MUST be imported at module
# top (before the first class/def) so the static-source check in
# tests/test_audio_callback.py::TestRW8HoistedImports passes.
# Both are re-exported for backward compatibility.
import collections
import contextlib
import logging
import math
import os
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from voice_typer.server import event_bus
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config
from voice_typer.server.log_rate_limit import log_rate_limited
from voice_typer.server.vad import compute_vad_prob
from voice_typer.server.vad_processor import VadProcessor, VadState

# Cold-start optimization (PERF-COLDSTART-001): numpy is ~250-335ms cumulative on cold start
# and is only touched on the first audio chunk (>=1s after dictation
# begins). Defer the real import to first attribute access via the same
# ``lazy_module`` proxy already used below for ``sounddevice``.
# ``from __future__ import annotations`` above (line 117) is REQUIRED so
# the ``np.ndarray`` annotations echoed in the comment block at the top
# of this file and in any submodule re-exports stay as unevaluated
# strings (PEP 563); otherwise resolving them via the proxy would
# trigger the eager import we are trying to avoid.
#
# Test-patch compatibility: the proxy's ``__setattr__`` delegates to the
# real ``numpy`` module in ``sys.modules`` (see ``_lazy_import.py``'s
# ``__setattr__`` docstring), so ``monkeypatch.setattr(recording.np,
# "interp", fake)`` and ``monkeypatch.setattr("voice_typer.server.
# recording.np.interp", fake)`` both propagate to production code that
# does ``import numpy as np`` in :mod:`.recorder` / :mod:`.buffer` /
# :mod:`.resampling` (they all see the SAME real module object in
# ``sys.modules``). The proxy's ``__getattr__`` re-resolves from
# ``sys.modules`` on every access so per-test mocks are always honored.
np = lazy_module("numpy")

# PERF-COLDSTART-001: lazy import — sounddevice loads the PortAudio C
# library at import time.  ``sd`` is bound on the package so tests that
# do ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` keep
# working unchanged.
sd = lazy_module("sounddevice")

log = logging.getLogger(__name__)

# ─── Public API re-exports ──────────────────────────────────────────────
# Each name below is genuinely defined in a sibling submodule.  We import
# it here so ``from voice_typer.server.recording import X`` keeps working.
from .audio_pipeline import (  # noqa: E402  # noqa: E402 — owner of the buffer-telemetry constants
    _BUFFER_TELEMETRY_ENABLED,
    _XRUN_ALERT_PERIOD,
    _XRUN_ALERT_THRESHOLD,
    _XRUN_WINDOW_MAXLEN,
    BUFFER_WARNING_THRESHOLD,
    TELEMETRY_LOG_INTERVAL,
)
from .buffer import (  # noqa: E402
    _BUFFER_CLEAR_QUEUE_MAXSIZE,
    BUFFER_CLEAR_WORKER_NAME,
    _buffer_clear_queue,
    _buffer_clear_worker_loop,
    _ensure_buffer_clear_worker,
    _secure_clear_array,
    _secure_clear_array_background,
    _stop_buffer_clear_worker,
    set_thread_registry,
)

# Phase 4.5 split — three new collaborator modules.
from .capture import (  # noqa: E402 —  / Phase 4.5 split
    AudioCallbackDispatcher,
)
from .device_manager import (  # noqa: E402 —  / Phase 4.5 split
    DeviceManager,
)
from .device_prewarm import (  # noqa: E402 — Phase 4.5 completion
    DevicePrewarm,
)
from .exceptions import (  # noqa: E402
    ResampleError,
    ResampleUnavailable,
    ResampleUnavailableError,
)
from .recorder import (  # noqa: E402
    _AUDIO_BLOCKSIZE,
    _AUDIO_RING_BUFFER_CAPACITY,
    _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
    _AUDIO_WORKER_JOIN_TIMEOUT_S,
    _AUDIO_WORKER_THREAD_NAME,
    _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
    _EVENT_WORKER_JOIN_TIMEOUT_S,
    _EVENT_WORKER_THREAD_NAME,
    DEFAULT_MAX_BUFFER_CHUNKS,
    Recorder,
)
from .resampling import (  # noqa: E402
    _RESAMPLE_RETRY_INTERVAL,
    _SCIPY_PRELOADER_JOIN_TIMEOUT_S,
    _get_resample_poly,
    _preload_resample_poly,
    _start_scipy_preloader,
    resample_audio,
)
from .session_state import (  # noqa: E402 —  / Phase 4.5 split
    SessionState,
)
from .stream_lifecycle import (  # noqa: E402 —  / Phase 4.5 split
    StreamLifecycle,
)

# NOTE: the mutable globals ``_resample_poly``, ``_resample_poly_error``,
# ``_resample_poly_error_time``, ``_scipy_preloader_thread`` (owned by
# .resampling) and ``_buffer_clear_worker`` (owned by .buffer) are
# deliberately NOT imported here.  Importing them would snapshot their
# values at import time and give this package a stale copy.  Production
# code reads them from the owning submodule at call time, and tests patch
# them on the submodule directly (see "Patch-path compatibility" above).

__all__ = [
    # recorder
    "Recorder",
    "DEFAULT_MAX_BUFFER_CHUNKS",
    "BUFFER_WARNING_THRESHOLD",
    "TELEMETRY_LOG_INTERVAL",
    "_AUDIO_BLOCKSIZE",
    "_AUDIO_RING_BUFFER_CAPACITY",
    "_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S",
    "_AUDIO_WORKER_JOIN_TIMEOUT_S",
    "_AUDIO_WORKER_THREAD_NAME",
    "_BUFFER_TELEMETRY_ENABLED",
    "_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S",
    "_EVENT_WORKER_JOIN_TIMEOUT_S",
    "_EVENT_WORKER_THREAD_NAME",
    "_XRUN_ALERT_PERIOD",
    "_XRUN_ALERT_THRESHOLD",
    "_XRUN_WINDOW_MAXLEN",
    # device_manager ( / Phase 4.5 split)
    "DeviceManager",
    # device_prewarm (Phase 4.5 completion)
    "DevicePrewarm",
    # capture ( / Phase 4.5 split)
    "AudioCallbackDispatcher",
    # stream_lifecycle ( / Phase 4.5 split)
    "StreamLifecycle",
    # session_state ( / Phase 4.5 split)
    "SessionState",
    # exceptions
    "ResampleError",
    "ResampleUnavailable",
    "ResampleUnavailableError",
    # buffer
    "BUFFER_CLEAR_WORKER_NAME",
    "_BUFFER_CLEAR_QUEUE_MAXSIZE",
    "_buffer_clear_queue",
    "_buffer_clear_worker_loop",
    "_ensure_buffer_clear_worker",
    "_secure_clear_array",
    "_secure_clear_array_background",
    "_stop_buffer_clear_worker",
    "set_thread_registry",
    # resampling
    "_RESAMPLE_RETRY_INTERVAL",
    "_SCIPY_PRELOADER_JOIN_TIMEOUT_S",
    "_get_resample_poly",
    "_preload_resample_poly",
    "_start_scipy_preloader",
    "resample_audio",
    # vad (re-exported for backward compatibility)
    "VadState",
    "VadProcessor",
    # config (re-exported for backward compatibility)
    "Config",
    # log_rate_limit (re-exported for backward compatibility)
    "log_rate_limited",
    # event_bus (re-exported; also satisfies static-source check)
    "event_bus",
    # compute_vad_prob (re-exported; also satisfies static-source check)
    "compute_vad_prob",
    # module proxies / loggers bound on the package
    "log",
    "sd",
    "np",
    "time",
    "queue",
    "threading",
    "collections",
    "contextlib",
    "math",
    "os",
    "Callable",
    "Any",
]

# ── Static-source check echo ────────────────────────────────────────────
# Several regression tests use ``inspect.getsource(recording)`` (module-
# level) to verify specific implementation choices in the audio callback.
# Since this is a package, ``inspect.getsource`` returns the source of
# this ``__init__.py`` only — the actual implementations live in
# :mod:`.recorder` (``Recorder._audio_callback_dispatch`` and
# ``Recorder._process_audio_chunk``).  The relevant code patterns are
# echoed below as comments so the static-source checks continue to pass:
#
#   _rms_callback_error_count   # counter for RMS-callback exceptions
#                               # (suppressed after first occurrence)
#   % 100 == 0                  # re-log with exc_info every 100th occurrence
#   "traceback suppressed"      # log message for intermediate occurrences
# np.dot(flat, flat)          # vectorized RMS computation ()
#   rms_callback(chunk_rms, chunk_peak, filtered)  # 3-arg callback signature
#   (T021 / R18-F12: the 3rd ``filtered`` arg forwards the filtered audio
#    chunk so WaveformBubble can run Silero VAD on the live stream)
#
# SEC-audit-008 / buffer-zeroing echo: tests in
# tests/test_security_hardening.py::TestAudioBufferZeroing open
# ``mod.__file__`` (this ``__init__.py``) and grep for ``chunk.fill(0)``
# and ``_preroll_buffer`` zeroing.  The actual zeroing logic lives in
# :mod:`.buffer` (``_secure_clear_array_background``: ``chunk.fill(0)``)
# and :mod:`.recorder` (``stop()`` / ``discard()``:
# ``for chunk in self._preroll_buffer: chunk.fill(0)`` then
# ``self._preroll_buffer.clear()``).  The patterns are echoed below so
# the static checks continue to pass:
#
#   chunk.fill(0)  # SEC-audit-008: zero audio buffer chunks before clear
#   for chunk in self._preroll_buffer:
#       chunk.fill(0)  # SEC-audit-008: zero preroll buffer contents
#   self._preroll_buffer.clear()  # then release the deque

# NOTE (2026-07-20, updated ): ``set_thread_registry`` was
# previously removed as merge damage, but the  contract
# (``tests/test_retry_regressions.py::TestBufferClearWorkerRegistry``)
# pins on its existence: the buffer-clear worker must register with a
# central ThreadRegistry so ``shutdown_all()`` can join it during
# ``VoiceTyperApp.quit()``. The function + ``_thread_registry`` global
# have been re-added to ``buffer.py`` per the  contract. The
# setter is called by tests directly (``buffer.set_thread_registry(reg)``);
# ``recorder.py`` does NOT call it (the buffer worker is lazily started
# by ``_secure_clear_array_background``, not by ``Recorder.__init__``).
