"""Configuration management with platform-aware storage."""

# ARCH-REFAC-001: validators extracted to config_validators.py
# ──────────────────────────────────────────────────────────────────────────
# This module previously contained both the config-loading code (JSON
# parsing, schema migration, atomic writes, the ``Config`` dataclass)
# *and* the IPC ``set_config`` input-validation layer (the per-field
# validator factories, the pre-built validator instances, the
# ``IPC_CONFIG_ALLOWLIST`` map, and the ``validate_config_update``
# entry point).  The two concerns have been split:
#
#   - ``config.py``             (this file) → loading, saving, dataclass
#   - ``config_validators.py``              → pure input validators
#
# The validator symbols are re-exported from this module via a wildcard
# ``from .config_validators import *`` at the very bottom, so any
# existing ``from voice_typer.server.config import validate_config_update``
# (or ``import IPC_CONFIG_ALLOWLIST``) keeps working unchanged.
# ``ALLOWED_USER_MODELS`` is imported explicitly at the top because
# ``Config.load()`` consults it during schema migration.
# ──────────────────────────────────────────────────────────────────────────

import contextlib
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from voice_typer.server.config_validators import ALLOWED_USER_MODELS
from voice_typer.server.platform_utils import is_macos, is_windows

log = logging.getLogger("voice_typer.server.config")


def _default_hotkey_for_platform() -> str:
    """NATIVE-001: Return the platform-appropriate default hotkey.

    FIX-HOTKEY-ARCHITECTURE: Caps Lock is now the default on ALL
    platforms (including macOS). It is universally present, isolated
    (rarely used in shortcuts), and easy to remap. The previous
    platform-specific defaults (``<fn>`` on macOS, ``<f2>`` on unknown
    platforms) caused inconsistency and the Fn key is firmware-only on
    most Windows/Linux laptops, making it a poor cross-platform default.

    Platform notes:
    - Windows: the native binary (``windows-key-listener.exe``)
      suppresses the caps-lock toggle via ``WH_KEYBOARD_LL``. The
      legacy ``WindowsNativeHotkey`` polling backend also suppresses
      the toggle programmatically via ``keybd_event``.
    - Linux: neutralize the toggle via
      ``setxkbmap -option caps:none`` (documented in onboarding).
    - macOS: Caps Lock works once Accessibility is granted. The Fn /
      Globe key remains available as an alternative in the dropdown.
    - Other platforms: ``<caps_lock>`` (legacy ``<f2>`` is no longer
      used as a default — the function keys are not universally
      present on laptop keyboards without an Fn combo).
    """
    return "<caps_lock>"


def _secure_atomic_write(path: Path, content: str) -> None:
    """Write content to ``path`` atomically and securely.

    NEW-SEC-008: prevents symlink-TOCTOU attacks by:
    1. Writing to a temp file in the same directory (so os.replace
       is atomic on the same filesystem).
    2. On POSIX, using ``os.open(tmp, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)``
       for the temp file. ``O_EXCL`` prevents a pre-created temp file
       from being hijacked; ``O_NOFOLLOW`` refuses to follow symlinks.
    3. On POSIX, tightening the target directory to 0o700 before the
       write so the temp file is not world-readable.
    4. Using ``os.replace(tmp, target)`` which is atomic on POSIX
       and does NOT follow symlinks on the target (it replaces the
       directory entry).

    SEC-007: file mode 0o600 ensures API keys in config.json are not
    world-readable on multi-user POSIX systems.

    Parameters
    ----------
    path : Path
        Target file path.
    content : str
        Content to write (UTF-8 encoded).
    """
    tmp_path = None
    try:
        # Create temp file in same directory for atomic rename
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        if not is_windows():
            # POSIX: use O_NOFOLLOW to prevent symlink attacks on the
            # temp file itself, and O_EXCL to prevent a pre-created
            # temp file from being hijacked.
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
        else:
            # Windows: O_NOFOLLOW not available, but NTFS ACLs under
            # %APPDATA% are per-user. Use standard open + fsync.
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        # os.replace is atomic and does NOT follow symlinks on the
        # target — it replaces the directory entry itself.
        os.replace(str(tmp_path), str(path))
    except Exception:
        # Clean up temp file on failure
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def _secure_read_text(path: Path, *, encoding: str = "utf-8") -> str:
    """SEC-002: Read text from a file securely, refusing to follow symlinks.

    On POSIX, opens the file with ``os.O_RDONLY | os.O_NOFOLLOW`` to
    prevent symlink-TOCTOU attacks. If ``path`` is a symlink, the open
    call raises ``OSError`` with ``errno=ELOOP`` (or ``EINVAL`` on some
    kernels). On Windows, checks for reparse points before reading.

    After opening, uses ``os.fstat()`` to verify the inode so that a
    race between the open and the read is detectable (the file could be
    replaced by a symlink or different file in the window between
    ``open()`` and ``read()`` — on Linux this is extremely unlikely
    due to O_NOFOLLOW, but the inode check provides defense in depth).

    Parameters
    ----------
    path : Path
        File to read.
    encoding : str
        Text encoding (default UTF-8).

    Returns
    -------
    str
        File contents as a string.

    Raises
    ------
    OSError
        If the file is a symlink (POSIX) or cannot be opened.
    ValueError
        If the inode changed between open and read (TOCTOU detected).
    """
    if not is_windows():
        # POSIX: O_NOFOLLOW refuses to follow symlinks
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            # SEC-002: Record the inode after opening to detect TOCTOU replacement
            stat_before = os.fstat(fd)
            f = os.fdopen(fd, "r", encoding=encoding)
            try:
                content = f.read()
                # SEC-002: Re-stat the fd to verify inode hasn't changed
                # Must do this before f.close() since close() releases the fd
                stat_after = os.fstat(fd)
                if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                    raise ValueError(f"SEC-002: inode changed during read of {path} — possible TOCTOU attack")
            finally:
                f.close()
            return content
        except Exception:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
    else:
        # Windows: check for reparse points (symlinks/junctions) before reading
        # NTFS reparse points have the FILE_ATTRIBUTE_REPARSE_POINT bit set.
        try:
            # `st_file_attributes` is a Windows-only attribute on
            # `os.stat_result`. Use ``getattr`` with a default of 0 so
            # the type-checker doesn't reject the access on the
            # cross-platform `stat_result` type (which doesn't declare
            # this attribute). On non-Windows platforms the attribute
            # is absent at runtime and ``getattr`` returns 0, so the
            # reparse-point check is a no-op (correct behavior —
            # reparse points are a Windows-only NTFS concept).
            stat_result = os.lstat(str(path)) if hasattr(os, "lstat") else None
            attrs = getattr(stat_result, "st_file_attributes", 0) or 0
            if attrs & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
                raise OSError(f"SEC-002: refusing to follow reparse point: {path}")
        except (AttributeError, OSError):
            pass  # lstat not available or file doesn't exist; open() will catch it
        with open(path, encoding=encoding) as f:
            # SEC-002: verify inode on Windows too (using os.fstat on the fileno)
            stat_before = os.fstat(f.fileno())
            content = f.read()
            stat_after = os.fstat(f.fileno())
            if stat_before.st_ino != stat_after.st_ino or stat_before.st_dev != stat_after.st_dev:
                raise ValueError(f"SEC-002: inode changed during read of {path} — possible TOCTOU attack")
            return content


def _legacy_config_dir() -> Path | None:
    """Get the legacy platform-specific config directory, if different from new one.

    NEW-DEAD-017: This function is never called in production code.
    Kept for backward compatibility with external scripts that may
    import it.
    """
    if is_windows():
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    return legacy if legacy != _config_dir() else None


def _validate_path_safety(path: Path, parent: Path) -> Path:
    """Resolve and validate that path stays within parent directory.

    SEC-005: prevents path traversal attacks when user-supplied env vars
    (VOICE_TYPER_CONFIG_DIR, XDG_DATA_HOME, etc.) contain ``..`` sequences
    that could escape the expected parent directory.
    """
    resolved = path.resolve()
    parent_resolved = parent.resolve()
    if not str(resolved).startswith(str(parent_resolved)):
        raise ValueError(f"Path traversal detected: {path} escapes {parent}")
    return resolved


def _is_path_within(path: Path, root: Path) -> bool:
    """RW-5: whether ``path`` is ``root`` itself or a descendant of it.

    Cross-platform path-containment check used by
    :func:`_validate_import_path`.  Both arguments are ``resolve()``-d
    first so symlinks and ``..`` segments are canonicalized before
    comparison.

    On Windows and macOS the default filesystem is case-insensitive, so
    the comparison lower-cases both sides on those platforms; on Linux
    the comparison is case-sensitive (matching the filesystem).

    Uses :func:`os.path.commonpath` to correctly respect directory
    boundaries — ``/home/userX`` is NOT considered within
    ``/home/user`` (a naive ``str.startswith`` would incorrectly accept
    it).  ``commonpath`` also handles the root-directory edge case
    (``/etc`` IS within ``/``).
    """
    import os.path

    try:
        p_resolved = str(path.resolve())
        r_resolved = str(root.resolve())
    except (OSError, RuntimeError):
        # Path.resolve() can raise on some platforms if the path is
        # not decodable; treat that as "not within".
        return False
    if sys.platform in ("win32", "darwin"):
        p_resolved = p_resolved.lower()
        r_resolved = r_resolved.lower()
    try:
        common = os.path.commonpath([p_resolved, r_resolved])
    except ValueError:
        # commonpath raises ValueError if the paths are on different
        # drives (Windows) or if one is absolute and the other is not.
        # Either way, ``path`` cannot be within ``root``.
        return False
    return common == r_resolved


def _validate_import_path(dir_path: str) -> str:
    """RW-5: validate that ``dir_path`` is within an allowed root.

    Used by the ``import_model`` IPC handler to reject arbitrary
    filesystem paths the user did not pick via the file chooser.

    Allowed roots (the directory itself or a descendant):
      - the user's home directory — covers ``~/Downloads``,
        ``~/Documents``, the default HF cache at
        ``~/.cache/huggingface/hub``, etc.
      - the OS temp directory (``tempfile.gettempdir()``) — covers
        ``/tmp``, ``%TEMP%``, etc.
      - the app's own HF cache directory (``_config_dir() /
        "huggingface" / "hub"``) — so re-importing from the app's
        cache is allowed.
      - ``$HF_HOME`` if set — some users point this at a custom
        location (e.g. an external drive mounted under a non-home
        path).

    Returns the resolved path as a string.  Raises ``ValueError`` if
    the path is outside all allowed roots.
    """
    import os
    import tempfile

    resolved = Path(dir_path).resolve()
    allowed_roots = [
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        (_config_dir() / "huggingface" / "hub").resolve(),
    ]
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        allowed_roots.append(Path(hf_home).resolve())
    for root in allowed_roots:
        if _is_path_within(resolved, root):
            return str(resolved)
    raise ValueError(
        f"Import path '{dir_path}' is outside the allowed roots (home directory, temp directory, or HF cache)."
    )


def _validate_systemroot() -> None:
    """SEC-audit-011: Validate the SystemRoot environment variable on Windows.

    The ``SystemRoot`` env var (e.g. ``C:\\Windows``) is used by Python's
    ``os.path`` module and various Win32 APIs to locate system DLLs.  An
    attacker who can set this variable before our process starts could
    redirect DLL lookups to a malicious directory.  This function verifies
    that ``SystemRoot`` points to an existing directory on Windows and
    rejects values that contain path traversal sequences or unusual
    characters.

    On non-Windows platforms, this is a no-op.
    """
    if not is_windows():
        return

    systemroot = os.environ.get("SYSTEMROOT", "")
    if not systemroot:
        # SystemRoot not set — unusual but not a direct attack vector
        # for our process.  Windows APIs may fail later; we just log.
        log.warning("[CONFIG] SystemRoot environment variable is not set")
        return

    # Check for path traversal
    if ".." in systemroot:
        log.error(
            "[CONFIG] SystemRoot contains path traversal ('..'): %s — "
            "possible DLL injection attack. Resetting to default.",
            systemroot,
        )
        # Try to use the standard default
        default = r"C:\Windows"
        if Path(default).is_dir():
            os.environ["SYSTEMROOT"] = default
        return

    # Check for unusual characters that could indicate tampering
    import re

    if re.search(r'[<>|"&\'\n\r\t]', systemroot):
        log.error(
            "[CONFIG] SystemRoot contains unusual characters: %r — possible injection attack. Resetting to default.",
            systemroot,
        )
        default = r"C:\Windows"
        if Path(default).is_dir():
            os.environ["SYSTEMROOT"] = default
        return

    # Verify the directory exists
    if not Path(systemroot).is_dir():
        log.error(
            "[CONFIG] SystemRoot does not point to an existing directory: %s — "
            "possible tampering. Resetting to default.",
            systemroot,
        )
        default = r"C:\Windows"
        if Path(default).is_dir():
            os.environ["SYSTEMROOT"] = default
        return

    # SEC-audit-011: Verify SystemRoot contains System32\notepad.exe.
    # This is the canonical sanity check — every valid Windows
    # installation has notepad.exe in System32.  If it's missing, the
    # SystemRoot value is almost certainly invalid or tampered.
    notepad_path = Path(systemroot) / "System32" / "notepad.exe"
    if not notepad_path.exists():
        log.error(
            "[CONFIG] SystemRoot does not contain System32\\notepad.exe: %s — "
            "possible tampering. Falling back to hardcoded notepad path.",
            systemroot,
        )
        # Do NOT reset SystemRoot itself (other system DLLs may still be
        # valid), but the caller should use the hardcoded fallback for
        # notepad specifically.


def _config_dir() -> Path:
    """Get the voice-typer data directory.

    NEW-CLI-004: honors VOICE_TYPER_CONFIG_DIR env var.
    NEW-XPLAT-001: uses platform-appropriate paths instead of always
    ``~/.voice-typer``.  On Windows this is ``%APPDATA%/voice-typer``,
    on macOS ``~/Library/Application Support/voice-typer``, on Linux
    ``$XDG_DATA_HOME/voice-typer`` (falling back to
    ``~/.local/share/voice-typer``).  The legacy ``~/.voice-typer`` is
    still checked first for migration — existing users' data is
    automatically found and used.

    SEC-005: user-supplied env vars are validated for path traversal.
    """
    custom = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if custom:
        custom_path = Path(custom)
        # SEC-005: validate that custom path doesn't traverse above home
        try:
            _validate_path_safety(custom_path, Path.home())
        except ValueError:
            log.warning("[CONFIG] VOICE_TYPER_CONFIG_DIR path traversal detected: %s", custom)
            # Fall through to default paths
        else:
            return custom_path

    # NEW-XPLAT-001: check for legacy ~/.voice-typer first (migration
    # path — existing users keep their data where it is).
    legacy = Path.home() / ".voice-typer"
    if legacy.exists():
        return legacy

    # Platform-specific paths for new installations.
    if is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata) / "voice-typer"
            # SEC-005: validate APPDATA-derived path
            try:
                _validate_path_safety(appdata_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] APPDATA path traversal detected: %s", appdata)
            else:
                return appdata_path
    elif is_macos():
        return Path.home() / "Library" / "Application Support" / "voice-typer"
    else:
        # Linux / FreeBSD: honor XDG_DATA_HOME.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            xdg_path = Path(xdg) / "voice-typer"
            # SEC-005: validate XDG_DATA_HOME-derived path
            try:
                _validate_path_safety(xdg_path, Path.home())
            except ValueError:
                log.warning("[CONFIG] XDG_DATA_HOME path traversal detected: %s", xdg)
            else:
                return xdg_path
        return Path.home() / ".local" / "share" / "voice-typer"

    # Fallback for any platform where the above checks didn't return.
    return legacy


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%)."""
    if is_windows():
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif is_macos():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    if not legacy.exists() or legacy.resolve() == _config_dir().resolve():
        return
    target = _config_dir()
    if target.exists():
        return
    import shutil

    shutil.copytree(legacy, target, dirs_exist_ok=True)
    log.info("[CONFIG] Migrated data from %s to %s", legacy, target)


_CURRENT_SCHEMA_VERSION = 2

# NEW-DEAD-018: _MIGRATIONS infrastructure for schema version migrations.
# ADR 0007: v2 migrates old audio preset names and deprecated fields.
_MIGRATIONS: dict[int, Any] = {}


def _migrate_to_v2(data: dict) -> dict:
    """Migrate config from schema v1 to v2 (ADR 0007 — filter chain).

    Changes:
    - Rename audio_preset "recommended" → "auto", "none" → "off"
    - If noise_filter_enabled was False, set audio_preset="off"
    - If noise_filter_rnnoise was True and no method set, set method="rnnoise"
    - Old noise_filter_gate_threshold (linear) is left in place; the new
      gate uses open/close dB thresholds with OBS-style defaults.
    - normalize_audio / normalize_target_peak left in place (ignored at runtime).
    """
    # Rename old preset names
    preset = data.get("audio_preset", "auto")
    if preset == "recommended":
        data["audio_preset"] = "auto"
    elif preset == "none":
        data["audio_preset"] = "off"

    # If noise_filter_enabled was False, switch to "off" preset
    if data.get("noise_filter_enabled") is False and "audio_preset" not in data:
        data["audio_preset"] = "off"

    # If RNNoise was explicitly enabled, ensure method is set
    if data.get("noise_filter_rnnoise") is True and "noise_suppression_method" not in data:
        data["noise_suppression_method"] = "rnnoise"

    return data


_MIGRATIONS[2] = _migrate_to_v2


@dataclass
class Config:
    """Application configuration."""

    schema_version: int = _CURRENT_SCHEMA_VERSION
    # NEW-CQ-016: warnings from the last load() call. Populated by
    # _validate_non_numeric_fields when a field had an invalid type
    # and was reset to default. The IPC layer can surface these to
    # the renderer so the user knows their config was corrected.
    last_load_warnings: list | None = None

    # Hotkey
    # NATIVE-001 / FIX-HOTKEY-ARCHITECTURE: default hotkey is now
    # ``<caps_lock>`` on ALL platforms (was previously <fn> on macOS
    # and <f2> on unknown platforms). Caps Lock is universally present,
    # isolated, and easy to remap. See ``_default_hotkey_for_platform``
    # for platform-specific suppression notes.
    hotkey: str = _default_hotkey_for_platform()

    # Recording
    sample_rate: int = 16000
    microphone: str | None = None  # None = system default

    # Transcription
    model_size: str = "small.en"
    language: str = "en"
    device: str = "cuda"  # cuda, cpu
    beam_size: int = 1  # 1 = fastest greedy decoding; higher values trade speed for accuracy
    best_of: int = 1
    condition_on_previous_text: bool = False

    # Hidden streaming transcription
    streaming_transcription: bool = True
    streaming_chunk_seconds: float = 12.0
    streaming_step_seconds: float = 5.0
    streaming_left_overlap_seconds: float = 3.0
    streaming_right_guard_seconds: float = 1.5
    streaming_min_first_chunk_seconds: float = 6.0
    streaming_silence_threshold: float = 0.003

    # Behavior
    autostart: bool = True
    paste_on_stop: bool = True
    # NEW-MISMATCH-001: client-side field now has a server counterpart
    unsafe_paste_on_unknown_focus: bool = False  # paste even when focus detection fails
    show_notifications: bool = True
    # PLAT-013: warn when pasting into an elevated process from non-elevated
    warn_elevated_paste: bool = True
    # PLAT-014: warn when pasting into a password field
    warn_password_paste: bool = True
    # PW-3: Master toggle for the OS-level prewarm scheduled task.
    # Defaults ON so existing users keep fast cold-boot behaviour.
    # When False, the prewarm task is unregistered at startup and the
    # prewarm entrypoint exits early with EXIT_DISABLED. The "Run
    # Prewarm Now" button in the About page remains usable for
    # on-demand warming even when scheduled prewarm is disabled.
    fast_startup: bool = True

    # ASR backend selection
    asr_backend: str = "whisper"  # "whisper", "qwen", or "parakeet"
    qwen_model_path: str | None = None  # local path to Qwen3-ASR weights
    parakeet_model_path: str | None = None  # local override for Parakeet weights (None = HF cache)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: str | None = None

    # Logging
    log_transcriptions: bool = False

    # SEC-012: Clipboard security settings.
    # ADR-0010 §8.2: removed ``clipboard_clear_delay_seconds`` (dead —
    # was only read by the now-deleted ``schedule_clipboard_clear``).
    # Added ``clipboard_restore_delay_ms`` (now actually consulted in
    # ``clipboard.py:paste()`` and refreshed at runtime via
    # ``refresh_config()`` when the user changes settings).
    clipboard_save_restore: bool = True  # save/restore previous clipboard content after paste
    clipboard_restore_delay_ms: int = 150  # delay between paste keystroke and clipboard restore (ms)

    # ─── P1 Features ───────────────────────────────────────────────

    # Push-to-talk mode (hold to record, release to stop)
    recording_mode: str = "toggle"  # "toggle" or "push_to_talk"
    push_to_talk_hotkey: str = ""  # Separate hotkey for PTT (empty = same as toggle)

    # ESC to cancel at any stage
    # NEW-UX-020: Esc-to-cancel defaults ON so users can cancel a
    # recording they started by mistake.  Previously OFF and hidden in
    # Settings, so the only way to cancel was to wait for silence
    # auto-stop or toggle the hotkey again.
    esc_cancel_enabled: bool = True

    # Repaste last transcription
    repaste_hotkey: str = "<ctrl>+<alt>+v"  # Hotkey for repasting last

    # Auto-punctuation (runs AFTER template matching)
    # NEW-UX-010: Auto-punctuation defaults ON.  The #1 voice-typing
    # complaint is missing punctuation.  This feature adds periods,
    # commas, and capitalization automatically.  Previously OFF and
    # undocumented in-app.
    auto_punctuation: bool = True

    # ─── P2 Features ───────────────────────────────────────────────

    # Templates
    templates_enabled: bool = True

    # Vocabulary
    vocabulary_enabled: bool = True

    # Cloud ASR backends
    cloud_api_key: str = ""
    cloud_api_url: str = ""
    cloud_model: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""
    deepgram_api_key: str = ""

    # LLM text polishing
    llm_polish: bool = False
    llm_api_key: str = ""
    llm_api_url: str = "https://api.openai.com/v1/chat/completions"
    llm_model: str = "gpt-4o-mini"
    llm_preset: str = "professional"  # professional/casual/email/code

    # PRIVACY-001: explicit user consent that text may leave the
    # machine for LLM polishing.  Separate from ``llm_polish`` so that
    # turning the toggle off doesn't silently revoke consent (and
    # turning it back on doesn't bypass the consent dialog).
    llm_polish_consent: bool = False

    # NEW-PRIV-005: explicit consent that model weights are downloaded
    # from HuggingFace on first use.  The download reveals the user's
    # IP to a US-headquartered third party — GDPR Art. 13/44 require
    # disclosure + consent for this.  When False, the first model
    # download shows a consent dialog in the renderer; only after the
    # user accepts does the download proceed.
    huggingface_consent: bool = False

    # NEW-PRIV-006: explicit per-provider consent for cloud ASR.
    # Storing an API key alone is NOT consent — the user must
    # explicitly agree that audio will be sent to that provider.
    # Each provider has its own flag so consent is granular.
    cloud_openai_consent: bool = False
    cloud_groq_consent: bool = False
    cloud_deepgram_consent: bool = False

    # NEW-PRIV-009: explicit consent that voice recordings (which may
    # constitute biometric data under BIPA / GDPR Art. 9) are
    # processed locally for transcription.  Required for compliance
    # in jurisdictions that classify voice as biometric.
    voice_biometric_consent: bool = False

    # NEW-UX-029: play a short audio cue when recording starts/stops.
    # Many users (especially blind users) prefer an auditory signal
    # instead of (or in addition to) the visual indicator.  Default
    # ON — most users benefit from the audible start/stop cue; those
    # who prefer silence can disable it in Settings → Behavior.
    sound_feedback_enabled: bool = True

    # Crash recovery
    crash_recovery_enabled: bool = True

    # T020 (superseded): an earlier draft removed AudioQualityAnalyzer as
    # dead code and archived a stale copy to archive/. The analyzer was
    # subsequently revived and is actively used — see app.py:208
    # (instantiation), app.py:_on_audio_quality_chunk and
    # _finalize_audio_quality_report (per-chunk + post-stop analysis),
    # and recording_controller.py:403 (invocation after stop()).
    # FIX-HOTKEY-AND-NOTIFICATION: the user-facing tray notification that
    # reported "Low volume / High noise" after each dictation was deemed
    # annoying. The default is now False, AND the app-side code path that
    # shows the notification is short-circuited (see
    # ``_finalize_audio_quality_report`` in app.py — early return at the
    # top so no tray notification is EVER shown, even if a user manually
    # flips this flag to True in their config file). The quality analysis
    # may still run for internal logging, but NEVER surfaces a tray
    # notification. The field is kept for backward compatibility with
    # existing config files.
    audio_quality_warnings: bool = False

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom).  Default "bottom" — the
    # recording bubble sits at bottom-center, out of the way of most
    # app title bars and camera notches.
    bubble_position: str = "bottom"

    # Bubble behavior: show on record, or always visible
    bubble_behavior: str = "show_on_record"  # "show_on_record" or "always_visible"

    # Whether the bubble can be dragged by the user
    bubble_draggable: bool = True

    # Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
    bubble_show_on_startup: bool = True

    # History database
    history_retention_days: int = 90  # 0 = keep forever
    history_retention_count: int = 0  # 0 = unlimited
    history_max_entries: int = 1000

    # ─── P3 Features ───────────────────────────────────────────────

    # Onboarding
    onboarding_completed: bool = False
    # ERR-010: marks that onboarding was force-completed after repeated
    # setup failures so the app remains usable. Lets the UI show a
    # "configure manually" hint instead of looping the wizard.
    onboarding_failed: bool = False

    # Tray icon left-click behavior
    tray_left_click_action: str = "open_app"  # "open_app" or "toggle_dictation"

    # UX-008: Theme mode (system/light/dark)
    theme_mode: str = "system"
    # Theme preset — a built-in colour scheme applied on top of the
    # current theme_mode. "default" means no overrides.
    theme_preset: str = "default"
    # User-customised theme colours (only used when theme_preset == "custom").
    # Stored as nested dict: {"light": {var: val, ...}, "dark": {var: val, ...}}
    custom_theme: dict | None = None

    # UX-036: Accessibility
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    stop_on_silence_seconds: float = 60.0
    # RW-0 / SIMPLIFY-001: single explicit field replaces the previous 3-field split
    # (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu, and
    # max_recording_time_seconds=0). The old GPU/CPU auto-selection was invisible
    # to users and the "0 = automatic" convention was user-hostile. Now the field
    # is always a concrete value with min 300 (5 min) / max 3600 (60 min).
    max_recording_time_seconds: int = 900  # 15 minutes

    # NOTE: dead_air_timeout (float) was REMOVED in RW-0.
    # It was redundant with stop_on_silence_seconds — both called the same
    # on_silence_auto_stop callback. Auto-stop already resets on every speech
    # detection, so the "only after speech" condition dead air added was
    # unnecessary. Do NOT re-add. See RecordingSettingsSection.tsx comment.

    # AUDIO-014: configurable VAD/silence thresholds (overridden by
    # auto-calibration at recording start). 0.0 = use built-in defaults.
    # NOTE: these are kept for backward compat but are no longer read
    # by the recording callback (dead code per ADR 0007 §4.3).
    silence_rms_threshold: float = 0.0  # DEPRECATED — dead code
    silence_peak_threshold: float = 0.0  # DEPRECATED — dead code

    # AUDIO-013: VAD configuration for the recording callback.
    # ADR 0007 §4.1: use_silero_vad defaults to True (torch is installed).
    # Falls back to RMS if Silero is unavailable.
    use_silero_vad: bool = True  # ADR 0007: was False, now True (torch available)
    vad_speech_threshold: float = 0.5  # Silero VAD prob > this → speech candidate
    vad_silence_threshold: float = 0.3  # Silero VAD prob < this → silence candidate

    # AUDIO-CH: number of channels to request from the input device.
    # Default 1 (mono) — appropriate for dictation. Set to 0 for
    # device default (auto-detect from device's max_input_channels).
    recording_channels: int = 1

    # AUDIO-PRE: pre-roll buffer captures audio before recording starts.
    # 0 = disabled (default, for privacy). When > 0, continuously
    # records N seconds of audio into a ring buffer and prepends it
    # when the user presses the hotkey, reducing cold-start latency.
    pre_roll_buffer_seconds: float = 0.0

    # ADR 0007 §5.2: normalize_audio and normalize_target_peak REMOVED.
    # Replaced by the Compressor filter in the audio filter chain.
    # Fields kept for backward compat (migration ignores them).
    normalize_audio: bool = True  # DEPRECATED — replaced by Compressor
    normalize_target_peak: float = 0.7  # DEPRECATED — replaced by Compressor

    # ─── Volume ducking (v1.1.0) ────────────────────────────────────
    # Reduces system volume during dictation to prevent speaker output
    # from bleeding into the microphone.
    #
    # UX-2: the Settings UI was simplified to just two controls:
    #   1. Auto Duck Volume (on/off)
    #   2. Duck Level (0–50%)
    # The remaining fields are internal (not exposed in the UI) and have
    # sensible defaults. They're kept in the config for backward compat
    # (existing user configs with custom values still load) and for
    # power users who edit config.json directly.
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.20  # 0.0–1.0 perceptual-linear (20% duck)
    # UX-2: per-session ducking removed — now always ducks all audio
    # (master volume) cross-platform. The field is kept for backward
    # compat but ignored at runtime (per_session is always False).
    volume_duck_per_session: bool = False  # DEPRECATED — always False at runtime
    # UX-2: fade duration is now a fixed 200ms default (was 150ms).
    # Not exposed in the UI. Power users can override in config.json.
    volume_duck_fade_ms: int = 200  # 0–1000, 0 = instant
    # UX-2: smart duck is now ALWAYS ON when volume_duck_enabled is True.
    # Not exposed as a separate toggle — merged into Auto Duck Volume.
    # The field is kept for backward compat but ignored at runtime
    # (smart_duck is always True when ducking is enabled).
    volume_duck_smart: bool = True  # DEPRECATED — always True at runtime
    # UX-2: smart-duck poll interval is now a fixed 500ms default.
    # Not exposed in the UI. Power users can override in config.json.
    volume_duck_smart_poll_interval_ms: int = 500

    # ─── Audio enhancement preset (ADR 0007) ─────────────────────────
    # Preset name that controls the entire filter chain:
    #   "auto"        — all filters ON, RNNoise (best for 90% of users)
    #   "studio"      — minimal processing (quiet room, good mic)
    #   "noisy_room"  — aggressive, DeepFilterNet
    #   "off"         — all filters OFF
    #   "custom"      — user controls each filter individually
    # The preset is applied at startup (Config.load) and on explicit
    # set_config. See voice_typer/server/audio_presets.py for the
    # single source of truth.
    audio_preset: str = "auto"

    # ─── Noise filtering (ADR 0007 — filter chain) ───────────────────
    # Each filter has an enable flag + parameters. The filter chain
    # (voice_typer/server/audio_filters/) is built from these fields
    # by audio_chain_builder.build_chain(). Chain order:
    #   HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter
    #
    # Legacy fields (noise_filter_enabled, noise_filter_rnnoise,
    # noise_filter_post_capture, noise_filter_gate_threshold) are kept
    # for backward compat but migrated/ignored per ADR 0007 §5.
    noise_filter_enabled: bool = True  # DEPRECATED — use audio_preset != "off"
    noise_filter_highpass: bool = True
    noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
    noise_filter_gate: bool = True
    noise_filter_gate_threshold: float = 0.003  # DEPRECATED — use open/close thresholds
    noise_filter_gate_hold_ms: float = 200.0  # ADR 0007: was 150, now 200 (matches OBS)
    noise_filter_rnnoise: bool = True  # ADR 0007: was False, now True (RNNoise is default dep)
    noise_filter_post_capture: bool = True  # DEPRECATED — post-capture removed per ADR 0007

    # ADR 0007 §5.1: New filter chain fields
    # Noise suppressor backend selection
    noise_suppression_method: str = "rnnoise"  # "rnnoise" | "deepfilternet" | "speex" | "none"

    # NoiseGate (OBS-style, replaces single threshold)
    noise_filter_gate_open_threshold_db: float = -26.0
    noise_filter_gate_close_threshold_db: float = -32.0
    noise_filter_gate_attack_ms: float = 25.0
    noise_filter_gate_release_ms: float = 150.0

    # Equalizer (3-band)
    noise_filter_eq: bool = True
    noise_filter_eq_low_db: float = -3.0
    noise_filter_eq_mid_db: float = 3.0
    noise_filter_eq_high_db: float = 2.0

    # Compressor (replaces normalize_audio + _agc_update)
    noise_filter_compressor: bool = True
    noise_filter_compressor_threshold_db: float = -18.0
    noise_filter_compressor_ratio: float = 3.0
    noise_filter_compressor_attack_ms: float = 6.0
    noise_filter_compressor_release_ms: float = 60.0
    noise_filter_compressor_output_gain_db: float = 0.0

    # Limiter (brick-wall)
    noise_filter_limiter: bool = True
    noise_filter_limiter_ceiling_db: float = -6.0
    noise_filter_limiter_release_ms: float = 60.0

    # Notch filter (50/60Hz hum) — optional, default OFF
    noise_filter_notch: bool = False
    noise_filter_notch_frequency_hz: float = 0.0  # 0 = auto-detect (60Hz Americas default)

    # ─── P4: AI grammar / punctuation / capitalization ─────────────
    # Rule-based, offline enhancement applied AFTER LLM polish and
    # BEFORE the result is stored to history / pasted.  See
    # ``voice_typer/server/ai_enhancement.py``.  The master toggle
    # defaults to OFF — the user must explicitly opt in via Settings
    # → AI Enhancement so existing users don't see behavior changes
    # after upgrading.  The three sub-toggles default to True so
    # that, once the master toggle is flipped, the feature "just
    # works" without further configuration.
    ai_enhancement_enabled: bool = False  # master toggle (opt-in)
    auto_capitalize: bool = True  # capitalize sentence starts + proper nouns
    auto_punctuate: bool = True  # add periods at sentence boundaries
    fix_grammar_basics: bool = True  # fix bare "i", contractions, double spaces

    # ─── P5: Vocabulary automation ─────────────────────────────────
    # Confidence-score-based auto-correction suggestions.  When the
    # master toggle is ON, the dictation pipeline analyzes each
    # transcription for low-confidence words and suggests vocabulary
    # corrections.  Suggestions above ``vocabulary_auto_apply_threshold``
    # are auto-applied; the rest are queued for the user to review.
    # Defaults to OFF — the user must explicitly opt in via Settings.
    vocabulary_automation_enabled: bool = False  # master toggle (opt-in)
    # Below this segment-confidence, suggest corrections.  0.7 is a
    # common Whisper "low confidence" threshold (the model emits
    # avg_logprob values around -1.0 for uncertain words; the
    # pipeline normalizes to a 0–1 confidence where 0.7 corresponds
    # to roughly avg_logprob -0.4).
    vocabulary_auto_confidence_threshold: float = 0.7
    # Above this confidence, auto-apply suggestions without asking.
    # 0.95 is high enough that false positives are rare but low
    # enough that the auto-apply path actually fires in practice.
    vocabulary_auto_apply_threshold: float = 0.95

    def save(self) -> bool:
        """Save config to disk atomically via temp file + os.replace.

        Returns True on success, False on failure. Errors are logged but not raised.

        SEC-007: on POSIX, restricts file permissions to 0o600
        (owner-read/write only) and directory permissions to 0o700.
        Without this, default umask leaves config.json world-readable
        (0o644), leaking API keys and other settings to any
        co-located user.  On Windows the chmod is a no-op (NTFS ACLs
        are not affected by os.chmod, but the config dir is already
        under %APPDATA% which is per-user).

        NEW-SEC-008: uses os.open with O_NOFOLLOW on POSIX to prevent
        symlink TOCTOU attacks. A local attacker who pre-creates
        config.json as a symlink to ~/.bashrc would previously have
        their target overwritten via os.replace. O_NOFOLLOW refuses to
        follow symlinks on open, so the write fails instead.
        """
        try:
            path = _config_dir()
            path.mkdir(parents=True, exist_ok=True)
            if not is_windows():
                try:
                    os.chmod(path, 0o700)
                except OSError as e:
                    log.warning("[CONFIG] Failed to chmod config dir: %s", e)
            config_file = path / "config.json"
            content = json.dumps(asdict(self), indent=2)
            _secure_atomic_write(config_file, content)
            return True
        except (OSError, PermissionError) as e:
            log.error("[CONFIG] Failed to save config: %s", e)
            return False

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults.

        RW-9: failure-mode enumeration.  The previous implementation
        caught ``Exception`` and silently returned defaults — that hid
        genuine bugs (e.g. ``KeyError`` from a missing ``data[key]``
        access, or ``AttributeError`` from an unexpected ``None``) and
        system-level failures (``MemoryError``).  We now enumerate the
        *expected* failure modes that indicate a corrupt or unreadable
        config file and fall back to defaults with a WARNING log so
        the user can see *why* their settings were reset.

        Caught (fall back to defaults + WARNING log):

        * ``OSError`` (incl. ``PermissionError``) — file missing,
          locked, or unreadable.
        * ``json.JSONDecodeError`` — corrupt JSON syntax.
        * ``TypeError`` — parsed JSON is not a dict (e.g. ``[]`` or
          ``42``), or a field has an uncoercible type (e.g. ``null``
          for a float field).
        * ``ValueError`` — a field has the right type but an invalid
          string form (e.g. ``"abc"`` for ``float()``).

        Propagated (NOT caught — indicates a bug in our code or a
        system-level failure that should not be silently hidden):

        * ``KeyError`` — we use ``.get()`` everywhere; a ``KeyError``
          means someone introduced a ``data[...]`` access without a
          default.  Surface it as a bug.
        * ``AttributeError`` — same reasoning; an unexpected ``None``
          where a dict was assumed.
        * ``MemoryError`` / ``KeyboardInterrupt`` / ``SystemExit`` —
          system-level, never silently swallowed.
        """
        config_file = _config_dir() / "config.json"
        if config_file.exists():
            try:
                # SEC-002 / SEC-audit-011: use _secure_read_text to prevent
                # symlink-TOCTOU attacks when reading config.json
                raw_text = _secure_read_text(config_file)
                parsed = json.loads(raw_text)
                # RW-9: a valid JSON scalar (null/true/42/"x"/[]) is
                # not a valid config — raise TypeError with a clear
                # message so the failure mode is visible in the WARNING
                # log below (and matches the caught tuple).  Without
                # this, ``parsed.items()`` on a non-dict would raise
                # AttributeError, which we deliberately let propagate.
                if not isinstance(parsed, dict):
                    raise TypeError(f"config root must be a JSON object, got {type(parsed).__name__}")
                data = {k: v for k, v in parsed.items() if k in cls.__dataclass_fields__}

                # M3: Schema versioning and migration
                loaded_version = data.get("schema_version", 0)
                for version in range(loaded_version + 1, _CURRENT_SCHEMA_VERSION + 1):
                    migrator = _MIGRATIONS.get(version)
                    if migrator is not None:
                        data = migrator(data)
                data["schema_version"] = _CURRENT_SCHEMA_VERSION

                # Config fields were renamed (no migration needed):
                data["streaming_left_overlap_seconds"] = max(
                    float(data.get("streaming_left_overlap_seconds", 3.0)),
                    3.0,
                )
                data["streaming_right_guard_seconds"] = max(
                    float(data.get("streaming_right_guard_seconds", 1.5)),
                    1.5,
                )
                # NEW-CQ-017: enforce streaming config invariants so the
                # AudioWindowPlanner doesn't run forever or produce
                # overlapping windows that never advance.
                # - step < chunk: otherwise the planner skips untranscribed
                #   audio between windows.
                # - left_overlap < chunk: otherwise every window is a
                #   duplicate of the previous one.
                chunk = float(data.get("streaming_chunk_seconds", 12.0))
                step = float(data.get("streaming_step_seconds", 5.0))
                left_overlap = float(data.get("streaming_left_overlap_seconds", 3.0))
                if step >= chunk:
                    log.warning(
                        "[CONFIG] streaming_step_seconds (%.1f) >= streaming_chunk_seconds "
                        "(%.1f); clamping step to chunk/2",
                        step,
                        chunk,
                    )
                    data["streaming_step_seconds"] = chunk / 2.0
                if left_overlap >= chunk:
                    log.warning(
                        "[CONFIG] streaming_left_overlap_seconds (%.1f) >= streaming_chunk_seconds "
                        "(%.1f); clamping overlap to chunk/3",
                        left_overlap,
                        chunk,
                    )
                    data["streaming_left_overlap_seconds"] = chunk / 3.0
                # SIMPLIFY-001: clamp max_recording_time_seconds to valid range [300, 3600]
                # to handle old config files that had 0 = auto-select (which is now invalid).
                max_rec = int(data.get("max_recording_time_seconds", 900))
                if max_rec < 300 or max_rec > 3600:
                    log.warning(
                        "[CONFIG] max_recording_time_seconds=%d outside valid range [300, 3600], resetting to 900",
                        max_rec,
                    )
                    data["max_recording_time_seconds"] = 900

                if data.get("model_size") not in ALLOWED_USER_MODELS:
                    data["model_size"] = "small.en"

                # Validate qwen_model_path: must be an existing directory if set
                qwen_path = data.get("qwen_model_path")
                if qwen_path is not None:
                    p = Path(qwen_path)
                    if not p.exists() or not p.is_dir():
                        log.warning(
                            "[CONFIG] Config qwen_model_path=%s does not exist or is not a "
                            "directory, resetting to None",
                            qwen_path,
                        )
                        data["qwen_model_path"] = None
                    else:
                        # SEC-audit-007: Validate qwen_model_path is in a safe location
                        qwen_resolved = p.resolve()
                        safe_dirs = [_config_dir().resolve()]
                        hf_home = os.environ.get("HF_HOME")
                        if hf_home:
                            safe_dirs.append(Path(hf_home).resolve())
                        if not any(str(qwen_resolved).startswith(str(d)) for d in safe_dirs):
                            log.warning("[CONFIG] qwen_model_path outside safe directories: %s", qwen_path)

                # Validate corrections_path: must be an existing file if set
                corrections = data.get("corrections_path")
                if corrections is not None:
                    cp = Path(corrections)
                    if not cp.exists() or not cp.is_file():
                        log.warning(
                            "[CONFIG] Config corrections_path=%s does not exist or is not a file, resetting to None",
                            corrections,
                        )
                        data["corrections_path"] = None
                    else:
                        # SEC-audit-006 (Round 0 forward-port — M6):
                        # defense-in-depth path-traversal check.
                        # ``corrections_path`` is NOT in the IPC
                        # allowlist (can only be set via direct
                        # ``config.json`` edit), but a user who
                        # manually edits the config could point it at
                        # an arbitrary file.  The :mod:`text_cleanup`
                        # module reads + applies corrections from this
                        # file, so a malicious or accidentally-
                        # chosen path could expose sensitive data
                        # (e.g. log transcription text being matched
                        # against /etc/passwd contents).  Restrict the
                        # path to the user's home directory or the
                        # config directory — both are user-writable
                        # locations where the user has explicitly
                        # chosen to store data.
                        try:
                            cp_resolved = cp.resolve()
                            allowed_roots = [
                                Path.home().resolve(),
                                _config_dir().resolve(),
                            ]
                            if not any(_is_path_within(cp_resolved, root) for root in allowed_roots):
                                raise ValueError("corrections_path must be within the user home or config directory")
                        except ValueError as exc:
                            log.warning(
                                "[CONFIG] Config corrections_path=%s rejected: %s, resetting to None",
                                corrections,
                                exc,
                            )
                            data["corrections_path"] = None

                # SEC-009: Warn the user about privacy implications when
                # log_transcriptions is enabled.  Transcription text may
                # contain sensitive personal information (names, addresses,
                # medical details, etc.) that gets written to log files
                # on disk.  The warning is emitted once per config load
                # so it appears in the log on every startup if the flag
                # is active.
                if data.get("log_transcriptions"):
                    log.warning(
                        "[CONFIG] log_transcriptions is enabled — transcription text "
                        "(potentially containing PII) will be written to log files. "
                        "Disable this setting if you do not want speech content persisted "
                        "to disk."
                    )

                # H1: Validate non-numeric fields before construction
                data = cls._validate_non_numeric_fields(data)
                # NEW-CQ-016: extract load warnings before construction
                # (cls(**data) would fail on the _load_warnings key)
                load_warnings = data.pop("_load_warnings", [])

                instance = cls(**data)
                instance.last_load_warnings = load_warnings

                # AUDIO-PRESET-LOAD-FIX: apply the audio preset's filter
                # toggles on every load so that the individual
                # ``noise_filter_*`` fields are always consistent with
                # ``audio_preset``, even if the JSON is stale (e.g., the
                # preset was saved but the side-effect toggles were not,
                # a bug fixed in ``config_handlers.py``).  Without this,
                # a config file with ``audio_preset: "off"`` but all
                # ``noise_filter_highpass: True`` (the dataclass default)
                # would build a filter chain with all filters ON on
                # startup, making the preset appear to reset to Auto
                # despite the UI showing Off.
                try:
                    from voice_typer.server.audio_presets import apply_preset

                    apply_preset(instance.audio_preset, instance)
                except Exception:
                    log.debug("[CONFIG] apply_preset on load failed", exc_info=True)

                return instance
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
                # RW-9: enumerated failure modes — see the docstring
                # above for the rationale.  The warning includes the
                # exception class name (failure-mode indicator) and the
                # config file path so the user can see *which* file is
                # corrupt and *why* the app fell back to defaults.
                # Unexpected exceptions (KeyError, AttributeError,
                # MemoryError, KeyboardInterrupt, SystemExit) are NOT
                # caught here — they propagate so genuine bugs and
                # system-level failures are visible.
                log.warning(
                    "[CONFIG] %s loading config %s: %s. Using defaults.",
                    type(e).__name__,
                    config_file,
                    e,
                )
                return cls()
        return cls()

    @classmethod
    def _validate_non_numeric_fields(cls, data: dict) -> dict:
        """Validate and coerce bool and str fields in loaded config data.

        NEW-CQ-016: collects warnings in ``data['_load_warnings']`` so
        the caller (load()) can surface them via the
        ``last_load_warnings`` field. Previously warnings were only
        logged; the user had no way to know their config was corrected.

        NEW-DUP-005: this is NOT a duplicate of the type coercion that
        ``cls(**data)`` would do.  Python dataclasses do NOT coerce
        ``1`` → ``True`` or ``"true"`` → ``True`` — they store the raw
        value as-is, which would then fail downstream type checks
        (e.g. ``isinstance(cfg.autostart, bool)`` returns False for
        ``1``).  This validator is a migration layer that fixes up
        legacy on-disk configs (written by older versions of the app
        that used ints/strings for bool fields) BEFORE the dataclass
        constructor sees them.  Without it, a config.json with
        ``"autostart": 1`` would silently store ``1`` instead of
        ``True``, breaking every ``if cfg.autostart:`` check.
        """
        warnings: list[str] = []
        bool_fields = {
            "autostart",
            "paste_on_stop",
            "unsafe_paste_on_unknown_focus",
            "show_notifications",
            # PW-3: prewarm toggle is a bool so legacy configs that stored
            # it as "true"/"false" strings or 0/1 ints get coerced.
            "fast_startup",
            "text_cleanup_enabled",
            "streaming_transcription",
            "log_transcriptions",
            "condition_on_previous_text",
            "esc_cancel_enabled",
            "auto_punctuation",
            "llm_polish",
            "llm_polish_consent",
            # NEW-PRIV-005/006/009: privacy consent flags are bools.
            "huggingface_consent",
            "cloud_openai_consent",
            "cloud_groq_consent",
            "cloud_deepgram_consent",
            "voice_biometric_consent",
            # NEW-UX-029: sound feedback toggle.
            "sound_feedback_enabled",
            "crash_recovery_enabled",
            "audio_quality_warnings",
            "templates_enabled",
            "vocabulary_enabled",
            "waveform_bubble",
            "onboarding_completed",
            "onboarding_failed",
            "wayland_warned",
            "bubble_draggable",
            "bubble_show_on_startup",
            "volume_duck_enabled",
            "volume_duck_per_session",
            "volume_duck_smart",
            # STARTUP-6: volume_duck_smart_poll_interval_ms is an int (50-5000),
            # NOT a bool — it was misclassified here, causing the bool validator
            # to flag the default value 500 as invalid and log a spurious
            # "resetting to default 500" warning on every startup. It already
            # has its own int validator in IPC_CONFIG_ALLOWLIST.
            "noise_filter_enabled",
            "noise_filter_highpass",
            "noise_filter_gate",
            "noise_filter_rnnoise",
            "noise_filter_post_capture",
            # ADR 0007: new filter chain bool fields
            "noise_filter_eq",
            "noise_filter_compressor",
            "noise_filter_limiter",
            "noise_filter_notch",
            # P4: AI enhancement toggles.  All four are bools — the
            # master toggle defaults OFF, the three sub-toggles
            # default ON.  Include them here so legacy config files
            # that stored them as "true"/"false" strings or 0/1 ints
            # are coerced to real bools on load.
            "ai_enhancement_enabled",
            "auto_capitalize",
            "auto_punctuate",
            "fix_grammar_basics",
            # P5: vocabulary automation master toggle.
            "vocabulary_automation_enabled",
        }
        str_fields = {
            "hotkey",
            "language",
            "device",
            "asr_backend",
            "recording_mode",
            "push_to_talk_hotkey",
            "cloud_api_key",
            "cloud_api_url",
            "cloud_model",
            "openai_api_key",
            "groq_api_key",
            "deepgram_api_key",
            "llm_api_key",
            "llm_api_url",
            "llm_model",
            "llm_preset",
            "repaste_hotkey",
            "tray_left_click_action",
            "parakeet_model_path",
            "bubble_position",
            "bubble_behavior",
            "audio_preset",
            "noise_suppression_method",
            "theme_mode",
            "theme_preset",
        }
        defaults = cls()

        for field_name in bool_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, bool):
                continue
            # Coerce truthy/falsy values
            if val in (1, "1", "true", "True", "yes"):
                msg = f"Config field '{field_name}' had non-bool value {val!r}, coerced to True"
                log.warning("[CONFIG] %s", msg)
                warnings.append(msg)
                data[field_name] = True
            elif val in (0, "0", "false", "False", "no", ""):
                msg = f"Config field '{field_name}' had non-bool value {val!r}, coerced to False"
                log.warning("[CONFIG] %s", msg)
                warnings.append(msg)
                data[field_name] = False
            else:
                default_val = getattr(defaults, field_name)
                msg = f"Config field '{field_name}' had invalid value {val!r}, resetting to default {default_val!r}"
                log.warning("[CONFIG] %s", msg)
                warnings.append(msg)
                data[field_name] = default_val

        optional_str_fields = {"parakeet_model_path", "qwen_model_path"}

        for field_name in str_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, str):
                continue
            if val is None and field_name in optional_str_fields:
                continue
            default_val = getattr(defaults, field_name)
            msg = f"Config field '{field_name}' had non-string value {val!r}, resetting to default {default_val!r}"
            log.warning("[CONFIG] %s", msg)
            warnings.append(msg)
            data[field_name] = default_val

        # NEW-CQ-016: stash warnings so load() can surface them
        data["_load_warnings"] = warnings
        return data

    @property
    def config_dir(self) -> Path:
        return _config_dir()


# ──────────────────────────────────────────────────────────────────────────
# ARCH-REFAC-001: validator block moved to ``config_validators.py``.
# RW-06: the wildcard ``from voice_typer.server.config_validators import *``
# re-exported every symbol listed in ``config_validators.__all__``.  Wildcard
# imports make it impossible for static analysis (ruff F403, pyrefly) to
# distinguish genuinely-unused re-exports from genuinely-used ones, and they
# silently propagate any new symbol added to ``__all__`` — including future
# underscore-prefixed helpers — into this module's public surface.
#
# The explicit import below mirrors ``config_validators.__all__`` *exactly*
# (minus ``ALLOWED_USER_MODELS``, which is already imported at the top of
# this file for use by ``Config.load()``).  Re-importing it here would
# trip ruff F811 (redefinition of unused name) without changing the
# module's public surface, so it is intentionally omitted from this list.
#
# If a future change to ``config_validators.__all__`` adds a new symbol
# that callers expect to reach via ``from voice_typer.server.config import …``,
# it MUST be added to this list explicitly — that's the whole point of
# replacing the wildcard.
# ──────────────────────────────────────────────────────────────────────────
from voice_typer.server.config_validators import (  # noqa: E402,F401 — backward-compat bottom-of-file re-export
    _MAX_API_KEY_LEN,
    _MAX_STRING_LEN,
    _VALIDATOR_API_KEY,
    _VALIDATOR_API_URL,
    _VALIDATOR_CLOUD_MODEL,
    _VALIDATOR_HOTKEY,
    _VALIDATOR_LANGUAGE,
    _VALIDATOR_LLM_API_URL,
    _VALIDATOR_LLM_MODEL,
    _VALIDATOR_MICROPHONE,
    _VALIDATOR_PUSH_TO_TALK_HOTKEY,
    _VALIDATOR_REPASTE_HOTKEY,
    IPC_CONFIG_ALLOWLIST,
    FieldSpec,
    ValidatorFn,
    _bool_validator,
    _is_float_or_int_not_bool,
    _is_int_not_bool,
    _is_str,
    _make_custom_theme_validator,
    _make_enum_validator,
    _make_float_validator,
    _make_int_validator,
    _make_optional_str_validator,
    _make_str_validator,
    _make_url_validator,
    validate_config_update,
)
