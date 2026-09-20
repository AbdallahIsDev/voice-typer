"""Keyring availability probing, timeout isolation, and global caches."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from ._redact import _redact_sensitive
from ._schema import _T, KEYRING_SERVICE_NAME, log

#: Look up the package module (used so test-time monkey-patches on
_cs = sys.modules["voice_typer.server.credential_store"]

# keyring backends call into platform IPC (D-Bus on Linux, Keychain
_KEYRING_TIMEOUT_SECONDS = 5.0

#: When the backend times out twice in a row, we assume it is wedged
_KEYRING_WEDGE_COOLDOWN_S = 60.0

#: Orphaned keyring-io threads are normally bounded by the IPC handler
_KEYRING_ORPHAN_WARN_THRESHOLD = 20

# Module-level state for the orphan/wedge tracking. All accesses are
_keyring_state_lock = threading.Lock()
_orphaned_thread_count: int = 0
_consecutive_timeouts: int = 0
_wedged_until: float = 0.0

# Cached result of is_keyring_available(). None = not yet probed.
_keyring_available_cache: bool | None = None
# Cached backend name. Preserved even when unavailable for diagnostics.
_keyring_backend_name_cache: str | None = None
# Cached reason string (already redacted). None when available, or when
_keyring_reason_cache: str | None = None
# Monotonic time (seconds) of the most recent keyring probe.
_keyring_last_probe_ts: float = 0.0
# Minimum seconds between two on-demand re-probes when the cache says
_KEYRING_REPROBE_INTERVAL_SECONDS: float = 300.0
# Serializes re-probes so two concurrent ``load_secret`` calls don't
_keyring_probe_lock = threading.Lock()

#: Cache for parsed ``config.json`` in :func:`_read_plaintext_fallback`.
_plaintext_config_cache: dict[str, tuple[int, dict]] = {}


def _run_keyring_call(func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run a keyring backend call under a finite timeout."""
    global _orphaned_thread_count, _consecutive_timeouts, _wedged_until

    keyring_timeout_seconds = _cs._KEYRING_TIMEOUT_SECONDS
    keyring_wedge_cooldown_s = _cs._KEYRING_WEDGE_COOLDOWN_S
    keyring_orphan_warn_threshold = _cs._KEYRING_ORPHAN_WARN_THRESHOLD

    with _keyring_state_lock:
        now = time.monotonic()
        if 0.0 < _wedged_until <= now:
            _wedged_until = 0.0
            _consecutive_timeouts = 0
        if _wedged_until > now:
            remaining = _wedged_until - now
            raise TimeoutError(
                f"keyring backend is wedged (cooldown {remaining:.1f}s remaining); "
                f"short-circuiting call to {getattr(func, '__name__', repr(func))}"
            )

    state: dict[str, Any] = {
        "result": None,
        "exc": None,
        "completed": False,
        "orphaned": False,
    }

    def _runner() -> None:
        global _orphaned_thread_count
        try:
            state["result"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001, re-raised on the caller
            state["exc"] = exc
        finally:
            # Atomically mark completion AND decrement the orphan
            with _keyring_state_lock:
                state["completed"] = True
                if state["orphaned"]:
                    _orphaned_thread_count -= 1

    t = threading.Thread(
        target=_runner,
        name="keyring-io",
        daemon=True,
    )
    t.start()
    t.join(timeout=keyring_timeout_seconds)

    if t.is_alive():
        # Backend hung. Atomically mark this thread as orphaned and
        with _keyring_state_lock:
            if state["completed"]:
                # Race: the thread finished between ``t.is_alive()``
                pass
            else:
                state["orphaned"] = True
                _orphaned_thread_count += 1
                orphan_count = _orphaned_thread_count
                _consecutive_timeouts += 1
                consecutive = _consecutive_timeouts
                if consecutive >= 2:
                    _wedged_until = time.monotonic() + keyring_wedge_cooldown_s
                    log.warning(
                        "[CREDENTIAL] keyring backend wedged after %d consecutive "
                        "timeouts, short-circuiting all calls for %.0fs",
                        consecutive,
                        keyring_wedge_cooldown_s,
                    )
                if orphan_count > keyring_orphan_warn_threshold:
                    log.warning(
                        "[CREDENTIAL] %d orphaned keyring-io threads still running "
                        "(threshold %d), backend may be permanently stuck",
                        orphan_count,
                        keyring_orphan_warn_threshold,
                    )
                raise TimeoutError(
                    f"keyring call {getattr(func, '__name__', repr(func))} did not "
                    f"complete within {keyring_timeout_seconds}s "
                    f"(orphaned threads: {orphan_count}, consecutive timeouts: {consecutive})"
                )

    # Call completed (success or exception), reset the consecutive
    with _keyring_state_lock:
        if _consecutive_timeouts != 0:
            _consecutive_timeouts = 0

    if state["exc"] is not None:
        raise state["exc"]
    return state["result"]


def _probe_keyring() -> tuple[bool, str | None, str | None]:
    """Probe the keyring library and return ``(available, backend_name, reason)``."""
    try:
        from keyring.backends.fail import Keyring as FailKeyring  # type: ignore[import-not-found]

        import keyring  # type: ignore[import-not-found]
    except Exception as e:
        return False, None, _redact_sensitive(f"keyring import failed: {e}")

    try:
        backend = keyring.get_keyring()
    except Exception as e:
        return False, None, _redact_sensitive(f"keyring.get_keyring() raised: {e}")

    if isinstance(backend, FailKeyring):
        return False, "fail", "no usable keyring backend (fail backend selected)"

    # Some backends (e.g. libsecret on Linux without D-Bus) are
    try:
        # Use a sentinel username that we never store under to avoid
        _run_keyring_call(backend.get_password, KEYRING_SERVICE_NAME, "__keyring_probe__")
    except Exception as e:
        return (
            False,
            type(backend).__name__,
            _redact_sensitive(f"keyring backend probe failed: {e}"),
        )

    return True, type(backend).__name__, None


def is_keyring_available() -> bool:
    """Return True if a usable keyring backend is installed."""
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache, _keyring_last_probe_ts
    # The re-probe interval is read from the *package* module at call
    reprobe_interval = _cs._KEYRING_REPROBE_INTERVAL_SECONDS
    # Fast path: cache is populated AND either (a) the backend is
    if _keyring_available_cache is True:
        return True
    if _keyring_available_cache is False and _keyring_last_probe_ts is not None:
        elapsed = time.time() - _keyring_last_probe_ts
        if elapsed < reprobe_interval:
            return False
    # Slow path: probe (or re-probe). Serialize so two concurrent
    with _keyring_probe_lock:
        # Re-check under the lock, another thread may have probed
        if _keyring_available_cache is True:
            return True
        if (
            _keyring_available_cache is False
            and _keyring_last_probe_ts is not None
            and (time.time() - _keyring_last_probe_ts) < reprobe_interval
        ):
            return False
        # ``_probe_keyring`` is monkey-patched by tests via
        available, backend_name, reason = _cs._probe_keyring()
        _keyring_available_cache = available
        # Cache the backend name AND the reason so get_keyring_status()
        _keyring_backend_name_cache = backend_name
        _keyring_reason_cache = reason
        _keyring_last_probe_ts = time.time()
    return _keyring_available_cache


def _reset_keyring_cache() -> None:
    """Test-only: clear the cached keyring availability result."""
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache, _keyring_last_probe_ts
    _keyring_available_cache = None
    _keyring_backend_name_cache = None
    _keyring_reason_cache = None
    _keyring_last_probe_ts = 0.0


def _clear_plaintext_config_cache() -> None:
    """Drop the cached parsed ``config.json`` dict."""
    _cs._plaintext_config_cache.clear()


def get_keyring_status() -> dict[str, Any]:
    """Return a status dict describing the current keyring backend.

    Returns
    """
    # ``is_keyring_available`` is monkey-patched by tests via
    _cs.is_keyring_available()
    return {
        "available": bool(_keyring_available_cache),
        "backend": _keyring_backend_name_cache,
        "fallback": not bool(_keyring_available_cache),
        "reason": _redact_sensitive(_keyring_reason_cache),
    }


__all__ = [
    "_KEYRING_ORPHAN_WARN_THRESHOLD",
    "_KEYRING_REPROBE_INTERVAL_SECONDS",
    "_KEYRING_TIMEOUT_SECONDS",
    "_KEYRING_WEDGE_COOLDOWN_S",
    "_clear_plaintext_config_cache",
    "_consecutive_timeouts",
    "_keyring_available_cache",
    "_keyring_backend_name_cache",
    "_keyring_last_probe_ts",
    "_keyring_probe_lock",
    "_keyring_reason_cache",
    "_keyring_state_lock",
    "_orphaned_thread_count",
    "_plaintext_config_cache",
    "_probe_keyring",
    "_reset_keyring_cache",
    "_run_keyring_call",
    "_wedged_until",
    "get_keyring_status",
    "is_keyring_available",
]
