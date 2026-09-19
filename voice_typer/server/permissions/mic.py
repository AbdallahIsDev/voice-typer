"""Microphone permission probes for the ``permissions`` package."""

from __future__ import annotations

import logging
import os
import subprocess

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


def _check_windows_microphone() -> _p.MicrophonePermissionState:
    """probe Windows microphone permission via a 1-frame"""
    try:
        # Lazy import, sounddevice loads the PortAudio C library at
        import sounddevice as _sd
    except Exception:
        log.warning(
            "[PERMISSION] Windows mic permission probe: sounddevice not "
            "importable, pre-check is limited; runtime PortAudio failure "
            "will be re-classified by the recorder"
        )
        return _p.MicrophonePermissionState.GRANTED

    try:
        # Open a 1-frame InputStream. ``framesize=1`` + immediate
        stream = _sd.InputStream(
            samplerate=_p.WHISPER_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1,
        )
        # ``start()`` triggers the actual device-open. ``stop()`` then
        stream.start()
        stream.stop()
        stream.close()
        return _p.MicrophonePermissionState.GRANTED
    except OSError as exc:
        msg = str(exc).lower()
        # Windows MediaFoundation "access denied" signature when the
        if "access denied" in msg or "access is denied" in msg:
            log.warning(
                "[PERMISSION] Windows mic permission DENIED (PortAudio InputStream open raised 'access denied'): %s",
                exc,
            )
            return _p.MicrophonePermissionState.DENIED
        # Any other OSError (no default device, driver issue, etc.) —
        log.debug(
            "[PERMISSION] Windows mic probe raised unrelated OSError "
            "(falling back to GRANTED, recorder will re-classify): %s",
            exc,
        )
        return _p.MicrophonePermissionState.GRANTED
    except Exception as exc:
        # Probe failure (e.g. test Mock raised something unexpected, or
        log.warning(
            "[PERMISSION] Windows mic permission probe itself raised "
            "(falling back to GRANTED; runtime PortAudio failure will "
            "be re-classified by the recorder): %s",
            exc,
        )
        return _p.MicrophonePermissionState.GRANTED


def _check_linux_microphone() -> _p.MicrophonePermissionState:
    """probe Linux microphone permission."""
    from pathlib import Path

    # Flatpak detection: ``/.flatpak-info`` is the canonical marker
    try:
        is_flatpak = Path("/.flatpak-info").exists()
    except Exception:
        is_flatpak = False

    if not is_flatpak:
        # Non-Flatpak Linux: no standard per-app mic permission system.
        log.debug(
            "[PERMISSION] Linux mic permission pre-check is limited on "
            "non-Flatpak Linux, runtime PortAudio failure will be "
            "re-classified by the recorder"
        )
        return _p.MicrophonePermissionState.GRANTED

    # Flatpak: read the per-app permission table. The file is JSON;
    try:
        # ``$FLATPAK_ID`` is the canonical env var for the app-id.
        app_id = os.environ.get("FLATPAK_ID", "")
        xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        perm_path = Path(xdg_data) / "flatpak" / "permissions" / "permissions.json"
        if not perm_path.exists():
            # No permission file, fall back to GRANTED (flatpak may
            log.debug(
                "[PERMISSION] Flatpak mic permission file not found at %s, falling back to GRANTED",
                perm_path,
            )
            return _p.MicrophonePermissionState.GRANTED

        import json

        with perm_path.open("r", encoding="utf-8") as fh:
            perms = json.load(fh)

        # Schema (flatpak 1.x):
        portals = perms.get("portals", {}) if isinstance(perms, dict) else {}
        app_perms = portals.get(app_id, {}) if isinstance(portals, dict) else {}
        mic_perm = app_perms.get("microphone", "unset") if isinstance(app_perms, dict) else "unset"

        if str(mic_perm).lower() == "no":
            log.warning(
                "[PERMISSION] Flatpak mic permission DENIED for app %s (permissions.json reports microphone='no')",
                app_id or "(unknown)",
            )
            return _p.MicrophonePermissionState.DENIED
        # "yes" or "unset" → GRANTED (the portal will prompt on first
        return _p.MicrophonePermissionState.GRANTED
    except Exception as exc:
        log.warning(
            "[PERMISSION] Flatpak mic permission probe itself raised "
            "(falling back to GRANTED; runtime PortAudio failure will "
            "be re-classified by the recorder): %s",
            exc,
        )
        return _p.MicrophonePermissionState.GRANTED


def _check_macos_microphone() -> _p.MicrophonePermissionState:
    """Probe macOS microphone permission via AVFoundation (pyobjc)."""
    # short-circuit when pyobjc isn't installed. Avoids paying
    if not _p._is_pyobjc_available():
        return _p.MicrophonePermissionState.UNKNOWN

    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import-not-found]
    except ImportError:
        # pyobjc was cached as available but AVFoundation isn't
        _p._PYOBJC_AVAILABLE = False
        return _p.MicrophonePermissionState.UNKNOWN

    try:
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio())
    except Exception:
        log.exception("[PERMISSION] AVCaptureDevice.authorizationStatusForMediaType_ failed")
        return _p.MicrophonePermissionState.UNKNOWN

    # AVAuthorizationStatus enum values (int):
    if status == 2:
        return _p.MicrophonePermissionState.GRANTED
    if status == 0:
        return _p.MicrophonePermissionState.PROMPT
    if status in (1, 3):
        return _p.MicrophonePermissionState.DENIED
    return _p.MicrophonePermissionState.UNKNOWN


def _open_macos_microphone_settings() -> None:
    """Open System Settings -> Privacy & security -> Microphone."""
    deep_link = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone"
    try:
        subprocess.Popen(
            ["open", deep_link],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("[PERMISSION] Opened macOS Microphone settings via URL scheme")
        return
    except OSError as exc:
        log.warning(
            "[PERMISSION] Failed to open via 'open %s': %s - falling back to prefpane path",
            deep_link,
            exc,
        )

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
            except OSError as exc:
                log.debug(
                    "[PERMISSION] macOS Microphone prefpane open failed for %s: %s",
                    path,
                    exc,
                    exc_info=True,
                )
                continue

    log.error("[PERMISSION] Could not open macOS Microphone settings")


def _trigger_macos_microphone_consent_prompt() -> None:
    """Actively trigger the macOS OS consent dialog for microphone access."""
    import sys as _sys

    av = _sys.modules.get("AVFoundation")
    if av is None:
        try:
            import AVFoundation as av  # type: ignore[import-not-found, no-redef]  # noqa: N813
        except ImportError:
            log.debug("[PERMISSION] AVFoundation not available - skipping macOS mic consent prompt")
            return

    try:
        media_type_sentinel = av.AVMediaTypeAudio()
        av.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            media_type_sentinel,
            lambda granted: None,
        )
        log.info("[PERMISSION] Triggered macOS microphone consent prompt via AVFoundation")
    except Exception:
        log.exception("[PERMISSION] Failed to trigger macOS microphone consent prompt")
