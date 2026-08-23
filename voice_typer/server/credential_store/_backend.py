"""Keyring availability probing, timeout isolation, and global caches.

Owns the "keyring availability probing + global caches" concern AND the
keyring-I/O timeout-isolation machinery (the per-call worker thread +
orphan/wedge tracker). The constants :data:`_KEYRING_TIMEOUT_SECONDS`,
:data:`_KEYRING_WEDGE_COOLDOWN_S`, :data:`_KEYRING_ORPHAN_WARN_THRESHOLD`,
the function :data:`_probe_keyring`, the function
:func:`is_keyring_available`, and the dict :data:`_plaintext_config_cache`
are all monkey-patched by tests via
``monkeypatch.setattr(credential_store, "<name>", ...)``. Such patches set
the attribute on the *package* module
(``voice_typer.server.credential_store``), so call sites that need to
observe the patched value look it up via ``_cs.<name>`` (attribute access
on the package module) rather than via bare-name global lookup against
this submodule's ``__dict__``. The package module is fully loaded by
the time any of these functions is called, so the attribute access is
safe at call time.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from ._redact import _redact_sensitive
from ._schema import _T, KEYRING_SERVICE_NAME, log

#: Look up the package module (used so test-time monkey-patches on
#: ``voice_typer.server.credential_store`` propagate to call sites here).
_cs = sys.modules["voice_typer.server.credential_store"]

# ── Keyring I/O timeout isolation ────────────────────────────────────────
#
# keyring backends call into platform IPC (D-Bus on Linux, Keychain
# daemon on macOS, Credential Manager service on Windows). Any of these
# can block for up to ~30s (D-Bus default timeout) or indefinitely
# (Keychain waiting for an unlock prompt). When called synchronously
# from the IPC ``set_config`` handler thread or from ``Config.load()``
# at startup (× 5 providers), a single hung backend wedges the entire
# process for 30-150s.
#
# Mitigation: every keyring I/O call runs in a fresh daemon worker
# thread. The caller awaits the thread's result with a finite timeout
# (5s, well under the worst-case D-Bus timeout). On timeout, the caller
# treats it as a keyring failure and falls through to the existing
# plaintext fallback (``store_secret`` / ``load_secret``) or skips the
# provider (``migrate_secrets_to_keyring``).
_KEYRING_TIMEOUT_SECONDS = 5.0

#: When the backend times out twice in a row, we assume it is wedged
#: and short-circuit every subsequent call for this many seconds.
_KEYRING_WEDGE_COOLDOWN_S = 60.0

#: Orphaned keyring-io threads are normally bounded by the IPC handler
#: thread pool size. If the count exceeds this threshold we log a
#: WARNING so operators can diagnose a permanently-stuck backend.
_KEYRING_ORPHAN_WARN_THRESHOLD = 20

# Module-level state for the orphan/wedge tracking. All accesses are
# guarded by :data:`_keyring_state_lock`.
_keyring_state_lock = threading.Lock()
_orphaned_thread_count: int = 0
_consecutive_timeouts: int = 0
_wedged_until: float = 0.0

# ── Keyring availability caches ──────────────────────────────────────────
# Cached result of is_keyring_available(). None = not yet probed.
_keyring_available_cache: bool | None = None
# Cached backend name. Preserved even when unavailable for diagnostics.
_keyring_backend_name_cache: str | None = None
# Cached reason string (already redacted). None when available, or when
# not yet probed.
_keyring_reason_cache: str | None = None
# Monotonic time (seconds) of the most recent keyring probe.
_keyring_last_probe_ts: float = 0.0
# Minimum seconds between two on-demand re-probes when the cache says
# "unavailable". 300s (5 min) is short enough that a backend started
# mid-session is picked up within a typical user interaction, and long
# enough that a tight ``load_secret`` loop doesn't re-probe 5 times.
_KEYRING_REPROBE_INTERVAL_SECONDS: float = 300.0
# Serializes re-probes so two concurrent ``load_secret`` calls don't
# each fire a probe.
_keyring_probe_lock = threading.Lock()

#: Cache for parsed ``config.json`` in :func:`_read_plaintext_fallback`.
#: Keyed by config_file path, value is ``(mtime_ns, parsed_dict)``.
#: ``Config.load()`` resolves ``keyring://<provider>`` references by
#: calling ``load_secret()`` for each of the 5 providers — without this
#: cache, each call re-opens and re-parses the same config.json (5 reads
#: + 5 parses at startup when keyring is unavailable). Lives here
#: (rather than in :mod:`_plaintext`) because the "global caches"
#: concern is owned by this module; :func:`_clear_plaintext_config_cache`
#: also lives here so the GDPR-delete path can invalidate the cache from
#: a single location.
_plaintext_config_cache: dict[str, tuple[int, dict]] = {}


def _run_keyring_call(func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run a keyring backend call under a finite timeout.

    Spawns a daemon worker thread for the call (a pooled executor's
    single worker would queue subsequent calls behind a hung one,
    defeating the timeout). Raises ``TimeoutError`` if the call doesn't
    complete within ``_KEYRING_TIMEOUT_SECONDS``.

    Orphan / wedge tracking
    -----------------------
    When the backend hangs, the worker thread is abandoned (Python
    can't kill threads). Each abandoned thread is counted in
    :data:`_orphaned_thread_count`; the counter is decremented when the
    orphan eventually finishes. If the orphan count exceeds
    :data:`_KEYRING_ORPHAN_WARN_THRESHOLD`, a WARNING is logged.

    On the 2nd *consecutive* timeout, :data:`_wedged_until` is set to
    ``now + _KEYRING_WEDGE_COOLDOWN_S``. While the cooldown is active,
    every call short-circuits with a ``TimeoutError`` without spawning
    another worker thread. When the cooldown expires, the next call is
    attempted fresh.

    The timeout / cooldown / threshold values are read from the
    *package* module (``_cs.<NAME>``) at call time so test-time
    monkey-patches on ``voice_typer.server.credential_store`` propagate
    here — see the module docstring for the pattern rationale.
    """
    global _orphaned_thread_count, _consecutive_timeouts, _wedged_until

    keyring_timeout_seconds = _cs._KEYRING_TIMEOUT_SECONDS
    keyring_wedge_cooldown_s = _cs._KEYRING_WEDGE_COOLDOWN_S
    keyring_orphan_warn_threshold = _cs._KEYRING_ORPHAN_WARN_THRESHOLD

    # ── Wedge short-circuit ────────────────────────────────────────────
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
        "completed": False,  # set under lock in the runner's finally
        "orphaned": False,  # set under lock by the caller on timeout
    }

    def _runner() -> None:
        global _orphaned_thread_count
        try:
            state["result"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            state["exc"] = exc
        finally:
            # Atomically mark completion AND decrement the orphan
            # counter if the caller already counted us.
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
        # bump the counters / wedge state.
        with _keyring_state_lock:
            if state["completed"]:
                # Race: the thread finished between ``t.is_alive()``
                # and the lock acquisition. Its ``finally`` already
                # ran with ``orphaned=False`` (so it didn't
                # decrement). Don't increment either — fall through
                # to the normal result-handling path below.
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
                        "timeouts — short-circuiting all calls for %.0fs",
                        consecutive,
                        keyring_wedge_cooldown_s,
                    )
                if orphan_count > keyring_orphan_warn_threshold:
                    log.warning(
                        "[CREDENTIAL] %d orphaned keyring-io threads still running "
                        "(threshold %d) — backend may be permanently stuck",
                        orphan_count,
                        keyring_orphan_warn_threshold,
                    )
                raise TimeoutError(
                    f"keyring call {getattr(func, '__name__', repr(func))} did not "
                    f"complete within {keyring_timeout_seconds}s "
                    f"(orphaned threads: {orphan_count}, consecutive timeouts: {consecutive})"
                )

    # Call completed (success or exception) — reset the consecutive
    # timeout counter so a single success after a wedged state gives
    # the backend a clean slate.
    with _keyring_state_lock:
        if _consecutive_timeouts != 0:
            _consecutive_timeouts = 0

    if state["exc"] is not None:
        raise state["exc"]
    return state["result"]


def _probe_keyring() -> tuple[bool, str | None, str | None]:
    """Probe the keyring library and return ``(available, backend_name, reason)``.

    ``available`` is True only when a real backend is installed — the
    ``keyring.backends.fail.Keyring`` backend raises on every operation,
    so we treat it as unavailable and fall back to plaintext.

    The probe is wrapped in a broad ``except Exception`` because the
    keyring library can raise a variety of errors during backend
    selection (D-Bus connection errors on Linux, missing pyobjc on
    macOS, missing pywin32 on Windows). All of these mean "no usable
    backend" from our perspective.

    The returned ``reason`` (when not None) is passed through
    :func:`_redact_sensitive` to strip filesystem paths and
    API-key-like substrings — defense in depth against buggy or custom
    keyring backends that might embed sensitive data in their exception
    text.
    """
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
    # technically "selected" but raise on every operation. Probe with a
    # benign read to confirm the backend actually works.
    try:
        # Use a sentinel username that we never store under to avoid
        # accidentally returning a real secret.
        _run_keyring_call(backend.get_password, KEYRING_SERVICE_NAME, "__keyring_probe__")
    except Exception as e:
        return (
            False,
            type(backend).__name__,
            _redact_sensitive(f"keyring backend probe failed: {e}"),
        )

    return True, type(backend).__name__, None


def is_keyring_available() -> bool:
    """Return True if a usable keyring backend is installed.

    Caching policy (re-probe on demand):

    - When the cache says **available** (True), the result is cached
      for the lifetime of the process.
    - When the cache says **unavailable** (False), the result is cached
      only until :data:`_KEYRING_REPROBE_INTERVAL_SECONDS` seconds have
      elapsed since the last probe. After that interval, the next call
      re-probes. This picks up a backend that appears mid-session
      (e.g. ``gnome-keyring-daemon`` started while the app is running)
      without requiring an app restart. The re-probe is rate-limited
      so a tight ``load_secret`` loop (5 providers at startup) doesn't
      fire 5 probes back-to-back.
    - Tests that need to force re-probing can call
      :func:`_reset_keyring_cache` (which also clears the probe
      timestamp).
    """
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache, _keyring_last_probe_ts
    # The re-probe interval is read from the *package* module at call
    # time so a test that rebinds ``credential_store._KEYRING_REPROBE_INTERVAL_SECONDS``
    # is observed here (same pattern as ``_KEYRING_TIMEOUT_SECONDS`` in
    # :func:`_run_keyring_call`). The caches themselves are bare globals:
    # this function rebinds them, so they must live in this submodule.
    reprobe_interval = _cs._KEYRING_REPROBE_INTERVAL_SECONDS
    # Fast path: cache is populated AND either (a) the backend is
    # available (cached for process lifetime) or (b) the unavailable
    # result is still within the re-probe interval.
    if _keyring_available_cache is True:
        return True
    if _keyring_available_cache is False and _keyring_last_probe_ts is not None:
        elapsed = time.time() - _keyring_last_probe_ts
        if elapsed < reprobe_interval:
            return False
    # Slow path: probe (or re-probe). Serialize so two concurrent
    # ``load_secret`` calls don't each fire a probe.
    with _keyring_probe_lock:
        # Re-check under the lock — another thread may have probed
        # while we were waiting for the lock.
        if _keyring_available_cache is True:
            return True
        if (
            _keyring_available_cache is False
            and _keyring_last_probe_ts is not None
            and (time.time() - _keyring_last_probe_ts) < reprobe_interval
        ):
            return False
        # ``_probe_keyring`` is monkey-patched by tests via
        # ``monkeypatch.setattr(credential_store, "_probe_keyring", ...)``,
        # so look it up on the package module at call time.
        available, backend_name, reason = _cs._probe_keyring()
        _keyring_available_cache = available
        # Cache the backend name AND the reason so get_keyring_status()
        # can return a consistent snapshot without re-probing.
        _keyring_backend_name_cache = backend_name
        _keyring_reason_cache = reason
        _keyring_last_probe_ts = time.time()
    return _keyring_available_cache


def _reset_keyring_cache() -> None:
    """Test-only: clear the cached keyring availability result.

    Also clears the probe timestamp so the next
    :func:`is_keyring_available` call re-probes unconditionally.
    """
    global _keyring_available_cache, _keyring_backend_name_cache, _keyring_reason_cache, _keyring_last_probe_ts
    _keyring_available_cache = None
    _keyring_backend_name_cache = None
    _keyring_reason_cache = None
    _keyring_last_probe_ts = 0.0


def _clear_plaintext_config_cache() -> None:
    """Drop the cached parsed ``config.json`` dict.

    The module-level :data:`_plaintext_config_cache` holds the parsed
    config dict (which may contain plaintext API keys when keyring is
    unavailable). GDPR Art. 17 ``delete_all_personal_data`` zeroes the
    on-disk + in-memory ``Config`` attributes via
    :func:`delete_secret` / :func:`clear_in_memory_secrets`, but
    without this helper the stale parsed dict would persist in process
    memory until the next restart — a memory dump taken between the
    delete and the next restart would still contain the plaintext
    secrets.

    The cache dict is looked up on the *package* module at call time
    (``_cs._plaintext_config_cache``) so a test that does
    ``monkeypatch.setattr(credential_store, "_plaintext_config_cache", {})``
    sees the *patched* dict cleared (not the original).
    """
    _cs._plaintext_config_cache.clear()


def get_keyring_status() -> dict[str, Any]:
    """Return a status dict describing the current keyring backend.

    Returns
    -------
    dict with keys ``available``, ``backend``, ``fallback``, ``reason``.
    """
    # ``is_keyring_available`` is monkey-patched by tests via
    # ``monkeypatch.setattr(credential_store, "is_keyring_available", ...)``,
    # so look it up on the package module at call time.
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
