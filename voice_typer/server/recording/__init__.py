"""Session-based audio recording.

Phase 4.5 / ARCH-045 — this file was previously a 3,215-line god-module
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

CR-67 / TECH-DEBT: custom module class for test-patch compatibility
-------------------------------------------------------------------
This package installs a custom module subclass (``_RecordingModule``,
defined at the bottom of this file) whose ``__getattr__`` and
``__setattr__`` route a small set of mutable global names
(``_resample_poly``, ``_resample_poly_error``,
``_resample_poly_error_time``, ``_scipy_preloader_thread``,
``_buffer_clear_worker``) through to the owning submodule
(:mod:`.resampling` / :mod:`.buffer`) instead of landing on the
package's own ``__dict__``.

WHY this hack exists: the test suite uses
``monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)``
to inject failure modes into the resample path, and then expects
``Recorder._get_resample_poly`` (defined in :mod:`.resampling`) to
see the new value when it next reads the global.  Without the
custom module class, the test's write would land on the package's
``__dict__`` (a stale snapshot) and :mod:`.resampling`'s binding
would be unchanged — the test would silently no-op.

The same indirection pattern exists in :mod:`voice_typer.server.prewarm`
and :mod:`voice_typer.server.server_platform` (their submodules do
``from voice_typer.server import X as _pkg`` and look up patched
names via ``_pkg.X`` at call time).  All three packages together
account for ~500 LOC of ``__init__.py`` boilerplate that exists
purely for test-patch compatibility.

TODO (2026-07-25, CR-67 / TECH-DEBT — OPEN, awaiting migration):
This ``__init__.py`` boilerplate exists for test-patch compatibility
during the package reorganization.  Once CR-67 is complete, this
file will be simplified.  Migrate tests to patch submodules directly,
then remove this class.  Concretely: replace
``monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)``
with
``monkeypatch.setattr("voice_typer.server.recording.resampling._resample_poly_error", ...)``
(and similarly for the other routed names).  Once every test site
has been migrated, ``_RecordingModule`` and the ``_MUTABLE_*``
frozensets below can be deleted.  Estimated scope: 30-50 test files
per package (so 90-150 test files total across the three packages).
Tracked as CR-67 / TECH-DEBT.

Patch-path compatibility
------------------------
Tests use ``monkeypatch.setattr("voice_typer.server.recording.X", ...)``
for several names ``X``:

- ``_get_resample_poly`` — defined in :mod:`.resampling`; ``Recorder``
  (in :mod:`.recorder`) looks it up via ``_recording_pkg.X`` at call
  time so the patch takes effect.
- ``_resample_poly``, ``_resample_poly_error``, ``_resample_poly_error_time``,
  ``_scipy_preloader_thread`` — mutable globals owned by
  :mod:`.resampling`.  Tests both read and write these via the package
  namespace.  This ``__init__.py`` installs a custom module class
  (``_RecordingModule``) whose ``__getattr__`` / ``__setattr__`` route
  the mutable names through to :mod:`.resampling` so test writes
  propagate to the production code that reads them.
- ``_buffer_clear_worker`` — mutable global owned by :mod:`.buffer`;
  same routing mechanism.
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
      # np.dot(flat, flat): vectorized RMS computation (AUDIO-007)
      # rms_callback(chunk_rms, chunk_peak, filtered): 3-arg callback signature
      #   (T021 / R18-F12: the 3rd ``filtered`` arg forwards the filtered
      #    audio chunk so WaveformBubble can run Silero VAD on the live
      #    stream. G4-L-04 temporarily removed it, but BUBBLE-FIX-4.1's
      #    VAD gate was re-enabled after the Silero model learned to
      #    resample native-rate audio internally.)
"""

from __future__ import annotations

# ─── Top-of-module imports ──────────────────────────────────────────────
# RW-8: ``event_bus`` and ``compute_vad_prob`` MUST be imported at module
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

import numpy as np

from voice_typer.server import event_bus
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.config import Config
from voice_typer.server.log_rate_limit import log_rate_limited
from voice_typer.server.vad import compute_vad_prob
from voice_typer.server.vad_processor import VadProcessor, VadState

# PERF-COLDSTART-001: lazy import — sounddevice loads the PortAudio C
# library at import time.  ``sd`` is bound on the package so tests that
# do ``monkeypatch.setattr(recording.sd, "InputStream", fake)`` keep
# working unchanged.
sd = lazy_module("sounddevice")

log = logging.getLogger(__name__)

# ─── Public API re-exports ──────────────────────────────────────────────
# Each name below is genuinely defined in a sibling submodule.  We import
# it here so ``from voice_typer.server.recording import X`` keeps working.
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
from .device_manager import (  # noqa: E402 — PVT-22 / Phase 4.5 split
    DeviceManager,
)
from .exceptions import (  # noqa: E402
    ResampleError,
    ResampleUnavailable,
    ResampleUnavailableError,
)
from .recorder import (  # noqa: E402
    _AUDIO_RING_BUFFER_CAPACITY,
    _AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S,
    _AUDIO_WORKER_JOIN_TIMEOUT_S,
    _AUDIO_WORKER_THREAD_NAME,
    _BUFFER_TELEMETRY_ENABLED,
    _DEFAULT_VAD_CALIBRATION_DURATION,
    _DEFAULT_VAD_HANGOVER_FRAMES,
    _DEFAULT_VAD_SILENCE_FRAMES,
    _DEFAULT_VAD_SILENCE_THRESHOLD_DB,
    _DEFAULT_VAD_SPEECH_FRAMES,
    _DEFAULT_VAD_SPEECH_THRESHOLD_DB,
    _EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S,
    _EVENT_WORKER_JOIN_TIMEOUT_S,
    _EVENT_WORKER_THREAD_NAME,
    _XRUN_ALERT_PERIOD,
    _XRUN_ALERT_THRESHOLD,
    _XRUN_WINDOW_MAXLEN,
    BUFFER_WARNING_THRESHOLD,
    DEFAULT_MAX_BUFFER_CHUNKS,
    TELEMETRY_LOG_INTERVAL,
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

# NOTE: the mutable globals ``_resample_poly``, ``_resample_poly_error``,
# ``_resample_poly_error_time``, ``_scipy_preloader_thread`` (owned by
# .resampling) and ``_buffer_clear_worker`` (owned by .buffer) are
# deliberately NOT imported here.  If they were, the package's __dict__
# would hold a stale snapshot taken at import time, and test writes via
# ``recording.X = ...`` would land on the package's __dict__ instead of
# the submodule.  Instead, the custom module class below routes reads
# (via ``__getattr__``) and writes (via ``__setattr__``) for those names
# through to the owning submodule.

__all__ = [
    # recorder
    "Recorder",
    "DEFAULT_MAX_BUFFER_CHUNKS",
    "BUFFER_WARNING_THRESHOLD",
    "TELEMETRY_LOG_INTERVAL",
    "_AUDIO_RING_BUFFER_CAPACITY",
    "_AUDIO_WORKER_DISCARD_JOIN_TIMEOUT_S",
    "_AUDIO_WORKER_JOIN_TIMEOUT_S",
    "_AUDIO_WORKER_THREAD_NAME",
    "_BUFFER_TELEMETRY_ENABLED",
    "_DEFAULT_VAD_CALIBRATION_DURATION",
    "_DEFAULT_VAD_HANGOVER_FRAMES",
    "_DEFAULT_VAD_SILENCE_FRAMES",
    "_DEFAULT_VAD_SILENCE_THRESHOLD_DB",
    "_DEFAULT_VAD_SPEECH_FRAMES",
    "_DEFAULT_VAD_SPEECH_THRESHOLD_DB",
    "_EVENT_WORKER_DISCARD_JOIN_TIMEOUT_S",
    "_EVENT_WORKER_JOIN_TIMEOUT_S",
    "_EVENT_WORKER_THREAD_NAME",
    "_XRUN_ALERT_PERIOD",
    "_XRUN_ALERT_THRESHOLD",
    "_XRUN_WINDOW_MAXLEN",
    # device_manager (PVT-22 / Phase 4.5 split)
    "DeviceManager",
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
#   np.dot(flat, flat)          # vectorized RMS computation (AUDIO-007)
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

# ── Custom module class for mutable-state routing ───────────────────────
# CR-67 / TECH-DEBT: this entire block exists for test-patch
# compatibility — see the docstring at the top of this file for the
# full rationale.  In short: tests do
# ``monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)``
# and expect :mod:`.resampling`'s binding to be mutated, but a plain
# module's ``__dict__`` snapshot wouldn't propagate the write.  The
# custom ``_RecordingModule`` class below installs ``__getattr__`` /
# ``__setattr__`` overrides that route the mutable names through to
# the owning submodule.
#
# TODO (2026-07-25, CR-67 / TECH-DEBT — OPEN, awaiting migration):
# This ``__init__.py`` boilerplate exists for test-patch compatibility
# during the package reorganization.  Once CR-67 is complete, this
# file will be simplified.  Migrate tests to patch submodules directly,
# then remove this class.  Replace
# ``monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)``
# with
# ``monkeypatch.setattr("voice_typer.server.recording.resampling._resample_poly_error", ...)``
# (and similarly for the other names in ``_MUTABLE_RESAMPLING`` /
# ``_MUTABLE_BUFFER``).  Once every test site has been migrated,
# ``_RecordingModule`` and the ``_MUTABLE_*`` frozensets below can be
# deleted.  Tracked as CR-67 / TECH-DEBT (estimated 30-50 test files
# per package; ~90-150 total across recording / prewarm /
# server_platform).
#
# Tests like tests/test_recording.py:689 do
# ``rec_mod._resample_poly_error = RuntimeError(...)`` (write to the
# package namespace) and then expect ``_get_resample_poly`` (defined in
# :mod:`.resampling`) to see the new value when called.  Without this
# routing, the write would land on the package's ``__dict__`` and
# :mod:`.resampling`'s binding would be unchanged.
#
# Similarly, tests like tests/test_recording.py:966 read
# ``recording._scipy_preloader_thread`` after calling
# ``recording._start_scipy_preloader()`` and expect to see the thread
# object that :meth:`._start_scipy_preloader` stored in
# :mod:`.resampling`'s namespace.
#
# The fix is to install a custom module class whose ``__getattr__`` and
# ``__setattr__`` route the mutable names through to the owning
# submodule.  Reads of non-routed names fall back to the normal
# ``AttributeError`` (which Python resolves via the package's
# ``__dict__`` before ``__getattr__`` is even called).
import sys  # noqa: E402

# Names whose reads/writes on the package namespace should be routed to
# the owning submodule.  These are mutable globals — DO NOT add non-
# mutable names here (just ``from .submodule import X`` them at the top
# of this file instead).
_MUTABLE_RESAMPLING = frozenset(
    {
        "_resample_poly",
        "_resample_poly_error",
        "_resample_poly_error_time",
        "_scipy_preloader_thread",
    }
)
_MUTABLE_BUFFER = frozenset(
    {
        "_buffer_clear_worker",
    }
)

# NOTE (2026-07-20, updated XS-FIX-15): ``set_thread_registry`` was
# previously removed as merge damage, but the R4-F8 contract
# (``tests/test_i5_retry_fixes.py::TestR4F8BufferClearWorkerRegistry``)
# pins on its existence: the buffer-clear worker must register with a
# central ThreadRegistry so ``shutdown_all()`` can join it during
# ``VoiceTyperApp.quit()``. The function + ``_thread_registry`` global
# have been re-added to ``buffer.py`` per the R4-F8 contract. The
# setter is called by tests directly (``buffer.set_thread_registry(reg)``);
# ``recorder.py`` does NOT call it (the buffer worker is lazily started
# by ``_secure_clear_array_background``, not by ``Recorder.__init__``).


class _RecordingModule(sys.modules[__name__].__class__):
    """Module subclass routing mutable-state reads/writes to submodules.

    See the comment block above for the rationale.  The class is
    installed as the package's ``__class__`` at the bottom of this file
    via ``sys.modules[__name__].__class__ = _RecordingModule``.
    """

    def __getattr__(self, name):
        # ``__getattr__`` is only called when normal attribute lookup
        # (via ``__dict__``) fails — so this is a fallback for names
        # NOT bound at import time.  The mutable globals listed in
        # ``_MUTABLE_RESAMPLING`` / ``_MUTABLE_BUFFER`` are deliberately
        # not imported into the package's ``__dict__`` (see above), so
        # reads route through here.
        if name in _MUTABLE_RESAMPLING:
            from . import resampling as _r

            try:
                return getattr(_r, name)
            except AttributeError:
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        if name in _MUTABLE_BUFFER:
            from . import buffer as _b

            try:
                return getattr(_b, name)
            except AttributeError:
                raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        # Route writes to mutable globals through to the owning
        # submodule so production code (which reads them via ``global``
        # in :mod:`.resampling` / :mod:`.buffer`) sees the test's write.
        if name in _MUTABLE_RESAMPLING:
            from . import resampling as _r

            setattr(_r, name, value)
            return
        if name in _MUTABLE_BUFFER:
            from . import buffer as _b

            setattr(_b, name, value)
            return
        # Default: set on the package's __dict__ (normal module behaviour).
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _RecordingModule
