"""Core permission checker logic for the ``permissions`` package.

This submodule contains the platform-agnostic dispatchers, the retry
timer, the pyobjc availability cache, the error classifier, the
request dispatchers, and the tray notification helper. Platform-specific
probes live in the sibling submodules:

- :mod:`voice_typer.server.permissions.mic` — microphone probes.
- :mod:`voice_typer.server.permissions.accessibility` — macOS
  Accessibility probe.
- :mod:`voice_typer.server.permissions.filesystem` — Linux
  ``/dev/input/event*`` probe.

All mutable state (``_retry_timer``, ``_retry_count``, ``_cancelled``,
``_retry_lock``, ``_PYOBJC_AVAILABLE``), platform check functions
(``is_windows`` / ``is_macos`` / ``is_linux``), and constants
(``PERMISSION_RETRY_INTERVAL_SECONDS``, ``PERMISSION_RETRY_MAX_ATTEMPTS``,
``_PERMISSION_NOTIFY_*_KEY``, ``APP_NAME``) live on the facade
:mod:`voice_typer.server.permissions` and are accessed via
``_p.<name>`` so test monkeypatches on the facade propagate (mirrors
the crash_handler split pattern).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from enum import Enum

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


# ─── Permission state enums ────────────────────────────────────────────────


class PermissionState(str, Enum):
    """Three-state permission model.

        - ``GRANTED``: the OS reports we have the permission, or no
          permission is needed on this platform (e.g. Windows).
        - ``DENIED``: the OS reports we don't have the permission.
        - ``UNKNOWN``: we can't tell (e.g. macOS without pyobjc, or an
          unsupported platform). This is the "soft unknown" — the probe ran
          successfully but the answer is indeterminate.
    ``ERROR``:  — the probe itself failed unexpectedly (raised
          an exception). Distinct from ``UNKNOWN`` so the renderer can show
          "Permission probe failed — click to retry" instead of the
          misleading "No extra permission needed" (which it used to render
          for any state with ``needed=False``).
    """

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    ERROR = "error"


class MicrophonePermissionState(str, Enum):
    """three-state model for OS-level microphone permission.

    - ``GRANTED``: the OS reports we have microphone access (or no
      permission is needed on this platform / Linux).
    - ``DENIED``: the OS reports we don't have microphone access.
    - ``PROMPT``: the OS will prompt the user on first access (macOS
      ``AVAuthorizationStatusNotDetermined``; Windows default state).
    - ``UNKNOWN``: we can't tell (e.g. pyobjc missing on macOS, or an
      unsupported platform).
    """

    GRANTED = "granted"
    DENIED = "denied"
    PROMPT = "prompt"
    UNKNOWN = "unknown"


# ─── Public API: keyboard permission ───────────────────────────────────────


def check_keyboard_permission() -> PermissionState:
    """Return the current keyboard-monitoring permission state.

    On Windows this always returns ``GRANTED`` (``WH_KEYBOARD_LL`` needs
    no permission). On macOS it probes the Accessibility permission via
    the ``AXIsProcessTrustedWithOptions`` CoreFoundation call (when
    pyobjc is available). On Linux it checks whether the current user is
    in the ``input`` group AND whether at least one ``/dev/input/event*``
    device is readable.
    """
    if _p.is_windows():
        return PermissionState.GRANTED
    if _p.is_macos():
        return _p._check_macos_accessibility()
    if _p.is_linux():
        return _p._check_linux_input_access()
    return PermissionState.UNKNOWN


def permission_error_is_permission_denied(error_message: str) -> bool:
    """Classify a native binary ``ERROR:`` line as a permission issue.

    Returns True for:
    - ``Accessibility permission required...`` (macOS)
    - ``Permission denied opening /dev/input/event*...`` (Linux)
    - ``Add yourself to the 'input' group...`` (Linux)
    - ``No keyboard devices found ... Are you in the 'input' group?`` (Linux)

    Returns False for all other errors (binary not found, parse error,
    etc.) — those are handled by the startup fallback chain, not the
    permission onboarding flow.
    """
    if not error_message:
        return False
    lower = error_message.lower()
    return (
        "accessibility" in lower
        or "permission denied" in lower
        or "input' group" in lower
        or "input group" in lower
        or "/dev/input" in lower
    )


def request_keyboard_permission(
    on_granted: Callable[[], None] | None = None,
) -> None:
    """Open the OS permission UI so the user can grant the permission.

        - **macOS**: opens ``System Settings → Privacy & Security →
          Accessibility`` via the ``x-apple.systempreferences:`` scheme,
          with a fallback to the older ``Security.prefPane`` bundle path.
        - **Linux**: invokes ``pkexec`` to run
          ``scripts/linux/install_permissions.py`` (installs udev rule +
          adds user to ``input`` group + configures Caps Lock). If pkexec
          isn't available, falls back to ``gksu`` / ``kdesu`` / a
    terminal-based prompt. : this is the zero-command path for
          AppImage users — the OS shows a polkit GUI prompt, the user types
          their password once, and the install script runs as root. The
          onboarding instructions should mention that clicking "Grant
          permission" triggers ``pkexec install_permissions.py``.
        - **Windows**: no-op (no permission needed).

        The optional ``on_granted`` callback is invoked when the permission
        is detected as granted (best-effort — see ``schedule_permission_retry``
        for the retry mechanism).
    """
    if _p.is_macos():
        # trigger the native macOS TCC consent dialog ONCE via
        # ``AXIsProcessTrustedWithOptions(kAXTrustedCheckOptionPrompt=True)``
        # (the only sanctioned programmatic path on macOS 14+). Then
        # fall back to the deep-link for re-prompting after revocation
        # (the TCC dialog won't re-appear if the user previously denied
        # — the deep-link lands them on the Accessibility list so they
        # can re-toggle manually).
        _p._trigger_macos_accessibility_consent_prompt()
        _p._open_macos_accessibility_settings()
    elif _p.is_linux():
        _p._open_linux_pkexec_prompt()
    # Windows: no-op

    if on_granted is not None:
        # Best-effort: schedule a retry to detect when the user grants
        # permission. The caller may also set up its own retry timer.
        _p.schedule_permission_retry(on_granted)


# ─── Permission retry mechanism ────────────────────────────────────────────

# Default: retry every 60 seconds, up to 5 times. These match the design
# in ADR 0006 Section B.5.
PERMISSION_RETRY_INTERVAL_SECONDS = 60.0
PERMISSION_RETRY_MAX_ATTEMPTS = 5


def schedule_permission_retry(
    callback: Callable[[], None],
    interval: float = PERMISSION_RETRY_INTERVAL_SECONDS,
    max_attempts: int = PERMISSION_RETRY_MAX_ATTEMPTS,
) -> None:
    """Schedule a periodic check for permission grant.

    After the user opens the OS permission UI (via
    ``request_keyboard_permission``), the native backend has already
    failed and won't auto-restart. This function polls
    ``check_keyboard_permission()`` every ``interval`` seconds; when it
    returns ``GRANTED``, the ``callback`` is invoked (the callback is
    responsible for restarting the native backend) and the timer stops.

    After ``max_attempts`` checks, the timer gives up. This prevents
    infinite polling if the user never grants permission.
    """
    # RETRY-LOCK-FIX: guard the cancel-and-reschedule sequence so two
    # concurrent callers cannot both create orphaned Timer threads.
    with _p._retry_lock:
        # Cancel any existing retry timer
        _p.cancel_permission_retry()

        _p._retry_count = 0
        _p._cancelled = False

        def _poll() -> None:
            _p._retry_count += 1
            if _p._cancelled:
                # cancelled between scheduling and this poll firing — skip
                return
            state = _p.check_keyboard_permission()
            log.info(
                "[PERMISSION] Retry %d/%d: state=%s",
                _p._retry_count,
                max_attempts,
                state.value,
            )
            if state == PermissionState.GRANTED:
                if _p._cancelled:
                    # cancelled between the state check and the callback fire
                    return
                log.info("[PERMISSION] Permission granted — invoking callback")
                try:
                    callback()
                except Exception:
                    log.exception("[PERMISSION] Retry callback raised")
                return
            if _p._retry_count >= max_attempts:
                log.info(
                    "[PERMISSION] Giving up after %d attempts (will retry on next hotkey failure)",
                    max_attempts,
                )
                return
            # Schedule next poll
            with _p._retry_lock:
                if _p._cancelled:
                    return
                _p._retry_timer = threading.Timer(interval, _poll)
                _p._retry_timer.daemon = True
                _p._retry_timer.start()

        _p._retry_timer = threading.Timer(interval, _poll)
        _p._retry_timer.daemon = True
        _p._retry_timer.start()


def cancel_permission_retry() -> None:
    """Cancel any pending permission retry timer. Safe to call multiple times."""
    with _p._retry_lock:
        _p._cancelled = True
        if _p._retry_timer is not None:
            with contextlib.suppress(Exception):
                _p._retry_timer.cancel()
            _p._retry_timer = None
        _p._retry_count = 0


# ─── pyobjc availability cache ─────────────────────────────────────────────


def _is_pyobjc_available() -> bool:
    """probe whether pyobjc (``ApplicationServices``) is importable.

    Cached at module level in :data:`voice_typer.server.permissions._PYOBJC_AVAILABLE`
    so repeated calls (e.g. one per permission check) don't re-pay the
    import-lookup cost. On non-macOS hosts (Linux sandbox, CI, Windows)
    where pyobjc isn't installed, the first call returns ``False`` and
    subsequent calls are O(1). On macOS with pyobjc installed, the first
    call returns ``True`` and subsequent calls are O(1).

    Use :func:`reset_pyobjc_cache` to clear the cache (e.g. in tests).
    """
    if _p._PYOBJC_AVAILABLE is not None:
        return _p._PYOBJC_AVAILABLE
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]  # noqa: F401
            AXIsProcessTrustedWithOptions,
        )
    except ImportError:
        _p._PYOBJC_AVAILABLE = False
    else:
        _p._PYOBJC_AVAILABLE = True
    return _p._PYOBJC_AVAILABLE


def reset_pyobjc_cache() -> None:
    """clear the cached pyobjc availability flag.

    The next call to :func:`_is_pyobjc_available` will re-probe. Intended
    for tests (which monkeypatch the cache to exercise specific branches)
    and for hot-reload scenarios.
    """
    _p._PYOBJC_AVAILABLE = None


# ─── Microphone permission probe (dispatcher) ──────────────────────────────


def check_microphone_permission() -> MicrophonePermissionState:
    """probe the OS-level microphone permission state.

        - **macOS**: uses pyobjc ``AVCaptureDevice.authorizationStatus(for: .audio)``
          to return one of ``GRANTED`` / ``DENIED`` / ``PROMPT`` (the
          ``AVAuthorizationStatusNotDetermined`` case). Returns ``UNKNOWN``
          if pyobjc isn't installed.
    **Windows**:  — attempts a 1-frame ``sounddevice.InputStream``
          open in probe mode. If PortAudio raises an ``OSError`` whose
          message contains "access denied" (the Windows mic-privacy-blocked
          signature), returns ``DENIED``. Otherwise returns ``GRANTED``
          (the probe succeeded) or ``UNKNOWN`` (the probe itself raised an
          unrelated error — we never want a probe failure to take down the
          caller). Pre-fix, this branch unconditionally returned ``GRANTED``,
          so a globally-disabled Windows mic privacy setting was reported
          as GRANTED and the user got a generic OSError toast instead of a
          clean "Open Windows Settings → Microphone" prompt.
    **Linux**:  — checks for Flatpak (``/.flatpak-info``) and
          reads the flatpak per-app microphone permission table. Returns
          ``DENIED`` if the per-app portal permission is revoked. Returns
          ``GRANTED`` otherwise (no standard per-app mic permission system
          on non-Flatpak Linux — PipeWire/PulseAudio access is controlled
          by the session manager but typically granted by default).
        - **Unsupported platform**: returns ``UNKNOWN``.

    note: the Windows/Linux probes are best-effort. If the probe
        itself raises (e.g. sounddevice not importable on a headless CI
        box, or the flatpak permission file moved between versions), we
        fall back to ``GRANTED`` and log a warning so the operator knows
        the pre-check is limited — the runtime PortAudio-open path in
        :mod:`voice_typer.server.recording.recorder` will re-classify the
        actual OSError at device-open time.
    """
    try:
        if _p.is_macos():
            return _p._check_macos_microphone()
        if _p.is_windows():
            return _p._check_windows_microphone()
        if _p.is_linux():
            return _p._check_linux_microphone()
        return MicrophonePermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] check_microphone_permission probe raised")
        return MicrophonePermissionState.UNKNOWN


def verify_microphone_accessible() -> None:
    """pre-flight check that the OS reports microphone permission
    as granted (or prompt — the OS will show the consent dialog on first
    PortAudio open in that case).

    Raises :class:`MicrophonePermissionDeniedError` (from
    :mod:`voice_typer.server.asr_errors`) when the OS reports the
    permission state as ``DENIED``. The IPC layer ``isinstance``-checks
    this exception type to surface the permission onboarding UI
    instead of a generic error toast.

    Does NOT raise on ``GRANTED`` / ``PROMPT`` / ``UNKNOWN``:
    - ``GRANTED``: nothing to do.
    - ``PROMPT``: the OS will show the consent dialog on first
      PortAudio open — pre-empting would double-prompt.
    - ``UNKNOWN`` (pyobjc missing on macOS, or unsupported platform):
      defer to the PortAudio-open re-classification path in
      :mod:`voice_typer.server.recording.recorder` which inspects the
      actual OSError message at runtime.
    """
    state = _p.check_microphone_permission()
    if state == MicrophonePermissionState.DENIED:
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        raise MicrophonePermissionDeniedError(
            "Microphone permission denied by OS",
            state="denied",
        )


# ─── Microphone permission request (dispatchers) ───────────────────────────


def request_microphone_permission(
    on_granted: Callable[[], None] | None = None,
) -> None:
    """request microphone permission from the OS.

    Mirrors :func:`request_keyboard_permission` but for the microphone.
    On macOS, opens System Settings -> Privacy -> Microphone AND actively
    triggers the OS consent dialog via AVFoundation. On Windows / Linux,
    this is a no-op (Windows doesn't need permission; Linux uses
    PipeWire/PulseAudio permissions managed outside the app).

    Parameters
    ----------
    on_granted:
        Optional callback invoked when the OS reports the permission
        was granted (observed via the periodic
        :func:`schedule_permission_retry` poller). The callback is
        responsible for restarting the native backend / recorder.
    """
    if _p.is_macos():
        _p._open_macos_microphone_settings()
        _p._trigger_macos_microphone_consent_prompt()
        if on_granted is not None:
            _p.schedule_permission_retry(on_granted)
    elif _p.is_linux():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Linux")
    elif _p.is_windows():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Windows")
    else:
        log.warning("[PERMISSION] request_microphone_permission: unknown platform")


def request_microphone_permission_result(
    on_granted: Callable[[], None] | None = None,
) -> dict:
    """IPC-friendly wrapper around :func:`request_microphone_permission`.

    Returns a result dict so the renderer can surface success/failure
    without a follow-up ``onboarding_check_permissions`` round-trip.

    Returns
    -------
    dict
        ``{"requested": bool, "platform": str, "error": str | None,
        "instructions": str | None}`` where:

        - ``requested``: True if the OS permission UI was launched
          (macOS). False on Windows/Linux (no-op) or unknown platforms.
        - ``platform``: ``"windows"`` / ``"macos"`` / ``"linux"`` /
          ``"unknown"``.
        - ``error``: ``None`` on success, or a short string explaining
          why the request couldn't be issued.
        - ``instructions``: optional human-readable next-step hint.
    """
    try:
        if _p.is_macos():
            _p._open_macos_microphone_settings()
            _p._trigger_macos_microphone_consent_prompt()
            platform_name = "macos"
            requested = True
            error = None
            instructions = (
                "Open System Settings -> Privacy & Security -> Microphone and "
                "enable Voice Typer. The consent dialog should appear automatically."
            )
        elif _p.is_linux():
            platform_name = "linux"
            requested = False
            error = None
            instructions = (
                "Linux uses PipeWire/PulseAudio permissions managed outside "
                "the app. Ensure your user is in the ``audio`` group "
                "(``sudo usermod -aG audio $USER && sudo systemctl restart pipewire``)."
            )
        elif _p.is_windows():
            platform_name = "windows"
            requested = False
            error = None
            instructions = (
                "Windows will prompt for microphone access on first use. "
                "Open Windows Settings -> Privacy -> Microphone if you need to reset."
            )
        else:
            platform_name = "unknown"
            requested = False
            error = "Unsupported platform"
            instructions = None

        if on_granted is not None and requested:
            _p.schedule_permission_retry(on_granted)
    except Exception as exc:
        log.exception("[PERMISSION] request_microphone_permission_result failed")
        platform_name = (
            "macos" if _p.is_macos() else "linux" if _p.is_linux() else "windows" if _p.is_windows() else "unknown"
        )
        requested = False
        error = str(exc)
        instructions = None

    return {
        "requested": requested,
        "platform": platform_name,
        "error": error,
        "instructions": instructions,
    }


# ─── Tray notification helper ──────────────────────────────────────────────


def show_permission_notification(tray, error_message: str) -> None:
    """Show a tray notification about the permission issue.

        ``tray`` is the app's tray object (must have a ``notify(title, body)``
        method). If ``tray`` is None, the notification is only logged.

    the title/body strings are now resolved through
        :mod:`voice_typer.server.i18n` (via :func:`i18n.t`) so the renderer
        can push translations via the ``set_tray_locale`` IPC. The English
        fallback (registered at i18n import time) matches the previous
        hardcoded strings verbatim, so source-level regression tests that
        grep for the English text continue to find the substring.
    """
    # Local import to avoid a circular dependency at module import time
    # (i18n imports branding, branding is imported by this module).
    from voice_typer.server import i18n

    if _p.is_macos():
        title = i18n.t(_p._PERMISSION_NOTIFY_MACOS_TITLE_KEY, app=_p.APP_NAME)
        body = i18n.t(_p._PERMISSION_NOTIFY_MACOS_BODY_KEY)
    elif _p.is_linux():
        title = i18n.t(_p._PERMISSION_NOTIFY_LINUX_TITLE_KEY)
        body = i18n.t(_p._PERMISSION_NOTIFY_LINUX_BODY_KEY)
    else:
        # Windows shouldn't reach here — no permission needed. No i18n
        # key for this branch (it's an unexpected path); fall back to
        # the raw APP_NAME + error_message so the log is useful.
        title = _p.APP_NAME
        body = error_message

    log.warning("[PERMISSION] %s: %s (error: %s)", title, body, error_message)

    if tray is not None:
        try:
            tray.notify(title, body)
        except Exception:
            log.exception("[PERMISSION] tray.notify failed")
    # If tray is None, the log.warning above is the only signal — the
    # caller may also surface this in the UI.
