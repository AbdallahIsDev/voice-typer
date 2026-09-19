"""Session-based audio recording."""

from __future__ import annotations

# ``event_bus`` and ``compute_vad_prob`` MUST be imported at module
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
np = lazy_module("numpy")

# PERF-COLDSTART-001: lazy import, sounddevice loads the PortAudio C
sd = lazy_module("sounddevice")

log = logging.getLogger(__name__)

# Each name below is genuinely defined in a sibling submodule.  We import
from .audio_pipeline import (  # noqa: E402, owner of the buffer-telemetry constants
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

# Phase 4.5 split, three new collaborator modules.
from .capture import (  # noqa: E402,  / Phase 4.5 split
    AudioCallbackDispatcher,
)
from .device_manager import (  # noqa: E402,  / Phase 4.5 split
    DeviceManager,
)
from .device_prewarm import (  # noqa: E402, Phase 4.5 completion
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
from .session_state import (  # noqa: E402,  / Phase 4.5 split
    SessionState,
)
from .stream_lifecycle import (  # noqa: E402,  / Phase 4.5 split
    StreamLifecycle,
)

# NOTE: the mutable globals ``_resample_poly``, ``_resample_poly_error``,

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

# A couple of regression tests still use ``inspect.getsource(recording)``

# NOTE (2026-07-20, updated ): ``set_thread_registry`` was
