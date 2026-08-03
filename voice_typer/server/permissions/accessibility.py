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
