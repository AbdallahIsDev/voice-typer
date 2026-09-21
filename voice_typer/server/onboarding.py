"""First-run detection + 4-step onboarding wizard controller.

Detects whether the app is running for the first time (no config.json
exists) and guides the user through initial setup:

Step 1: Welcome screen, brief explanation of what the app does + the
        app-language picker (changeable later in Settings).
Step 2: Consent, consolidated grant of every consent flag (voice
        biometric, HuggingFace model downloads, OpenAI / Groq /
        Deepgram cloud ASR, LLM polish) with an "Agree to All"
        convenience; the renderer persists each toggle immediately via
        the allowlisted set_config fields, so no backend-side
        collection is needed.
Step 3: Model selection, local-vs-cloud backend choice + per-model
        download, tiny (default), large-v3, large-v3-turbo
        (multilingual Whisper variants), plus Parakeet
Step 4: Hotkey selection, F2-F12 or custom combo. This is the LAST
        step: its Continue button finalizes the wizard (applies every
        selection + marks onboarding complete via ``apply_settings``
        through the service layer's ``onboarding_apply``).

Removed from the original 7-step flow (user decision 2026-09-14,
"shorten the first-run flow to the essentials"):
- Microphone step: the app uses the OS System Default microphone
  until the user picks a device in Settings → Microphone (C-MIC-1,
  ``config.microphone`` defaults to ``null``).
- Permissions step: keyboard monitoring is standard app behavior, not
  a consent gate; macOS Accessibility / Linux input-group setup is
  surfaced by the Dashboard / Settings KeyboardPermissionBanner
  (``onboarding_check_permissions`` IPC stays available for it).
- Done summary step: the final step's Continue applies everything and
  navigates home; a static "You are all set" recap duplicated the
  user's own choices back at them.
"""

import json
import logging
from pathlib import Path

from voice_typer.server import onboarding_status
from voice_typer.server.config import DEFAULT_HOTKEY
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE
from voice_typer.server.server_platform.macos_bundle_id import resolve_host_bundle_id

log = logging.getLogger(__name__)


class OnboardingController:
    """Controls the 6-step first-run onboarding wizard.

    completion is *not* triggered by :meth:`next_step` reaching
    the last step, it is only triggered by :meth:`apply_settings`
    (after ``config.save()`` succeeds) or :meth:`skip`. This prevents
    the wizard from marking itself complete when the user reaches the
    final "Done" screen without actually persisting their selections.
    """

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._config_dir = config_dir
        # Wizard lifecycle state (started / completed) lives in the
        self._progress_path = config_dir / ".onboarding_progress"
        self._current_step = 0
        # 4-step essentials flow (2026-09-14): Welcome → Consent →
        self._total_steps = 4

        # Collected settings
        self.selected_microphone: str | None = None
        # Default hotkey is Caps Lock on all platforms
        self.selected_hotkey: str = DEFAULT_HOTKEY
        # Canonical default: see ``model_registry.DEFAULT_MODEL_SIZE``.
        self.selected_model: str = DEFAULT_MODEL_SIZE
        # The Model step's local-vs-cloud choice. "local" downloads and
        self.selected_backend: str = "local"
        # removed the ``on_step_change`` and ``on_complete``

        # Restore in-progress wizard state from the
        self._load_progress()

    # In-progress wizard state persistence. The
    def _load_progress(self) -> None:
        """Restore in-progress wizard state from the progress marker file.

        Best-effort: if the file is absent or corrupt, leave the defaults
        set in __init__ unchanged. Type-validate every field; partial
        restore is allowed (e.g. a corrupt `selected_model` field is
        ignored but a valid `current_step` is still restored).
        """
        try:
            if not self._progress_path.exists():
                return
            raw = self._progress_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            # Progress-schema version gate: v1 files were written by
            if data.get("version") != 3:
                log.info("[ONBOARDING] ignoring stale progress schema v%s", data.get("version"))
                return
            # current_step, int in [0, total_steps)
            cs = data.get("current_step")
            if isinstance(cs, int) and 0 <= cs < self._total_steps:
                self._current_step = cs
            # selected_microphone, str | None
            sm = data.get("selected_microphone")
            if sm is None or isinstance(sm, str):
                self.selected_microphone = sm
            # selected_hotkey, str
            sh = data.get("selected_hotkey")
            if isinstance(sh, str) and sh:
                self.selected_hotkey = sh
            # selected_model, str
            smd = data.get("selected_model")
            if isinstance(smd, str) and smd:
                self.selected_model = smd
            # selected_backend, one of BACKEND_CHOICES ("local" /
            sb = data.get("selected_backend")
            if isinstance(sb, str) and sb in self.BACKEND_CHOICES:
                self.selected_backend = sb
            log.info(
                "[ONBOARDING] Resumed in-progress wizard state from %s (step=%d)",
                self._progress_path.name,
                self._current_step,
            )
        except Exception:
            # Corrupt progress file, leave defaults in place and let the
            log.debug("[ONBOARDING] progress marker unreadable; starting fresh")

    def _persist_progress(self) -> None:
        """Write the current wizard state to the progress marker file.

        Uses _secure_atomic_write for symlink-safe, 0o600-permission POSIX
        writes (matches the security posture of mark_complete).
        """
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            from voice_typer.server.config import _secure_atomic_write

            payload = json.dumps(
                {
                    # v3: 4-step layout (Welcome, Consent, Model,
                    "version": 3,
                    "current_step": self._current_step,
                    "selected_microphone": self.selected_microphone,
                    "selected_hotkey": self.selected_hotkey,
                    "selected_model": self.selected_model,
                    "selected_backend": self.selected_backend,
                }
            )
            # durability=False, onboarding progress is transient UI
            _secure_atomic_write(self._progress_path, payload, durability=False)
        except Exception:
            log.debug("[ONBOARDING] failed to persist progress marker", exc_info=True)

    def _clear_progress(self) -> None:
        """Delete the progress marker file (called on terminal transitions)."""
        try:
            self._progress_path.unlink(missing_ok=True)
        except Exception:
            log.debug("[ONBOARDING] failed to clear progress marker", exc_info=True)

    def is_first_run(self) -> bool:
        """Return True if the onboarding wizard should be shown.

        #8: Previously this returned True only when config.json didn't
        exist AND the marker didn't exist. That broke the wizard flow:
        app.py saved config.json with defaults on first run (so the
        app could keep running), at which point is_first_run() flipped
        to False and the frontend's `onboarding_is_first_run` IPC call
        returned False, the wizard never appeared.

        Now we return True whenever ``onboarding_completed`` is False
        (regardless of whether config.json exists yet). The wizard's
        ``apply_settings`` / ``skip`` methods set the flag to True and
        create the marker, so subsequent calls correctly return False.
        """
        # Fast path: the status document records completion (or the
        if onboarding_status.read_status(self._config_dir).get("completed"):
            return False
        # Otherwise, check config.onboarding_completed. Default to
        try:
            from voice_typer.server.config import Config

            cfg = Config.load()
            return not getattr(cfg, "onboarding_completed", False)
        except Exception:
            return True

    def mark_complete(self) -> None:
        """Mark onboarding as complete so it doesn't show again.

        Uses _secure_atomic_write to ensure 0o600 permissions
        on POSIX and O_NOFOLLOW symlink protection.

        re-raises on failure instead of swallowing the exception.
        Previously this method caught all exceptions via
        ``except Exception: log.exception(...)`` without re-raising, so
        if the marker write failed (disk full, read-only ``config_dir``,
        permission revoked) the caller had no way to surface the error
        to the user. Combined with :meth:`apply_settings` not setting
        ``config.onboarding_completed = True`` before ``config.save()``,
        this caused an infinite wizard-reappear loop: settings were
        saved to ``config.json`` but the marker was missing and the
        config flag stayed ``False``, so :meth:`is_first_run` returned
        ``True`` on every launch.

        The fix has two halves (this method is the second):
        1. :meth:`apply_settings` sets ``config.onboarding_completed = True``
           BEFORE ``config.save()``: making the config flag the source
           of truth. The marker file becomes a fast-path cache.
        2. This method re-raises marker-write failures so the IPC layer
           can surface the disk error. Even if the marker write fails,
           the persisted config flag keeps :meth:`is_first_run` returning
           ``False`` on the next launch (it falls through to the config
           check), breaking the infinite loop.

        The cleanup of the started/progress markers () remains
        best-effort: if the marker write succeeded, a failure to delete
        the now-stale started/progress markers is logged but not
        re-raised (the wizard is correctly marked complete and won't
        reappear).
        """
        # Critical operation, let exceptions propagate ().
        onboarding_status.write_status(self._config_dir, started=False, completed=True)
        # The progress marker is also no longer
        self._clear_progress()
        log.info("[ONBOARDING] Marked as complete")

    def mark_started(self) -> None:
        """Mark that the onboarding wizard has started rendering.

        ``startup_sequence.py``'s auto-heal logic (see lines
        143-183 of that module) fires when ``config.json`` exists on
        disk but ``onboarding_completed`` is ``False`` and the
        ``.onboarding_complete`` marker is missing. The intent is to
        fix a stale state where the marker was lost/deleted but the
        user had already completed onboarding.

        The bug: the auto-heal can't distinguish "stale state from a
        previous install" from "genuine first-run wizard that's
        currently in progress." If the user launches the app, the
        wizard starts, saves a default ``config.json``, and the user
        is mid-way through the wizard when the app restarts (crash,
        force-quit, system reboot), auto-heal fires and marks
        onboarding complete, silently dropping the user's
        in-progress selections.

        The fix: this marker is created as soon as the wizard renders
        (via the ``onboarding_start`` IPC handler, see
        :meth:`voice_typer.server.handlers.onboarding_handlers.OnboardingHandlersMixin._handle_onboarding_start`).
        ``startup_sequence.py`` should be updated to check for this
        marker and skip auto-heal when it exists::

            if onboarding.is_first_run():
                config_file = _config_dir() / "config.json"
                started_marker = _config_dir() / ".onboarding_started"
                if config_file.exists() and not started_marker.exists():
                    # auto-heal (stale state)
                    ...
                else:
                    # genuine first run, save default config
                    ...

        NOTE: ``startup_sequence.py`` is owned by another agent
        (Agent 22's scope per the review fix plan). This
        method + the ``onboarding_start`` IPC handler wiring are the
        renderer/controller-side prerequisites; the startup_sequence.py
        gate is the remaining piece.
        """
        try:
            onboarding_status.write_status(self._config_dir, started=True)
        except Exception:
            # Best-effort, status creation is non-critical. If it
            log.debug("[ONBOARDING] Failed to write started status", exc_info=True)

    def reset(self) -> None:
        """Reset onboarding state so the wizard shows again on next launch.

        deletes the ``.onboarding_status.json`` document (and any
        legacy ``.onboarding_complete`` / ``.onboarding_started``
        markers still on disk). Used by tests and by the "re-run
        onboarding" affordance in Settings. Does NOT modify
        ``config.json``: the caller is responsible for flipping
        ``config.onboarding_completed`` to ``False`` if they want
        :meth:`is_first_run` to return ``True`` on the next launch.
        """
        onboarding_status.reset_status(self._config_dir)
        # Clear the in-progress wizard state so the
        self._clear_progress()
        self._current_step = 0
        self.selected_microphone = None
        self.selected_hotkey = DEFAULT_HOTKEY
        self.selected_model = DEFAULT_MODEL_SIZE
        self.selected_backend = "local"
        log.info("[ONBOARDING] Reset (markers removed)")

    @property
    def current_step(self) -> int:
        """Current step number (0-indexed)."""
        return self._current_step

    @property
    def total_steps(self) -> int:
        """Total number of steps."""
        return self._total_steps

    @property
    def step_name(self) -> str:
        """Human-readable name of the current step."""
        # Step order is now (4-step essentials layout):
        names = [
            "Welcome",
            "Consent",
            "Model",
            "Hotkey",
        ]
        if 0 <= self._current_step < len(names):
            return names[self._current_step]
        return "Unknown"

    def next_step(self) -> int:
        """Advance to the next step. Returns the new step number.

        this method no longer calls :meth:`mark_complete` when
        the last step is reached. Completion is now triggered only by
        :meth:`apply_settings` (after ``config.save()`` succeeds) or
        :meth:`skip`. Previously, the wizard marked itself complete as
        soon as the user reached the Done step, even if
        ``apply_settings`` later failed, leaving the user with no
        working microphone/hotkey/model selection but a "completed"
        marker that suppressed the wizard on the next launch.
        """
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
        # Persist progress so a mid-wizard app
        self._persist_progress()
        return self._current_step

    def prev_step(self) -> int:
        """Go back to the previous step. Returns the new step number."""
        if self._current_step > 0:
            self._current_step -= 1
        # Persist progress so a mid-wizard app
        self._persist_progress()
        return self._current_step

    def skip(self) -> None:
        """Skip onboarding entirely.

        ``skip`` is one of the two valid completion paths
        (the other is :meth:`apply_settings`). It marks onboarding
        as complete without persisting any user selections, the
        config defaults remain in effect.

        :meth:`mark_complete` now re-raises on marker-write
        failure instead of swallowing. ``skip`` lets the exception
        propagate to the caller (the service layer's
        ``onboarding_skip`` → the IPC handler's ``except`` clause)
        so the user sees the disk error rather than the wizard
        silently failing to mark itself complete. Note: unlike
        :meth:`apply_settings`, ``skip`` has no ``config`` parameter
        and therefore cannot set ``config.onboarding_completed = True``
        as a fallback, so a marker-write failure here means the
        wizard WILL reappear on next launch. The re-raise at least
        surfaces the problem so the user knows to free disk space /
        fix permissions before retrying.
        """
        self.mark_complete()

    def get_microphones(self) -> list[dict]:
        """Get available microphones for Step 2."""
        try:
            from voice_typer.server.server_platform.microphone_list import list_microphones

            return list_microphones()
        except Exception:
            return []

    def set_microphone(self, mic_id: str | None) -> None:
        """Store the selected microphone."""
        self.selected_microphone = mic_id
        # Persist progress so a mid-wizard app
        self._persist_progress()

    HOTKEY_PRESETS = [
        # Caps Lock is the recommended default, universally present,
        DEFAULT_HOTKEY,
        # F-keys remain available as alternatives for users with
        "<f2>",
        "<f3>",
        "<f4>",
        "<f5>",
        "<f6>",
        "<f7>",
        "<f8>",
        "<f9>",
        "<f10>",
        "<f11>",
        "<f12>",
    ]

    def set_hotkey(self, hotkey: str) -> None:
        """Store the selected hotkey."""
        self.selected_hotkey = hotkey
        # Persist progress so a mid-wizard app
        self._persist_progress()

    def check_permissions(self) -> dict:
        """Probe the OS-level keyboard-monitoring permission state.

        macOS first-run users without Accessibility permission
        complete the wizard, press their hotkey, and nothing happens.
        Linux users not in the ``input`` group (and without
        the udev rule) hit the same silent failure.

        This method is the **canonical entry point** for the
        ``onboarding_check_permissions`` and
        ``onboarding_recheck_permission`` IPC handlers, it is the
        single source of truth that produces the renderer-facing
        permission payload. (A previous ``check_permissions_payload``
        free-function in ``permissions.py`` was dead code with a
        misleading docstring claiming this same role; it has been
        removed.)

        This method delegates to
        :func:`voice_typer.server.permissions.check_keyboard_permission`
        to detect the current state and returns a renderer-friendly
        dict containing:

        - ``platform``: ``"windows"`` / ``"macos"`` / ``"linux"`` /
          ``"unknown"``
        - ``state``: ``"granted"`` / ``"denied"`` / ``"unknown"``
          (matches :class:`PermissionState`)
        - ``needed``: bool. True iff the platform requires a
          permission and the user hasn't granted it yet
        - ``instructions``: ``None`` on Windows / unknown platforms;
          a dict with ``title_key`` (str), ``steps_keys`` (list[str]),
          and ``commands`` (list[str] | None) on macOS / Linux when
          permission is needed. The key strings are dotted i18n keys
          (e.g. ``"onboarding.permissionsInstructionsMacosTitle"``)
          that the renderer resolves via ``t(key)``.

        The renderer uses this in the Permissions step to show a
        platform-specific setup walkthrough.

        the ``instructions`` dict now carries i18n *keys*
        (``title_key`` / ``steps_keys``) instead of literal English
        strings. The renderer resolves them via ``t(key)`` so the
        walkthrough is fully localized. ``commands`` remains literal
        (shell commands are not translatable). On macOS the commands
        carry the ``tccutil reset Accessibility <bundle-id>`` re-grant
        command with the bundle ID resolved at RUNTIME
        (``resolve_host_bundle_id``), never hardcoded, so both the
        predecessor and Tauri builds show the command for the actually
        running host. The renderer supports both the new key-based
        shape and the legacy literal shape (``title`` / ``steps``) for
        backward compatibility with older backends and test mocks.
        """
        # Import the platform helpers from ``permissions`` (which
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import (
            LINUX_UDEV_RULE,
            PermissionState,
            check_keyboard_permission,
        )

        state = check_keyboard_permission()

        if perm_mod.is_windows():
            platform_name = "windows"
            instructions = None
            needed = False
        elif perm_mod.is_macos():
            platform_name = "macos"
            needed = state != PermissionState.GRANTED
            if needed:
                # The re-grant command embeds the host app's bundle ID,
                bundle_id = resolve_host_bundle_id()
                if bundle_id:
                    # The command string comes from the single
                    from voice_typer.server.server_platform.macos_bundle_id import (
                        tccutil_reset_command_str,
                    )

                    commands = [tccutil_reset_command_str("Accessibility", bundle_id)]
                else:
                    commands = None
                instructions = {
                    "title_key": "onboarding.permissionsInstructionsMacosTitle",
                    "steps_keys": [
                        "onboarding.permissionsInstructionsMacosStep1",
                        "onboarding.permissionsInstructionsMacosStep2",
                        "onboarding.permissionsInstructionsMacosStep3",
                    ],
                    "commands": commands,
                }
            else:
                instructions = None
        elif perm_mod.is_linux():
            platform_name = "linux"
            needed = state != PermissionState.GRANTED
            # mirror the macOS step but for the input group +
            instructions = (
                {
                    "title_key": "onboarding.permissionsInstructionsLinuxTitle",
                    "steps_keys": [
                        "onboarding.permissionsInstructionsLinuxStep1",
                        "onboarding.permissionsInstructionsLinuxStep2",
                        "onboarding.permissionsInstructionsLinuxStep3",
                    ],
                    "commands": [
                        "sudo usermod -aG input $USER",
                        "# udev rule (installed by scripts/linux/install_permissions.py):",
                        f"# {LINUX_UDEV_RULE}",
                    ],
                }
                if needed
                else None
            )
        else:
            platform_name = "unknown"
            instructions = None
            needed = False

        return {
            "platform": platform_name,
            "state": state.value,
            "needed": needed,
            "instructions": instructions,
        }

    # The Model step's local-vs-cloud choice. The app NEVER downloads
    BACKEND_CHOICES: tuple[str, ...] = ("local", "cloud")

    # each entry now carries ``vram_gb`` (estimated VRAM for
    MODEL_OPTIONS = [
        {
            "name": "tiny",
            "size": "~75MB",
            "speed": "Fastest",
            "description": "Multilingual, best for quick notes",
            "vram_gb": 0.5,
            "languages": None,
        },
        {
            "name": "large-v3",
            "size": "~3GB",
            "speed": "Slow",
            "description": "Multilingual, highest accuracy, GPU recommended",
            "vram_gb": 4.0,
            "languages": None,
        },
        {
            "name": "large-v3-turbo",
            "size": "~809MB",
            "speed": "Fast",
            "description": "Multilingual, near-large-v3 accuracy at 8x speed",
            "vram_gb": 2.0,
            "languages": None,
        },
        # NVIDIA Parakeet RNN-T model, fast, accurate, multilingual.
        {
            "name": "parakeet",
            "size": "~1.2GB",
            "speed": "Fast",
            "description": "NVIDIA Parakeet, fast & accurate, multilingual",
            "vram_gb": 2.0,
            "languages": None,
        },
    ]

    def set_model(self, model_name: str) -> None:
        """Store the selected model."""
        self.selected_model = model_name
        # Persist progress so a mid-wizard app
        self._persist_progress()

    def set_backend(self, backend: str) -> None:
        """Store the local-vs-cloud backend choice (Model step).

        ``"local"`` runs a local AI model (downloaded explicitly by the
        user); ``"cloud"`` connects a cloud transcription API. Invalid
        choices raise ``ValueError`` so the IPC layer surfaces an error
        envelope instead of silently persisting garbage.
        """
        if backend not in self.BACKEND_CHOICES:
            raise ValueError(f"unknown onboarding backend choice: {backend!r}")
        self.selected_backend = backend
        # Persist progress so a mid-wizard app
        self._persist_progress()

    @classmethod
    def get_model_catalog(cls) -> list[dict]:
        """Return the full rich-metadata model catalog.

        the static :attr:`MODEL_OPTIONS` list is intentionally
        short, it's the curated subset shown on the wizard's Model
        step. The *full* catalog (every Whisper variant, distilled
        variants, turbo, Parakeet, with VRAM / language / speed /
        accuracy / repo_id metadata) lives in
        :mod:`voice_typer.server.model_registry` and is exposed via
        :func:`get_all_models`.

        The renderer's Models page already consumes this catalog via
        the ``get_model_catalog`` IPC; the onboarding wizard can use
        the same catalog (via this method, exposed as the new
        ``onboarding_get_model_catalog`` IPC) when it wants to show
        the full set instead of the curated subset.

        Each entry is a dict with the fields defined on
        :class:`voice_typer.server.model_registry.ModelMetadata`:
        ``name``, ``download_size_mb``, ``required_vram_mb``,
        ``backend``, ``multilingual``, ``supported_languages``,
        ``description``, ``repo_id``, ``is_distilled``,
        ``speed_rating``, ``accuracy_rating``.

        Returns an empty list if the registry can't be imported
        (defensive, the registry module is side-effect-free at
        import time so this should never trigger in practice).
        """
        try:
            from voice_typer.server.model_registry import get_all_models

            return [m.to_dict() for m in get_all_models()]
        except Exception:
            log.exception("[ONBOARDING] get_model_catalog failed")
            return []

    def apply_settings(self, config) -> None:
        """Apply all collected settings to the Config object.

        this method calls :meth:`mark_complete` *after*
        ``config.save()`` succeeds, so the onboarding marker is only
        written when the user's selections have actually been
        persisted. If ``config.save()`` raises, the marker is NOT
        written and the wizard will reappear on next launch —
        giving the user another chance to complete setup instead
        of silently dropping their choices.

        ``config.onboarding_completed`` is set to ``True`` BEFORE
        ``config.save()`` so the config flag becomes the source of
        truth for first-run detection. The ``.onboarding_complete``
        marker file (written by :meth:`mark_complete` after save)
        becomes a fast-path cache. This breaks the previous infinite
        wizard-reappear loop where a marker-write failure (disk full,
        read-only ``config_dir``) left both the marker missing AND
        ``onboarding_completed=False``, so :meth:`is_first_run`
        returned ``True`` on every launch even though the user's
        settings were already persisted to ``config.json``.

        If :meth:`mark_complete` raises (: it now re-raises instead
        of swallowing), the exception propagates to the caller (the
        service layer's ``onboarding_apply`` wraps the call in a
        try/except and returns an ``{"error": ...}`` envelope so the
        IPC handler surfaces it to the user). The config flag was
        already persisted by the ``config.save()`` call above, so the
        wizard will NOT reappear on the next launch even though the
        marker file is missing, :meth:`is_first_run` falls through to
        the config check and returns ``False``.
        """
        if self.selected_microphone is not None:
            config.microphone = self.selected_microphone
        config.hotkey = self.selected_hotkey
        config.model_size = self.selected_model
        # set the onboarding-completed flag BEFORE ``config.save()``
        config.onboarding_completed = True
        # ``config.save()`` returns ``False`` on failure (errors
        save_result = config.save()
        if save_result is False:
            raise RuntimeError("failed to persist onboarding settings")
        # only mark complete once the config has been
        self.mark_complete()
        log.info(
            "[ONBOARDING] Settings applied: mic=%s | hotkey=%s | model=%s",
            self.selected_microphone,
            self.selected_hotkey,
            self.selected_model,
        )
