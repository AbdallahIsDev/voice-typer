"""Crash recovery: stores last 10 transcriptions, checks on startup."""

import atexit
import collections as collections
import contextlib as contextlib
import json as json
import logging
import os as os
import queue as queue
import threading as threading
import weakref as weakref
from pathlib import Path as Path
from typing import Any as Any

# Import _secure_atomic_write at module load time so
from voice_typer.server.config import _secure_atomic_write as _secure_atomic_write

# Re-export the split-out concerns so every pre-split attribute of this
from voice_typer.server.crash_recovery._store import CrashRecovery as CrashRecovery
from voice_typer.server.crash_recovery._worker import (
    _ATEXIT_FLUSH_TIMEOUT_S as _ATEXIT_FLUSH_TIMEOUT_S,
    _LIVE_INSTANCES as _LIVE_INSTANCES,
    _atexit_flush_all as _atexit_flush_all,
    _run_save_with_timeout as _run_save_with_timeout,
)
from voice_typer.server.platform_utils import is_windows as is_windows

log = logging.getLogger(__name__)

RECOVERY_FILENAME = "recovery.json"
_LEGACY_RECOVERY_FILENAME = "voice-typer-recovery.json"
MAX_RECOVERY_ENTRIES = 10

# Persistence role: ``recovery.json`` is an ACTIVE crash-recovery store

# Bounded queue: if the worker falls behind (e.g. disk is slow),
_SAVE_QUEUE_MAXSIZE = 32


# Keep the stdlib/config names importable from this module for parity

atexit.register(_atexit_flush_all)
