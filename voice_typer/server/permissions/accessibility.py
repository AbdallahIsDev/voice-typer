"""macOS Accessibility permission probe for the ``permissions`` package."""

from __future__ import annotations

import logging
import os
import subprocess

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


def _check_macos_accessibility() -> _p.PermissionState:
    """Probe macOS Accessibility permission."""
    # short-circuit when pyobjc isn't installed. Avoids paying
    if not _p._is_pyobjc_available():
        return _p.PermissionState.UNKNOWN

    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate

        # AXIsProcessTrustedWithOptions takes an options dict; passing
        options = CFDictionaryCreate(None, [], [], 0, None, None)
        trusted = AXIsProcessTrustedWithOptions(options)
        return _p.PermissionState.GRANTED if trusted else _p.PermissionState.DENIED
    except ImportError:
        # pyobjc was cached as available but ApplicationServices
        _p._PYOBJC_AVAILABLE = False
        return _p.PermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] macOS Accessibility check failed")
        return _p.PermissionState.UNKNOWN


def _open_macos_accessibility_settings() -> None:
    """Open System Settings → Privacy & Security → Accessibility."""
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
            "[PERMISSION] Failed to open via 'open %s': %s, falling back to prefpane path",
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

    Returns ``True`` if the process is already trusted (no prompt
    """
    # Module-level de-dup flag: only ONE prompt per process lifetime.
    if getattr(_p, "_a11y_prompt_shown", False):
        log.debug("[PERMISSION] macOS a11y TCC prompt already shown this session, skipping")
        return False
    if not _p._is_pyobjc_available():
        log.debug("[PERMISSION] pyobjc not available, cannot trigger native TCC prompt; will use deep-link fallback")
        return False
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        from CoreFoundation import CFDictionaryCreate, kCFBooleanTrue
    except ImportError:
        # pyobjc was cached as available but the specific symbols
        _p._PYOBJC_AVAILABLE = False
        log.debug("[PERMISSION] pyobjc partial install, cannot trigger TCC prompt; will use deep-link fallback")
        return False

    try:
        # Build the options dict: {kAXTrustedCheckOptionPrompt: True}.
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
