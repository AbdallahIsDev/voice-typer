"""macOS CoreAudio event-driven microphone device watcher.

PERF-MIC-001 / Task 15: native CoreAudio property listener — receives
event-driven notifications when the audio device list changes,
eliminating the 1/s polling wakeups used by the generic
``MicrophoneDeviceWatcher`` on macOS.

Platform support
----------------
- **macOS only**: uses ``pyobjc-framework-CoreAudio``'s
  ``AudioObjectAddPropertyListener`` on ``kAudioHardwarePropertyDevices``
  with a ``CFRunLoop`` on the watcher thread. When a device is added or
  removed, CoreAudio invokes the listener proc, which fires the
  invalidation callback. No polling.
- On non-macOS platforms, importing the module succeeds (only stdlib
  at the top level) but ``_try_import_coreaudio`` and
  ``CoreAudioMicrophoneWatcher.start`` raise ``ImportError``.
- If ``pyobjc-framework-CoreAudio`` (or its CoreFoundation companion)
  is not installed on macOS, instantiation also raises ``ImportError``
  so the caller (``microphone_watcher.py``) can fall back to polling.

The public API matches ``MicrophoneDeviceWatcher``:

- ``__init__(on_change, poll_interval=1.0)`` — ``poll_interval`` is
  accepted for drop-in compatibility but ignored (the listener is
  event-driven).
- ``start()`` — spawns a daemon thread that registers the property
  listener and runs ``CFRunLoopRun()`` in a blocking loop.
- ``stop()`` — calls ``CFRunLoopStop()`` on the watcher's run loop
  from the calling thread, then joins the watcher thread (2 s
  timeout).

The watcher is best-effort: any failure inside ``start()`` or the
watcher thread is logged and swallowed so the 30 s TTL cache in
``recording.py`` remains the ultimate backstop.

Cross-platform import safety
----------------------------
The top-level imports are stdlib-only (``logging``, ``sys``,
``threading``, ``typing``). The pyobjc imports happen lazily inside
``_try_import_coreaudio`` so that ``from voice_typer.server import
microphone_watcher_coreaudio`` succeeds on Linux/Windows even though
``pyobjc-framework-CoreAudio`` is macOS-only.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

log = logging.getLogger(__name__)

# Platform flag captured at import time so the lazy-import helper can
# short-circuit on non-macOS without inspecting sys.platform every
# call. Stored as a module-level attribute so tests can patch it.
_IS_MACOS = sys.platform == "darwin"

# Sentinel value for "noErr" — the OSStatus success code returned by
# CoreAudio functions. Defined as a constant rather than importing
# ``kAudioHardwareNoError`` to avoid an extra pyobjc dependency at
# the call site.
_NO_ERR = 0


def _try_import_coreaudio() -> SimpleNamespace:
    """Lazy import of pyobjc CoreAudio / CoreFoundation symbols.

    Returns a ``SimpleNamespace`` exposing the symbols needed by the
    watcher. Raises ``ImportError`` (with a clear, actionable message)
    on non-macOS platforms or when ``pyobjc-framework-CoreAudio`` /
    ``pyobjc-framework-CoreFoundation`` is not installed.

    The caller (``CoreAudioMicrophoneWatcher.start``) is expected to
    catch ``ImportError`` and fall back to the polling watcher.
    """
    if not _IS_MACOS:
        raise ImportError(
            "CoreAudioMicrophoneWatcher is only available on macOS "
            f"(current platform: {sys.platform}). Use the polling "
            "MicrophoneDeviceWatcher instead."
        )

    try:
        from CoreAudio import (  # noqa: PLC0415 — lazy import is the point
            AudioObjectAddPropertyListener,
            AudioObjectRemovePropertyListener,
            kAudioHardwarePropertyDevices,
            kAudioObjectPropertyElementMaster,
            kAudioObjectPropertyScopeGlobal,
            kAudioObjectSystemObject,
        )
        from CoreFoundation import (  # noqa: PLC0415 — lazy import is the point
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            CFRunLoopStop,
        )
    except ImportError as exc:
        raise ImportError(
            "pyobjc-framework-CoreAudio and pyobjc-framework-CoreFoundation "
            "are required for CoreAudioMicrophoneWatcher. Install with: "
            "pip install pyobjc-framework-CoreAudio "
            "pyobjc-framework-CoreFoundation"
        ) from exc

    return SimpleNamespace(
        add_listener=AudioObjectAddPropertyListener,
        remove_listener=AudioObjectRemovePropertyListener,
        property_devices=kAudioHardwarePropertyDevices,
        element_master=kAudioObjectPropertyElementMaster,
        scope_global=kAudioObjectPropertyScopeGlobal,
        system_object=kAudioObjectSystemObject,
        runloop_get_current=CFRunLoopGetCurrent,
        runloop_run=CFRunLoopRun,
        runloop_stop=CFRunLoopStop,
    )


class CoreAudioMicrophoneWatcher:
    """Event-driven macOS microphone watcher using CoreAudio property listeners.

    Drop-in replacement for the macOS branch of
    ``MicrophoneDeviceWatcher``: instead of polling
    ``sounddevice.query_devices()`` at 1 Hz, registers a CoreAudio
    property listener on ``kAudioHardwarePropertyDevices`` that fires
    the callback the instant a device is added or removed. This
    reduces wakeups from 1/s to event-driven (zero idle wakeups).

    See module docstring for platform behavior and fallback semantics.
    """

    def __init__(
        self,
        on_change: Callable[[], None],
        poll_interval: float = 1.0,  # accepted for API compat, ignored
    ) -> None:
        self._on_change = on_change
        # ``poll_interval`` is accepted but ignored — the watcher is
        # event-driven and has no polling cadence. Kept on the
        # instance only for API parity with ``MicrophoneDeviceWatcher``.
        self._poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Reference to the watcher thread's CFRunLoop, set by the
        # thread itself before entering ``CFRunLoopRun()`` so ``stop()``
        # can call ``CFRunLoopStop()`` from the calling thread.
        self._run_loop: Any = None
        # The listener proc must be kept alive for the lifetime of the
        # registration — CoreAudio does not retain it. Storing it on
        # the instance prevents the GC from collecting the wrapper
        # pyobjc builds around the Python callable, which would cause
        # a use-after-free crash inside CoreAudio.
        self._listener_proc: Callable[..., int] | None = None
        # pyobjc symbols — loaded lazily in ``start()`` so the module
        # is importable on non-macOS platforms without raising.
        self._ca: SimpleNamespace | None = None
        # lifecycle lock. Serialises ``start()``/``stop()``
        # so two concurrent callers can't both pass the idempotency
        # guard and register two CoreAudio property listeners on
        # ``kAudioHardwarePropertyDevices`` (double-firing callbacks +
        # potential listener-proc UAF under rapid start/stop). The
        # watcher thread briefly acquires this lock (in ``_run_impl``)
        # to publish ``_run_loop``/``_listener_proc``; ``stop()`` uses
        # the snapshot-then-act pattern (capture under the lock,
        # release, then ``CFRunLoopStop`` + ``join`` outside) so the
        # join doesn't deadlock against the watcher thread's lock
        # acquisition.
        self._lock = threading.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the watcher thread.

                Idempotent: calling ``start()`` twice does not spawn a second
                thread.

                Raises
                ------
                ImportError
                    If ``pyobjc-framework-CoreAudio`` is not available, or if
                    the platform is not macOS. The caller is expected to catch
                    ``ImportError`` and fall back to the polling watcher.

        the entire body runs under ``self._lock`` so two
                concurrent ``start()`` callers can't both pass the idempotency
                guard. The lazy pyobjc import is fast (no blocking), and
                ``threading.Thread.start()`` is non-blocking, so holding the
                lock here is deadlock-free.
        """
        with self._lock:
            if self._thread is not None:
                return
            # Lazy import — raises ImportError if pyobjc is missing or
            # we're not on macOS. The caller catches this and falls back
            # to the polling watcher.
            self._ca = _try_import_coreaudio()
            self._stop_event.clear()
            self._run_loop = None
            self._listener_proc = None
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="mic-coreaudio-watcher",
            )
            self._thread.start()
        log.info("[MIC-WATCHER-CA] Started CoreAudio property-listener watcher")

    def stop(self) -> None:
        """Signal the watcher thread to stop and join it (timeout 2 s).

                Idempotent: calling ``stop()`` twice is safe. After ``stop()``
                returns, the watcher can be ``start()``-ed again.

        ``thread`` / ``run_loop`` / ``ca`` are captured
                atomically under ``self._lock``, then the lock is released
                before ``CFRunLoopStop`` + ``join``. The lock is NOT held
                during the join because the watcher thread briefly acquires
                it (in ``_run_impl``) to publish ``self._run_loop`` before
                entering ``CFRunLoopRun`` — holding it through the join would
                deadlock if ``stop()`` ran before the watcher thread had a
                chance to set ``_run_loop``. Clearing ``_thread`` under the
                lock (before the join) makes a concurrent ``stop()`` a no-op
                and a concurrent ``start()`` safe to spawn a fresh watcher
                immediately.
        """
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            # Snapshot the run_loop + pyobjc symbol table atomically.
            # A torn snapshot (run_loop set but ca cleared, or vice
            # versa) would either skip ``CFRunLoopStop`` (leak) or
            # pass a stale run_loop to a freed symbol (UAF).
            run_loop = self._run_loop
            ca = self._ca
            # Clear refs FIRST so a concurrent stop() is a no-op
            # (idempotent) and a concurrent start() can spawn a fresh
            # watcher immediately.
            self._thread = None
            self._run_loop = None
            self._listener_proc = None
            self._ca = None
            self._stop_event.set()

        # Outside the lock: ``CFRunLoopStop`` is thread-safe and causes
        # ``CFRunLoopRun()`` to return on the watcher thread. The
        # watcher thread then runs its cleanup (removes the listener
        # via the local ``_listener`` closure, which still holds a
        # strong reference) and exits.
        if run_loop is not None and ca is not None:
            try:
                ca.runloop_stop(run_loop)
            except Exception:
                log.debug("[MIC-WATCHER-CA] CFRunLoopStop failed", exc_info=True)
        thread.join(timeout=2.0)
        if thread.is_alive():
            log.warning(
                "[MIC-WATCHER-CA] Watcher thread did not exit within 2s "
                "(it is a daemon and will not block process exit)"
            )
        log.info("[MIC-WATCHER-CA] Stopped CoreAudio watcher")

    # ── thread entry point ────────────────────────────────────────────

    def _run(self) -> None:
        """Thread target — registers the listener and runs CFRunLoop.

        Catches any exception so a watcher crash never propagates to
        the caller. The 30 s TTL cache in ``recording.py`` covers the
        case where the watcher thread dies.
        """
        ca = self._ca
        if ca is None:
            return
        try:
            self._run_impl(ca)
        except Exception:
            log.warning(
                "[MIC-WATCHER-CA] Watcher thread crashed, falling back to TTL polling",
                exc_info=True,
            )

    def _run_impl(self, ca: SimpleNamespace) -> None:
        """Implementation of ``_run`` — separated so ``_run`` can catch exceptions.

        Builds the property address, defines the listener proc, registers
        it via ``AudioObjectAddPropertyListener`` on the system audio
        object, then enters ``CFRunLoopRun()``. On exit, removes the
        listener best-effort.
        """
        # Build the property address for "device list changed":
        # (selector, scope, element). pyobjc accepts a 3-tuple for the
        # ``AudioObjectPropertyAddress`` struct argument.
        #   - mSelector = kAudioHardwarePropertyDevices (fires when the
        #     global device list changes — add/remove/unplug).
        #   - mScope    = kAudioObjectPropertyScopeGlobal (whole object).
        #   - mElement  = kAudioObjectPropertyElementMaster (master element).
        address = (
            ca.property_devices,
            ca.scope_global,
            ca.element_master,
        )

        # The listener proc is invoked by CoreAudio on the watcher
        # thread (the one running ``CFRunLoopRun``). Signature:
        #     OSStatus listener(AudioObjectID inObjectID,
        #                       UInt32 inNumberAddresses,
        #                       const AudioObjectPropertyAddress *inAddresses,
        #                       void *inClientData)
        # pyobjc marshals it as a 4-arg Python callable. We ignore
        # the arguments (we already know which property changed) and
        # fire the invalidation callback.
        #
        # call ``self._on_change()`` directly instead of
        # going through a redundant ``_invoke_callback`` wrapper.
        # When this watcher is constructed by
        # ``MicrophoneDeviceWatcher._try_create_coreaudio_watcher``
        # (the normal path), ``_on_change`` IS
        # ``MicrophoneDeviceWatcher._invoke_callback`` — which already
        # wraps the raw callback in try/except + 0.5 s debounce +
        # active-mic-lost detection. A second try/except here would
        # just mask bugs in the outer wrapper. Any exception that
        # does escape is caught by ``_run``'s top-level try/except so
        # the watcher thread never crashes the process.
        def _listener(
            in_object_id: Any,
            in_number_addresses: Any,
            in_addresses: Any,
            in_client_data: Any,
        ) -> int:
            log.debug(
                "[MIC-WATCHER-CA] CoreAudio device-list changed (object_id=%s, n_addresses=%s)",
                in_object_id,
                in_number_addresses,
            )
            self._on_change()
            return _NO_ERR  # noErr

        # Keep a strong reference — CoreAudio does not retain the proc,
        # and pyobjc's wrapper would be GC'd if the only reference
        # were the local variable, causing a crash on the next
        # property change.
        # publish under ``self._lock`` so ``stop()`` sees a
        # consistent (``_listener_proc``, ``_run_loop``) pair when it
        # snapshots. The lock is released before ``add_listener`` /
        # ``CFRunLoopRun`` so the watcher thread never holds it across
        # a blocking CoreAudio call.
        with self._lock:
            self._listener_proc = _listener

        # Register the listener on the system audio object.
        # ``kAudioObjectSystemObject`` is the root ``AudioObject``; its
        # ``kAudioHardwarePropertyDevices`` property is the list of all
        # audio devices. Listening on it fires whenever a device is
        # added or removed (USB headset plugged in, Bluetooth mic
        # connected, etc.).
        try:
            status = ca.add_listener(
                ca.system_object,
                address,
                _listener,
                None,
            )
        except Exception:
            # pyobjc raises (rather than returning an OSStatus) for
            # some failure modes — treat them all as "registration
            # failed" and fall back to TTL polling.
            log.warning(
                "[MIC-WATCHER-CA] AudioObjectAddPropertyListener raised, falling back to TTL polling",
                exc_info=True,
            )
            return

        if status != _NO_ERR:
            log.warning(
                "[MIC-WATCHER-CA] AudioObjectAddPropertyListener failed (status=%d), falling back to TTL polling",
                status,
            )
            return

        # Capture the current thread's CFRunLoop so ``stop()`` can
        # wake it from another thread. Must be done BEFORE
        # ``CFRunLoopRun`` because ``CFRunLoopRun`` blocks.
        # publish under ``self._lock`` so ``stop()``'s
        # snapshot of (``_run_loop``, ``_ca``) is atomic with the
        # assignment. The lock is NOT held during ``CFRunLoopRun``
        # (which blocks) — that would deadlock ``stop()``.
        with self._lock:
            self._run_loop = ca.runloop_get_current()

        log.debug("[MIC-WATCHER-CA] listener registered, entering CFRunLoop")
        # ``CFRunLoopRun`` blocks until ``CFRunLoopStop`` is called
        # from another thread (or a run-loop source signals stop).
        ca.runloop_run()

        # Cleanup — remove the listener. Best-effort: if this fails
        # the listener leaks but the watcher thread is dying anyway,
        # and the 30 s TTL cache in ``recording.py`` covers missed
        # notifications.
        try:
            ca.remove_listener(
                ca.system_object,
                address,
                _listener,
                None,
            )
        except Exception:
            log.debug(
                "[MIC-WATCHER-CA] AudioObjectRemovePropertyListener failed",
                exc_info=True,
            )
