"""Microphone permission probes for the ``permissions`` package.

This submodule contains the per-platform microphone permission probes
(``_check_macos_microphone`` / ``_check_windows_microphone`` /
``_check_linux_microphone``) and the macOS-specific settings openers
(``_open_macos_microphone_settings`` /
``_trigger_macos_microphone_consent_prompt``).

The dispatcher :func:`voice_typer.server.permissions.check_microphone_permission`
lives in :mod:`voice_typer.server.permissions.checker` and routes to the
correct probe based on the current platform.

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


def _check_windows_microphone() -> _p.MicrophonePermissionState:
    """probe Windows microphone permission via a 1-frame
    ``sounddevice.InputStream`` open.

    Windows doesn't expose a clean ahead-of-time probe for the per-app
    mic privacy toggle (Settings → Privacy → Microphone → "Allow apps
    to access your microphone"). The WinRT ``MediaCapture`` API can
    probe, but the Python WinRT bindings aren't a hard dependency.

    The probe here opens an ``sd.InputStream`` for a single frame. If
    Windows has blocked mic access for the app, PortAudio raises an
    ``OSError`` whose message contains "access denied" (the Windows
    MediaFoundation signature). On any OTHER OSError (no default device,
    driver issue, etc.) we return ``GRANTED`` and let the runtime
    PortAudio-open path in the recorder re-classify — this matches the
    pre-fix behavior for those cases and avoids false-positive DENIED
    reports that would block the user from starting a recording.

    The probe is gated behind try/except so a probe failure (e.g.
    sounddevice not importable, or the test suite's ``mock_heavy_imports``
    autouse fixture replaces ``sd.InputStream`` with a Mock) NEVER takes
    down the caller — we fall back to ``GRANTED`` with a warning log.
    """
    try:
        # Lazy import — sounddevice loads the PortAudio C library at
        # import time, which we don't want to pay on the pre-flight
        # check path. The lazy_module proxy in ``recording.device_manager``
        # re-resolves ``sys.modules`` on every attribute access so test
        # patches of the form ``monkeypatch.setattr(sd, "InputStream", ...)``
        # propagate here. We import directly here so the probe is
        # self-contained (the device_manager import would create a
        # circular dependency: device_manager imports recorder imports
        # permissions).
        import sounddevice as _sd
    except Exception:
        log.warning(
            "[PERMISSION] Windows mic permission probe: sounddevice not "
            "importable — pre-check is limited; runtime PortAudio failure "
            "will be re-classified by the recorder"
        )
        return _p.MicrophonePermissionState.GRANTED

    try:
        # Open a 1-frame InputStream. ``framesize=1`` + immediate
        # ``stop()``/``close()`` minimizes the audio pipeline cost. We
        # do NOT call ``start()`` — just constructing the InputStream
        # triggers the PortAudio device-open which is where Windows
        # MediaFoundation checks the mic privacy setting.
        stream = _sd.InputStream(
            samplerate=_p.WHISPER_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=1,
        )
        # ``start()`` triggers the actual device-open. ``stop()`` then
        # ``close()`` tear it down immediately.
        stream.start()
        stream.stop()
        stream.close()
        return _p.MicrophonePermissionState.GRANTED
    except OSError as exc:
        msg = str(exc).lower()
        # Windows MediaFoundation "access denied" signature when the
        # per-app mic privacy toggle is OFF. The exact text varies by
        # Windows version (e.g. "Access denied", "access is denied"),
        # so we substring-match case-insensitively.
        if "access denied" in msg or "access is denied" in msg:
            log.warning(
                "[PERMISSION] Windows mic permission DENIED (PortAudio InputStream open raised 'access denied'): %s",
                exc,
            )
            return _p.MicrophonePermissionState.DENIED
        # Any other OSError (no default device, driver issue, etc.) —
        # fall back to GRANTED and let the recorder re-classify at
        # runtime. This avoids false-positive DENIED reports for
        # unrelated device issues.
        log.debug(
            "[PERMISSION] Windows mic probe raised unrelated OSError "
            "(falling back to GRANTED, recorder will re-classify): %s",
            exc,
        )
        return _p.MicrophonePermissionState.GRANTED
    except Exception as exc:
        # Probe failure (e.g. test Mock raised something unexpected, or
        # PortAudio is broken on this system). Fall back to GRANTED
        # with a warning so the operator knows the pre-check is limited.
        log.warning(
            "[PERMISSION] Windows mic permission probe itself raised "
            "(falling back to GRANTED; runtime PortAudio failure will "
            "be re-classified by the recorder): %s",
            exc,
        )
        return _p.MicrophonePermissionState.GRANTED


def _check_linux_microphone() -> _p.MicrophonePermissionState:
    """probe Linux microphone permission.

    On Flatpak, the per-app portal permission can revoke mic access
    while the app is running. We check ``/.flatpak-info`` (the canonical
    Flatpak marker file) and, if present, attempt to read the flatpak
    permission table for the current app.

    The flatpak permission store lives at
    ``~/.local/share/flatpak/permissions/permissions.json`` (or under
    ``$XDG_DATA_HOME/flatpak/permissions/``) and contains a per-app
    table. The ``microphone`` permission is in the ``portals`` section
    under the app's app-id. If the file is missing or the schema
    changed between flatpak versions, we fall back to ``GRANTED`` with
    a warning log — we never want a probe failure to take down the
    caller.

    On non-Flatpak Linux (PulseAudio/PipeWire), there's no standard
    per-app mic permission system — the session manager grants access
    by default. We return ``GRANTED`` and log a one-time warning that
    the pre-check is limited on Linux.
    """
    from pathlib import Path

    # Flatpak detection: ``/.flatpak-info`` is the canonical marker
    # file that flatpak creates at the root of the sandbox filesystem.
    try:
        is_flatpak = Path("/.flatpak-info").exists()
    except Exception:
        is_flatpak = False

    if not is_flatpak:
        # Non-Flatpak Linux: no standard per-app mic permission system.
        # Log a one-time warning (well, every call — but the call is
        # gated by the device_health_checker ~60s interval so it's
        # not spammy) so the operator knows the pre-check is limited.
        log.debug(
            "[PERMISSION] Linux mic permission pre-check is limited on "
            "non-Flatpak Linux — runtime PortAudio failure will be "
            "re-classified by the recorder"
        )
        return _p.MicrophonePermissionState.GRANTED

    # Flatpak: read the per-app permission table. The file is JSON;
    # schema varies between flatpak versions, so we defensively parse
    # and look for the ``microphone`` key under the app's app-id.
    try:
        # ``$FLATPAK_ID`` is the canonical env var for the app-id.
        app_id = os.environ.get("FLATPAK_ID", "")
        xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        perm_path = Path(xdg_data) / "flatpak" / "permissions" / "permissions.json"
        if not perm_path.exists():
            # No permission file — fall back to GRANTED (flatpak may
            # not have written it yet, or the app may not have any
            # portal permissions configured).
            log.debug(
                "[PERMISSION] Flatpak mic permission file not found at %s — falling back to GRANTED",
                perm_path,
            )
            return _p.MicrophonePermissionState.GRANTED

        import json

        with perm_path.open("r", encoding="utf-8") as fh:
            perms = json.load(fh)

        # Schema (flatpak 1.x):
        # {
        #   "portals": {
        #     "<app-id>": {
        #       "microphone": "yes" | "no" | "unset",
        #       "camera": "yes" | "no" | "unset",
        #       ...
        #     }
        #   }
        # }
        # Defensive: traverse with .get() and isinstance checks so a
        # schema change doesn't raise.
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
        # access for "unset", which we treat as PROMPT... but the
        # caller of check_microphone_permission treats PROMPT as "let
        # the OS prompt", so PROMPT is also acceptable here. We return
        # GRANTED to avoid double-prompting.)
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
    """Probe macOS microphone permission via AVFoundation (pyobjc).

    Maps ``AVAuthorizationStatus`` values:

    - ``AVAuthorizationStatusAuthorized`` (2) → ``GRANTED``
    - ``AVAuthorizationStatusDenied`` (1) → ``DENIED``
    - ``AVAuthorizationStatusRestricted`` (3) → ``DENIED`` (parental
      controls block access — functionally denied for the user)
    - ``AVAuthorizationStatusNotDetermined`` (0) → ``PROMPT`` (the OS
      will show the consent dialog on first access)
    """
    # short-circuit when pyobjc isn't installed. Avoids paying
    # the ``from AVFoundation import ...`` lookup cost on every probe.
    if not _p._is_pyobjc_available():
        return _p.MicrophonePermissionState.UNKNOWN

    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import-not-found]
    except ImportError:
        # pyobjc was cached as available but AVFoundation isn't
        # importable (partial pyobjc install). Flip the cache so future
        # probes short-circuit to UNKNOWN without re-attempting the import.
        _p._PYOBJC_AVAILABLE = False
        return _p.MicrophonePermissionState.UNKNOWN

    try:
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio())
    except Exception:
        log.exception("[PERMISSION] AVCaptureDevice.authorizationStatusForMediaType_ failed")
        return _p.MicrophonePermissionState.UNKNOWN

    # AVAuthorizationStatus enum values (int):
    # 0 = NotDetermined, 1 = Denied, 2 = Authorized, 3 = Restricted
    if status == 2:
        return _p.MicrophonePermissionState.GRANTED
    if status == 0:
        return _p.MicrophonePermissionState.PROMPT
    if status in (1, 3):
        return _p.MicrophonePermissionState.DENIED
    return _p.MicrophonePermissionState.UNKNOWN


def _open_macos_microphone_settings() -> None:
    """Open System Settings -> Privacy & security -> Microphone.

    Mirrors :func:`voice_typer.server.permissions.accessibility._open_macos_accessibility_settings`
    but targets the Microphone pane via the ``Privacy_Microphone`` deep-link
    marker. Falls back to opening the Security & Privacy prefpane directly
    (macOS 12 and earlier).
    """
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
    """Actively trigger the macOS OS consent dialog for microphone access.

    Uses AVFoundation's
    ``AVCaptureDevice.requestAccessForMediaType:completionHandler:``
    to programmatically request microphone access. On a machine without
    pyobjc / AVFoundation (e.g. dev sandbox, Linux container), this is
    a silent no-op - the OS will instead prompt on the first
    PortAudio device open.
    """
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
