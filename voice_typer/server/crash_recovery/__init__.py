"""Crash recovery: stores last 10 transcriptions, checks on startup.

After each transcription, the text is saved to a recovery file.
On startup, if the recovery file has unpasted transcriptions,
the user is notified. The recovery file is cleared after acknowledgment.

previously, every call to ``add()``, ``mark_pasted()``,
``mark_latest_pasted()``, and ``clear()`` wrote to disk synchronously
on the calling thread (typically the transcription thread).  Under
repeated crashes or rapid transcriptions, these synchronous writes
blocked the main thread and could compound the crash condition by
delaying restart.  The fix moves disk writes to a dedicated background
thread with a bounded queue: callers enqueue a save request and
return immediately; the worker thread serializes the writes.

Package layout (split of the former 1412-LOC single-file monolith —
split by concern, bodies moved verbatim, behavior preserved):

- :mod:`._store`  -- ``CrashRecovery`` (ctor, instance state, public API)
- :mod:`._io`     -- ``_load`` / ``_quarantine_corrupt`` / ``_save_sync``
- :mod:`._worker` -- save-thread machinery + atexit flush + ``_LIVE_INSTANCES``

Patch-target contract: this ``__init__`` keeps every name the
pre-split module bound (imports, constants, helpers) so that
``voice_typer.server.crash_recovery.X`` — including monkeypatch
targets like ``...crash_recovery._secure_atomic_write`` and
``...crash_recovery.os.chmod`` — keeps resolving exactly as before;
the leaf modules resolve shared names through this facade (call-time
attribute read for ``_secure_atomic_write``) so facade rebindings stay
visible everywhere.
"""

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
# ``_save_sync`` doesn't need to lazily import it during interpreter
# shutdown (where the import machinery can fail, dropping the final
# recovery state).  ``config`` doesn't import ``crash_recovery``, so
# this is safe from circular-import issues.
from voice_typer.server.config import _secure_atomic_write as _secure_atomic_write

# Re-export the split-out concerns so every pre-split attribute of this
# module keeps resolving (tests read ``crash_recovery._LIVE_INSTANCES``
# and call ``crash_recovery._atexit_flush_all()`` directly).
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
# for the last ``MAX_RECOVERY_ENTRIES`` UNPASTED transcriptions. It is
# NOT obsolete: the dictation pipeline calls ``CrashRecovery.add()``
# (gated by ``config.crash_recovery_enabled``), and startup
# (``startup_sequence`` → ``check_on_startup``) reads it to notify the
# user of recovered text; it is also exported to diagnostics bundles.
# An empty ``{"entries": []}`` is the NORMAL state (nothing pending),
# not a signal that the file can be removed.

# Bounded queue: if the worker falls behind (e.g. disk is slow),
# drop the oldest pending save rather than blocking the transcription
# thread.  The latest state is what matters; intermediate states are
# not useful for crash recovery.
_SAVE_QUEUE_MAXSIZE = 32


# Keep the stdlib/config names importable from this module for parity
# with the pre-split namespace (e.g. tests patch
# ``voice_typer.server.crash_recovery.os.chmod``). The redundant
# ``import x as x`` aliases above mark every facade-parity binding as
# an intentional re-export for the linter — no behavior in these names,
# they exist so pre-split monkeypatch targets keep resolving.

atexit.register(_atexit_flush_all)
