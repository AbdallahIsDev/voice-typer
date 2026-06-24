"""Configuration management with platform-aware storage."""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("voice_typer.server.config")


ALLOWED_USER_MODELS = {"tiny.en", "small.en", "medium.en", "qwen", "parakeet"}


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
    import tempfile
    tmp_fd = None
    tmp_path = None
    try:
        # Create temp file in same directory for atomic rename
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        if sys.platform != "win32":
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
                try:
                    os.close(fd)
                except OSError:
                    pass
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
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise



def _legacy_config_dir() -> Path | None:
    """Get the legacy platform-specific config directory, if different from new one.

    NEW-DEAD-017: This function is never called in production code.
    Kept for backward compatibility with external scripts that may
    import it.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    legacy = Path(base) / "voice-typer"
    return legacy if legacy != _config_dir() else None


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
    """
    custom = os.environ.get("VOICE_TYPER_CONFIG_DIR")
    if custom:
        return Path(custom)

    # NEW-XPLAT-001: check for legacy ~/.voice-typer first (migration
    # path — existing users keep their data where it is).
    legacy = Path.home() / ".voice-typer"
    if legacy.exists():
        return legacy

    # Platform-specific paths for new installations.
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "voice-typer"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "voice-typer"
    else:
        # Linux / FreeBSD: honor XDG_DATA_HOME.
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "voice-typer"
        return Path.home() / ".local" / "share" / "voice-typer"

    # Fallback for any platform where the above checks didn't return.
    return legacy


def _migrate_from_legacy():
    """One-time migration from old platform-specific location (e.g. %APPDATA%)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
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


_CURRENT_SCHEMA_VERSION = 1

# NEW-DEAD-018: _MIGRATIONS is currently empty. Infrastructure kept
# for future schema version migrations.
_MIGRATIONS = {}


@dataclass
class Config:
    """Application configuration."""

    schema_version: int = _CURRENT_SCHEMA_VERSION
    # NEW-CQ-016: warnings from the last load() call. Populated by
    # _validate_non_numeric_fields when a field had an invalid type
    # and was reset to default. The IPC layer can surface these to
    # the renderer so the user knows their config was corrected.
    last_load_warnings: list = None  # type: ignore[assignment]

    # Hotkey
    hotkey: str = "<f2>"

    # Recording
    sample_rate: int = 16000
    microphone: Optional[str] = None  # None = system default

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

    # ASR backend selection
    asr_backend: str = "whisper"  # "whisper", "qwen", or "parakeet"
    qwen_model_path: Optional[str] = None  # local path to Qwen3-ASR weights
    parakeet_model_path: Optional[str] = None  # local override for Parakeet weights (None = HF cache)

    # Text cleanup
    text_cleanup_enabled: bool = True  # Set False for raw (uncorrected) output

    # External corrections file
    corrections_path: Optional[str] = None

    # Logging
    log_transcriptions: bool = False

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
    # OFF — opt-in to avoid surprising users with sound.
    sound_feedback_enabled: bool = False

    # Crash recovery
    crash_recovery_enabled: bool = True

    # T020: AudioQualityAnalyzer removed — dead code archived to archive/
    # The following fields are kept for backward compatibility with existing
    # config files but have no behavioral effect.
    audio_quality_warnings: bool = True

    # Waveform visualization bubble
    waveform_bubble: bool = False

    # Bubble screen position (top / bottom)
    bubble_position: str = "top"

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

    # UX-036: Accessibility
    high_contrast: bool = False
    text_size: int = 14

    # Wayland hotkey fallback warning
    wayland_warned: bool = False

    # Fast startup: keep torch + transformers + model weights in the OS
    # file cache by running a low-priority prewarm on login/idle.  Cuts
    # cold-boot startup from ~45s to a few seconds.  Disable on low-RAM
    # machines where pinning ~6 GB of file cache is undesirable.
    fast_startup: bool = True

    # Silent mic disconnection (H12)
    silence_warning_seconds: float = 20.0
    silence_auto_stop_seconds: float = 120.0
    max_recording_seconds_gpu: int = 1200
    max_recording_seconds_cpu: int = 600
    max_recording_seconds: int = 0  # 0 = use device-specific default (gpu/cpu)

    # ─── Volume ducking (v1.1.0) ────────────────────────────────────
    # Reduces system volume during dictation to prevent speaker output
    # from bleeding into the microphone.
    volume_duck_enabled: bool = True
    volume_duck_level: float = 0.25  # 0.0–1.0 perceptual-linear
    volume_duck_per_session: bool = False  # Windows only — duck other apps, keep alerts
    volume_duck_fade_ms: int = 150  # 0–1000, 0 = instant
    # Smart duck: skip the volume change when no application is currently
    # playing audio through the speakers.  Avoids a pointless speaker-icon
    # animation during silent dictation.  Default ON.  Set to False to
    # always duck (the pre-smart-duck behaviour).  Cross-platform:
    # Windows uses IAudioMeterInformation.GetPeakValue(); macOS uses
    # osascript + known audio-app heuristic; Linux uses pactl/wpctl or
    # /proc/asound.  See VolumeDucker.duck() and
    # VolumeBackend.is_speaker_active().
    volume_duck_smart: bool = True
    # Smart-duck background monitor polling interval (milliseconds).
    # When smart-duck skips the initial duck (no audio playing), a
    # background thread polls is_speaker_active() at this interval and
    # retroactively ducks if audio starts mid-dictation.  500ms is the
    # default — fast enough to catch audio within half a second, slow
    # enough to not spam the backend (macOS osascript is 200-500ms per
    # call).  Range 50–5000ms.  See VolumeDucker._smart_duck_monitor_loop.
    volume_duck_smart_poll_interval_ms: int = 500

    # ─── Noise filtering (v1.1.0) ───────────────────────────────────
    # Cleans the microphone signal: removes fan noise, keyboard clicks,
    # HVAC rumble, and residual speaker bleed.
    noise_filter_enabled: bool = True
    noise_filter_highpass: bool = True
    noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
    noise_filter_gate: bool = True
    noise_filter_gate_threshold: float = 0.015  # 0.0–0.1, ~-45dBFS
    noise_filter_rnnoise: bool = False  # opt-in (CPU cost), neural denoise
    noise_filter_post_capture: bool = True  # noisereduce on stop()

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
            if sys.platform != "win32":
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
        """Load config from disk, or return defaults."""
        config_file = _config_dir() / "config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    data = json.load(f)
                data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}

                # M3: Schema versioning and migration
                loaded_version = data.get("schema_version", 0)
                for version in range(loaded_version + 1, _CURRENT_SCHEMA_VERSION + 1):
                    migrator = _MIGRATIONS.get(version)
                    if migrator is not None:
                        data = migrator(data)
                data["schema_version"] = _CURRENT_SCHEMA_VERSION

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
                        "(%.1f); clamping step to chunk/2", step, chunk,
                    )
                    data["streaming_step_seconds"] = chunk / 2.0
                if left_overlap >= chunk:
                    log.warning(
                        "[CONFIG] streaming_left_overlap_seconds (%.1f) >= streaming_chunk_seconds "
                        "(%.1f); clamping overlap to chunk/3", left_overlap, chunk,
                    )
                    data["streaming_left_overlap_seconds"] = chunk / 3.0
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

                # H1: Validate non-numeric fields before construction
                data = cls._validate_non_numeric_fields(data)
                # NEW-CQ-016: extract load warnings before construction
                # (cls(**data) would fail on the _load_warnings key)
                load_warnings = data.pop("_load_warnings", [])

                instance = cls(**data)
                instance.last_load_warnings = load_warnings
                return instance
            except json.JSONDecodeError as e:
                log.error("[CONFIG] Config file corrupted: %s. Using defaults.", e)
                return cls()
            except Exception as e:
                log.error("[CONFIG] Failed to load config: %s. Using defaults.", e)
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
            "autostart", "paste_on_stop", "unsafe_paste_on_unknown_focus", "show_notifications",
            "text_cleanup_enabled",
            "streaming_transcription", "log_transcriptions",
            "condition_on_previous_text",
            "esc_cancel_enabled", "auto_punctuation", "llm_polish",
            "llm_polish_consent",
            # NEW-PRIV-005/006/009: privacy consent flags are bools.
            "huggingface_consent",
            "cloud_openai_consent", "cloud_groq_consent", "cloud_deepgram_consent",
            "voice_biometric_consent",
            # NEW-UX-029: sound feedback toggle.
            "sound_feedback_enabled",
            "crash_recovery_enabled", "audio_quality_warnings",
            "templates_enabled", "vocabulary_enabled",
            "waveform_bubble", "onboarding_completed", "onboarding_failed", "wayland_warned",
            "fast_startup",
            "bubble_draggable", "bubble_show_on_startup",
            "volume_duck_enabled", "volume_duck_per_session",
            "volume_duck_smart",
            # STARTUP-6: volume_duck_smart_poll_interval_ms is an int (50-5000),
            # NOT a bool — it was misclassified here, causing the bool validator
            # to flag the default value 500 as invalid and log a spurious
            # "resetting to default 500" warning on every startup. It already
            # has its own int validator in IPC_CONFIG_ALLOWLIST.
            "noise_filter_enabled", "noise_filter_highpass",
            "noise_filter_gate", "noise_filter_rnnoise",
            "noise_filter_post_capture",
        }
        str_fields = {
            "hotkey", "language", "device", "asr_backend",
            "recording_mode", "push_to_talk_hotkey",
            "cloud_api_key", "cloud_api_url", "cloud_model",
            "openai_api_key", "groq_api_key", "deepgram_api_key",
            "llm_api_key", "llm_api_url", "llm_model", "llm_preset",
            "repaste_hotkey",
            "tray_left_click_action",
            "parakeet_model_path",
            "bubble_position", "bubble_behavior",
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
                msg = (
                    f"Config field '{field_name}' had invalid value "
                    f"{val!r}, resetting to default {default_val!r}"
                )
                log.warning("[CONFIG] %s", msg)
                warnings.append(msg)
                data[field_name] = default_val

        optional_str_fields = {"parakeet_model_path"}

        for field_name in str_fields:
            if field_name not in data:
                continue
            val = data[field_name]
            if isinstance(val, str):
                continue
            if val is None and field_name in optional_str_fields:
                continue
            default_val = getattr(defaults, field_name)
            msg = (
                f"Config field '{field_name}' had non-string value "
                f"{val!r}, resetting to default {default_val!r}"
            )
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
# SEC-002: IPC `set_config` allowlist
#
# The IPC `set_config` command previously used `hasattr(config, k) +
# setattr(config, k, v)`, which accepted *any* Config field.  That let a
# loopback IPC caller swap `llm_api_url`, `cloud_api_url`,
# `openai_api_key`, etc., enabling data exfiltration and unauthorized
# use of paid API keys.
#
# `IPC_CONFIG_ALLOWLIST` is the explicit, reviewed list of fields the
# Electron renderer is permitted to mutate via `set_config`, together
# with per-field validators.  Anything not in this map is silently
# dropped (preserving the existing "unknown field" contract from
# `test_ignores_unknown_fields_without_crashing`).
#
# Fields deliberately excluded:
#   - `schema_version`           — managed by Config.load() migration path
#   - `wayland_warned`           — internal UX state, not user-tunable
#   - `onboarding_completed`     — set via the dedicated `complete_onboarding`
#                                   IPC command, not `set_config`
#   - `qwen_model_path`          — trusted-path, set by model download flow
#   - `parakeet_model_path`      — trusted-path, set by model download flow
#   - `corrections_path`         — trusted-path, set by file picker IPC
#
# When adding a field here, also add a test in
# `tests/test_server.py::TestDispatchSetConfigAllowlist`.
# ──────────────────────────────────────────────────────────────────────────


# Type helpers ──────────────────────────────────────────────────────────────
#
# A field validator returns ``None`` on success or a human-readable error
# string describing why the value is rejected.  ``expected_type`` is the
# concrete Python type the value must be an instance of — note that for
# bool fields we set ``expected_type=bool`` and rely on the fact that
# ``isinstance(True, int)`` is True but ``isinstance(1, bool)`` is False,
# so the int-vs-bool ambiguity is resolved by checking bool first in the
# dispatcher (see ``_validate_config_update``).

ValidatorFn = Callable[[object], Optional[str]]
FieldSpec = Tuple[type, ValidatorFn]


def _is_str(v: object) -> bool:
    return isinstance(v, str)


def _is_int_not_bool(v: object) -> bool:
    # bool is a subclass of int in Python; reject it explicitly so that
    # ``max_recording_seconds=True`` doesn't silently become 1.
    return isinstance(v, int) and not isinstance(v, bool)


def _is_float_or_int_not_bool(v: object) -> bool:
    # Accept ints on the numeric tower (they're valid floats), but still
    # reject bool.  This matches the dataclass field type ``float`` while
    # being friendly to JSON, which has no int/float distinction.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Sane upper bound for any single string field.  API keys, URLs, hotkey
# strings and language codes are all well under this; anything bigger is
# either a bug or an attack.
_MAX_STRING_LEN = 8192

# API keys can be longer than typical strings (some Bearer tokens exceed
# 4 KB), so they get their own cap.
_MAX_API_KEY_LEN = 16384


def _make_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> Optional[str]:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if len(v) > max_len:
            return f"exceeds maximum length {max_len}"
        return None
    return _validate


def _make_optional_str_validator(max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    def _validate(v: object) -> Optional[str]:
        if v is None:
            return None
        if not _is_str(v):
            return f"must be a string or null, got {type(v).__name__}"
        if len(v) > max_len:
            return f"exceeds maximum length {max_len}"
        return None
    return _validate


def _bool_validator(v: object) -> Optional[str]:
    if not isinstance(v, bool):
        return f"must be a boolean, got {type(v).__name__}"
    return None


def _make_int_validator(*, lo: int, hi: int) -> ValidatorFn:
    def _validate(v: object) -> Optional[str]:
        if not _is_int_not_bool(v):
            return f"must be an integer, got {type(v).__name__}"
        if v < lo or v > hi:
            return f"must be in [{lo}, {hi}]"
        return None
    return _validate


def _make_float_validator(*, lo: float, hi: float) -> ValidatorFn:
    def _validate(v: object) -> Optional[str]:
        if not _is_float_or_int_not_bool(v):
            return f"must be a number, got {type(v).__name__}"
        if v < lo or v > hi:
            return f"must be in [{lo}, {hi}]"
        return None
    return _validate


def _make_enum_validator(allowed: set) -> ValidatorFn:
    def _validate(v: object) -> Optional[str]:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if v not in allowed:
            return f"must be one of {sorted(allowed)}"
        return None
    return _validate


def _make_url_validator(*, allow_empty: bool = False, max_len: int = _MAX_STRING_LEN) -> ValidatorFn:
    """Validate an HTTP(S) URL.

    Rejects non-string values, oversized values, and any URL whose scheme
    is not ``http`` or ``https``.  Empty string is accepted iff ``allow_empty``
    (used for fields where empty means "feature disabled").
    """
    def _validate(v: object) -> Optional[str]:
        if not _is_str(v):
            return f"must be a string, got {type(v).__name__}"
        if len(v) > max_len:
            return f"exceeds maximum length {max_len}"
        if v == "":
            if allow_empty:
                return None
            return "must not be empty"
        try:
            parsed = urlparse(v)
        except (ValueError, TypeError) as e:
            return f"is not a valid URL: {e}"
        if parsed.scheme not in ("http", "https"):
            return f"must use http or https scheme (got {parsed.scheme!r})"
        if not parsed.netloc:
            return "must include a network location (host)"
        return None
    return _validate


# Validator combinations ────────────────────────────────────────────────────

_VALIDATOR_HOTKEY = _make_str_validator(max_len=256)
_VALIDATOR_LANGUAGE = _make_str_validator(max_len=16)
_VALIDATOR_API_KEY = _make_str_validator(max_len=_MAX_API_KEY_LEN)
_VALIDATOR_API_URL = _make_url_validator(allow_empty=True)
_VALIDATOR_LLM_API_URL = _make_url_validator(allow_empty=False)
_VALIDATOR_LLM_MODEL = _make_str_validator(max_len=256)
_VALIDATOR_REPASTE_HOTKEY = _make_str_validator(max_len=256)
_VALIDATOR_MICROPHONE = _make_optional_str_validator(max_len=512)
_VALIDATOR_PUSH_TO_TALK_HOTKEY = _make_str_validator(max_len=256)
_VALIDATOR_CLOUD_MODEL = _make_str_validator(max_len=256)


IPC_CONFIG_ALLOWLIST: dict = {
    # ── Hotkey ────────────────────────────────────────────────────────
    "hotkey":                 (str, _VALIDATOR_HOTKEY),
    "push_to_talk_hotkey":    (str, _VALIDATOR_PUSH_TO_TALK_HOTKEY),
    "repaste_hotkey":         (str, _VALIDATOR_REPASTE_HOTKEY),

    # ── Recording ─────────────────────────────────────────────────────
    "microphone":             ((str, type(None)), _VALIDATOR_MICROPHONE),

    # ── Transcription ─────────────────────────────────────────────────
    "model_size":             (str, _make_enum_validator(ALLOWED_USER_MODELS)),
    "language":               (str, _VALIDATOR_LANGUAGE),
    "device":                 (str, _make_enum_validator({"cuda", "cpu"})),
    "beam_size":              (int, _make_int_validator(lo=1, hi=10)),
    "best_of":                (int, _make_int_validator(lo=1, hi=10)),
    "condition_on_previous_text": (bool, _bool_validator),

    # ── Streaming (hidden) ────────────────────────────────────────────
    "streaming_transcription":    (bool, _bool_validator),
    "streaming_chunk_seconds":    (float, _make_float_validator(lo=0.1, hi=120.0)),
    "streaming_step_seconds":     (float, _make_float_validator(lo=0.1, hi=60.0)),
    "streaming_left_overlap_seconds": (float, _make_float_validator(lo=0.0, hi=60.0)),
    "streaming_right_guard_seconds":  (float, _make_float_validator(lo=0.0, hi=30.0)),
    "streaming_min_first_chunk_seconds": (float, _make_float_validator(lo=0.1, hi=60.0)),
    "streaming_silence_threshold":   (float, _make_float_validator(lo=0.0, hi=1.0)),

    # ── Behavior ──────────────────────────────────────────────────────
    "autostart":             (bool, _bool_validator),
    "paste_on_stop":         (bool, _bool_validator),
    "unsafe_paste_on_unknown_focus": (bool, _bool_validator),
    "show_notifications":    (bool, _bool_validator),

    # ── ASR backend selection ─────────────────────────────────────────
    "asr_backend":           (str, _make_enum_validator({"whisper", "qwen", "parakeet"})),

    # ── Text cleanup ──────────────────────────────────────────────────
    "text_cleanup_enabled":  (bool, _bool_validator),
    "auto_punctuation":      (bool, _bool_validator),

    # ── Logging ───────────────────────────────────────────────────────
    "log_transcriptions":    (bool, _bool_validator),

    # ── P1 Features ───────────────────────────────────────────────────
    "recording_mode":        (str, _make_enum_validator({"toggle", "push_to_talk"})),
    "esc_cancel_enabled":    (bool, _bool_validator),

    # ── P2 Features ───────────────────────────────────────────────────
    "templates_enabled":     (bool, _bool_validator),
    "vocabulary_enabled":    (bool, _bool_validator),

    # Cloud ASR — secrets and URLs are sensitive but the renderer actively
    # manages them, so they are in the allowlist with strict validators.
    "cloud_api_key":         (str, _VALIDATOR_API_KEY),
    "cloud_api_url":         (str, _VALIDATOR_API_URL),
    "cloud_model":           (str, _VALIDATOR_CLOUD_MODEL),
    "openai_api_key":        (str, _VALIDATOR_API_KEY),
    "groq_api_key":          (str, _VALIDATOR_API_KEY),
    "deepgram_api_key":      (str, _VALIDATOR_API_KEY),

    # LLM polish — same rationale as cloud ASR.
    "llm_polish":            (bool, _bool_validator),
    "llm_api_key":           (str, _VALIDATOR_API_KEY),
    "llm_api_url":           (str, _VALIDATOR_LLM_API_URL),
    "llm_model":             (str, _VALIDATOR_LLM_MODEL),
    "llm_preset":            (str, _make_enum_validator({"professional", "casual", "email", "code"})),
    # PRIVACY-001: consent flag is user-tunable (the consent dialog
    # itself sets this), but it's still subject to type validation.
    "llm_polish_consent":    (bool, _bool_validator),
    # NEW-PRIV-005/006/009: privacy consent flags.  All user-tunable
    # via the consent dialogs in the renderer; all subject to type
    # validation so a malicious IPC client can't set them to non-bool
    # values to bypass the consent UI.
    "huggingface_consent":       (bool, _bool_validator),
    "cloud_openai_consent":      (bool, _bool_validator),
    "cloud_groq_consent":        (bool, _bool_validator),
    "cloud_deepgram_consent":    (bool, _bool_validator),
    "voice_biometric_consent":   (bool, _bool_validator),
    # NEW-UX-029: sound feedback toggle.
    "sound_feedback_enabled":    (bool, _bool_validator),

    # ── Crash recovery ────────────────────────────────────────────────
    "crash_recovery_enabled": (bool, _bool_validator),

    # ── Audio quality ─────────────────────────────────────────────────
    "audio_quality_warnings":     (bool, _bool_validator),

    # ── Waveform bubble ───────────────────────────────────────────────
    "waveform_bubble":       (bool, _bool_validator),
    "bubble_position":       (str, _make_enum_validator({"top", "bottom"})),
    "bubble_behavior":       (str, _make_enum_validator({"show_on_record", "always_visible"})),
    "bubble_draggable":      (bool, _bool_validator),
    "bubble_show_on_startup": (bool, _bool_validator),

    # ── History database ──────────────────────────────────────────────
    "history_retention_days":  (int, _make_int_validator(lo=0, hi=36500)),
    "history_retention_count": (int, _make_int_validator(lo=0, hi=1_000_000)),
    "history_max_entries":     (int, _make_int_validator(lo=10, hi=1_000_000)),

    # ── P3 Features / UX ──────────────────────────────────────────────
    "tray_left_click_action": (str, _make_enum_validator({"open_app", "toggle_dictation"})),
    "theme_mode":            (str, _make_enum_validator({"system", "light", "dark"})),
    "high_contrast":         (bool, _bool_validator),
    "text_size":             (int, _make_int_validator(lo=8, hi=72)),

    # ── Fast startup ──────────────────────────────────────────────────
    "fast_startup":          (bool, _bool_validator),

    # ── Silent mic disconnection (H12) ────────────────────────────────
    "silence_warning_seconds":    (float, _make_float_validator(lo=0.0, hi=600.0)),
    "silence_auto_stop_seconds":  (float, _make_float_validator(lo=0.0, hi=3600.0)),
    "max_recording_seconds_gpu":  (int, _make_int_validator(lo=0, hi=86400)),
    "max_recording_seconds_cpu":  (int, _make_int_validator(lo=0, hi=86400)),
    "max_recording_seconds":      (int, _make_int_validator(lo=0, hi=86400)),

    # ── Volume ducking (v1.1.0) ───────────────────────────────────────
    "volume_duck_enabled":          (bool, _bool_validator),
    "volume_duck_level":            (float, _make_float_validator(lo=0.0, hi=1.0)),
    "volume_duck_per_session":      (bool, _bool_validator),
    "volume_duck_fade_ms":          (int, _make_int_validator(lo=0, hi=1000)),
    "volume_duck_smart":            (bool, _bool_validator),
    "volume_duck_smart_poll_interval_ms": (int, _make_int_validator(lo=50, hi=5000)),

    # ── Noise filtering (v1.1.0) ──────────────────────────────────────
    "noise_filter_enabled":             (bool, _bool_validator),
    "noise_filter_highpass":            (bool, _bool_validator),
    "noise_filter_highpass_cutoff_hz":  (float, _make_float_validator(lo=20.0, hi=500.0)),
    "noise_filter_gate":                (bool, _bool_validator),
    "noise_filter_gate_threshold":      (float, _make_float_validator(lo=0.0, hi=0.1)),
    "noise_filter_rnnoise":             (bool, _bool_validator),
    "noise_filter_post_capture":        (bool, _bool_validator),
}


def validate_config_update(data: dict) -> Tuple[dict, list]:
    """Validate a caller-supplied config update payload.

    Parameters
    ----------
    data : dict
        The raw ``data`` field from an IPC ``set_config`` command.  Must
        be a dict — callers should check before invoking.

    Returns
    -------
    (validated, errors) : (dict, list[str])
        ``validated`` is the subset of ``data`` whose keys are in
        :data:`IPC_CONFIG_ALLOWLIST` and whose values passed their
        validators.  ``errors`` is a list of human-readable error
        strings for the first invalid field encountered (the function
        stops at the first error to keep messages actionable; the
        dispatcher treats the entire payload atomically — see
        ``ipc_server.set_config``).

        Unknown keys are silently dropped (no error, no log entry beyond
        a debug-level message) to preserve the existing
        "test_ignores_unknown_fields_without_crashing" contract.

    Notes
    -----
    The function is pure: it does not touch the Config object or perform
    any I/O.  This makes it trivially testable.
    """
    validated: dict = {}
    errors: list = []
    for k, v in data.items():
        spec = IPC_CONFIG_ALLOWLIST.get(k)
        if spec is None:
            # Unknown key — silently drop.  Debug-level so devs can
            # diagnose "why isn't my setting saving" without leaking
            # field-name existence to attackers (debug logs aren't
            # visible to end users by default).
            log.debug("[CONFIG] set_config dropped unknown key %r", k)
            continue
        expected_type, validator = spec
        # Type-check first (cheap), then run the field-specific validator
        # (which may do range/enum checks).  The expected_type is a
        # redundant guard against the validator being too lenient —
        # defense in depth.
        #
        # expected_type may be a single type (``str``, ``int``, ``bool``,
        # ``float``) or a tuple of types (e.g. ``(str, type(None))`` for
        # Optional[str] fields like ``microphone``).
        type_ok: bool
        if isinstance(expected_type, tuple):
            type_ok = isinstance(v, expected_type)
        elif expected_type is bool:
            type_ok = isinstance(v, bool)
        elif expected_type is int:
            type_ok = isinstance(v, int) and not isinstance(v, bool)
        elif expected_type is float:
            type_ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        elif expected_type is str:
            type_ok = isinstance(v, str)
        else:
            # Should never happen for the current allowlist.
            type_ok = isinstance(v, expected_type)
        if not type_ok:
            type_name = (
                " or ".join(t.__name__ for t in expected_type)
                if isinstance(expected_type, tuple)
                else expected_type.__name__
            )
            errors.append(
                f"field {k!r} must be {type_name}, got {type(v).__name__}"
            )
            break
        err = validator(v)
        if err is not None:
            errors.append(f"field {k!r} {err}")
            break
        validated[k] = v
    return validated, errors
