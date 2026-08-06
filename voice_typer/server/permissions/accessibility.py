"""macOS Accessibility permission probe for the ``permissions`` package.

This submodule contains the macOS Accessibility permission probe
(``_check_macos_accessibility``) and the System Settings deep-link
opener (``_open_macos_accessibility_settings``).

The pyobjc availability cache (``_PYOBJC_AVAILABLE``) and
``_is_pyobjc_available`` live on the facade and are accessed via
``_p.<name>`` so test monkeypatches on the facade propagate.
"""

from __future__ import annotations

import logging
import os
import subprocess

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


def _check_macos_accessibility() -> _p.PermissionState:
    """Probe macOS Accessibility permission.

    Uses ``AXIsProcessTrustedWithOptions`` via pyobjc if available.
    Falls back to ``UNKNOWN`` if pyobjc isn't installed (we can't probe
    without it).
    """
    # short-circuit when pyobjc isn't installed. Avoids paying
    # the ``from ApplicationServices import ...`` lookup cost on every probe.
    if not _p._is_pyobjc_available():
        return _p.PermissionState.UNKNOWN

    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate

        # AXIsProcessTrustedWithOptions takes an options dict; passing
        # kAXTrustedCheckOptionPrompt=True would pop the OS dialog.
        # We just want to *check*, not prompt, so pass an empty dict.
        options = CFDictionaryCreate(None, [], [], 0, None, None)
        trusted = AXIsProcessTrustedWithOptions(options)
        return _p.PermissionState.GRANTED if trusted else _p.PermissionState.DENIED
    except ImportError:
        # pyobjc was cached as available but ApplicationServices
        # CoreFoundation aren't importable (partial pyobjc install). Flip
        # the cache so future probes short-circuit to UNKNOWN, then return
        # UNKNOWN. The native binary will emit ERROR on first use and the
        # adapter will prompt the user.
        _p._PYOBJC_AVAILABLE = False
        return _p.PermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] macOS Accessibility check failed")
        return _p.PermissionState.UNKNOWN


def _open_macos_accessibility_settings() -> None:
    """Open System Settings → Privacy & Security → Accessibility.

    Uses the ``x-apple.systempreferences:`` URL scheme (macOS 13+).
    Falls back to opening the Security & Privacy prefpane directly
    (macOS 12 and earlier).

    this function is the *fallback* path used after the
    native TCC consent dialog has already been triggered (or when
    pyobjc is unavailable and the dialog can't be triggered). The
    primary path is :func:`_trigger_macos_accessibility_consent_prompt`,
    which calls ``AXIsProcessTrustedWithOptions`` ONCE with
    ``kAXTrustedCheckOptionPrompt=True`` to pop the native TCC dialog.
    That call is the only sanctioned way to programmatically surface
    the "Open System Settings" button on macOS 14+ (deep-links alone
    no longer route the user directly to the per-app toggle in
    Sequoia — they land on the Accessibility list and the user has
    to scroll/find Voice Typer themselves).
    """
    # Primary: deep-link via URL scheme (macOS Ventura+)
    deep_link = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility"
    try:
        subprocess.Popen(
            ["open", deep_link],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("[PERMISSION] Opened macOS Accessibility settings via URL scheme")
        return
    except OSError as exc:
        log.warning(
            "[PERMISSION] Failed to open via 'open %s': %s — falling back to prefpane path",
            deep_link,
            exc,
        )

    # Fallback: open the Security & Privacy prefpane directly
    prefpane_paths = [
        "/System/Library/PreferencePanes/Security.prefPane/",
        "/System/Library/PreferencePanes/SecurityAndPrivacy.prefPane/",
    ]
    for path in prefpane_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen(
                    ["open", path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("[PERMISSION] Opened prefpane: %s", path)
                return
            except OSError:
                continue

    log.error("[PERMISSION] Could not open macOS Accessibility settings")


def _trigger_macos_accessibility_consent_prompt() -> bool:
    """Trigger the native macOS TCC consent dialog for Accessibility.

    Calls ``AXIsProcessTrustedWithOptions`` ONCE with the
    ``kAXTrustedCheckOptionPrompt=True`` option, which causes macOS
    to pop the system "Accessibility access" dialog (the one with
    the "Open System Settings" button). The call returns the current
    trusted state (same as a non-prompting probe); the prompt is a
    side effect.

    This is the ONE sanctioned programmatic path to surface the
    TCC dialog. Calling it repeatedly while the dialog is already
    on-screen is a no-op (macOS de-duplicates), but we still gate
    it on a module-level ``_a11y_prompt_shown`` flag so a buggy
    caller that re-invokes ``request_keyboard_permission`` in a
    tight loop won't spam the API.

    Returns ``True`` if the process is already trusted (no prompt
    needed); ``False`` if not trusted (prompt was shown, or would
    have been shown if pyobjc is unavailable — see below).

    On non-macOS hosts (Linux sandbox, CI, Windows) or when pyobjc
    isn't installed, this is a silent no-op returning ``False`` —
    the caller falls back to :func:`_open_macos_accessibility_settings`
    (the deep-link) which works on every macOS version regardless
    of pyobjc availability.
    """
    # Module-level de-dup flag: only ONE prompt per process lifetime.
    # The OS itself de-dupes the dialog, but the python-level flag
    # avoids re-paying the ``AXIsProcessTrustedWithOptions`` call
    # cost on every ``request_keyboard_permission`` invocation
    # (which can be called from the hotkey adapter on every binary
    # error). Reset only by process restart.
    if getattr(_p, "_a11y_prompt_shown", False):
        log.debug("[PERMISSION] macOS a11y TCC prompt already shown this session — skipping")
        return False
    if not _p._is_pyobjc_available():
        log.debug("[PERMISSION] pyobjc not available — cannot trigger native TCC prompt; will use deep-link fallback")
        return False
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        from CoreFoundation import CFDictionaryCreate, kCFBooleanTrue
    except ImportError:
        # pyobjc was cached as available but the specific symbols
        # aren't importable (partial pyobjc install). Flip the cache
        # so future probes short-circuit, then fall back to deep-link.
        _p._PYOBJC_AVAILABLE = False
        log.debug("[PERMISSION] pyobjc partial install — cannot trigger TCC prompt; will use deep-link fallback")
        return False

    try:
        # Build the options dict: {kAXTrustedCheckOptionPrompt: True}.
        # ``CFDictionaryCreate`` wants parallel C-arrays of keys + values
        # plus allocator + callbacks. ``kCFBooleanTrue`` is the canonical
        # singleton for a CFBoolean true.
        keys = [kAXTrustedCheckOptionPrompt]
        values = [kCFBooleanTrue]
        options = CFDictionaryCreate(None, keys, values, 1, None, None)
        trusted = AXIsProcessTrustedWithOptions(options)
        _p._a11y_prompt_shown = True
        if trusted:
            log.info("[PERMISSION] macOS Accessibility already granted (TCC prompt call was a no-op)")
        else:
            log.info("[PERMISSION] Triggered macOS Accessibility TCC consent dialog via AXIsProcessTrustedWithOptions")
        return bool(trusted)
    except Exception:
        log.exception("[PERMISSION] Failed to trigger macOS Accessibility TCC prompt")
        return False
