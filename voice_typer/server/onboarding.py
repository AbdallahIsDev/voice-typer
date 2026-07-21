"""First-run detection + 6-step onboarding wizard controller.

Detects whether the app is running for the first time (no config.json
exists) and guides the user through initial setup:

Step 1: Welcome screen — brief explanation of what the app does
Step 2: Microphone selection — dropdown of detected input devices
Step 3: Permissions — macOS Accessibility / Linux input group + udev rule
        (UX-4 / UX-27). On Windows the step auto-passes (no permission
        needed) but is still shown so the user knows hotkeys will work.
Step 4: Hotkey selection — F2-F12 or custom combo
Step 5: Model selection — tiny.en (fastest), small.en (recommended),
        medium.en (best accuracy), plus multilingual variants and
        Parakeet (UX-32 / UX-13)
Step 6: Done — app starts loading the model
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class OnboardingController:
    """Controls the 6-step first-run onboarding wizard.

    UX-31: completion is *not* triggered by :meth:`next_step` reaching
    the last step — it is only triggered by :meth:`apply_settings`
    (after ``config.save()`` succeeds) or :meth:`skip`. This prevents
    the wizard from marking itself complete when the user reaches the
    final "Done" screen without actually persisting their selections.
    """

    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir

            config_dir = _config_dir()
        self._config_dir = config_dir
        self._marker_path = config_dir / ".onboarding_complete"
        self._current_step = 0
        # UX-4 / UX-27: bumped from 5 → 6 to add a platform-conditional
        # Permissions step between Microphone (index 1) and Hotkey
        # (now index 3). On Windows the step content auto-passes; on
        # macOS it instructs the user to grant Accessibility; on Linux
        # it instructs the user to add themselves to the ``input``
        # group and install the udev rule.
        self._total_steps = 6

        # Collected settings
        self.selected_microphone: str | None = None
        # NATIVE-001: default hotkey is Caps Lock on all platforms
        self.selected_hotkey: str = "<caps_lock>"
        self.selected_model: str = "small.en"
        # NEW-DEAD-033: removed the ``on_step_change`` and ``on_complete``
        # callbacks — they were declared but never set by any caller.
        # The renderer tracks step changes via the IPC response
        # (``onboarding_next_step`` / ``onboarding_prev_step`` return
        # the new step number), and completion is tracked via
        # ``onboarding_completed`` in the config.  The callbacks were
        # dead infrastructure.

    # ── First-run detection ──────────────────────────────────────────

    def is_first_run(self) -> bool:
        """Return True if the onboarding wizard should be shown.

        #8: Previously this returned True only when config.json didn't
        exist AND the marker didn't exist. That broke the wizard flow:
        app.py saved config.json with defaults on first run (so the
        app could keep running), at which point is_first_run() flipped
        to False and the frontend's `onboarding_is_first_run` IPC call
        returned False — the wizard never appeared.

        Now we return True whenever ``onboarding_completed`` is False
        (regardless of whether config.json exists yet). The wizard's
        ``apply_settings`` / ``skip`` methods set the flag to True and
        create the marker, so subsequent calls correctly return False.
        """
        # Fast path: marker exists → onboarding was completed.
        if self._marker_path.exists():
            return False
        # Otherwise, check config.onboarding_completed. Default to
        # "first run" if the config can't be read.
        try:
            from voice_typer.server.config import Config

            cfg = Config.load()
            return not getattr(cfg, "onboarding_completed", False)
        except Exception:
            return True

    def mark_complete(self) -> None:
        """Mark onboarding as complete so it doesn't show again.

        SEC-003: Uses _secure_atomic_write to ensure 0o600 permissions
        on POSIX and O_NOFOLLOW symlink protection.
        """
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            from voice_typer.server.config import _secure_atomic_write

            _secure_atomic_write(
                self._marker_path,
                json.dumps({"completed": True, "version": 1}),
            )
            log.info("[ONBOARDING] Marked as complete")
        except Exception:
            log.exception("[ONBOARDING] Failed to mark complete")

    # ─── Step navigation ─────────────────────────────────────────────

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
        # UX-4 / UX-27: added "Permissions" between Microphone and
        # Hotkey. Step order is now:
        #   0 Welcome, 1 Microphone, 2 Permissions,
        #   3 Hotkey,     4 Model,    5 Done
        names = [
            "Welcome",
            "Microphone",
            "Permissions",
            "Hotkey",
            "Model",
            "Done",
        ]
        if 0 <= self._current_step < len(names):
            return names[self._current_step]
        return "Unknown"

    def next_step(self) -> int:
        """Advance to the next step. Returns the new step number.

        UX-31: this method no longer calls :meth:`mark_complete` when
        the last step is reached. Completion is now triggered only by
        :meth:`apply_settings` (after ``config.save()`` succeeds) or
        :meth:`skip`. Previously, the wizard marked itself complete as
        soon as the user reached the Done step, even if
        ``apply_settings`` later failed — leaving the user with no
        working microphone/hotkey/model selection but a "completed"
        marker that suppressed the wizard on the next launch.
        """
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
        return self._current_step

    def prev_step(self) -> int:
        """Go back to the previous step. Returns the new step number."""
        if self._current_step > 0:
            self._current_step -= 1
        return self._current_step

    def skip(self) -> None:
        """Skip onboarding entirely.

        UX-31: ``skip`` is one of the two valid completion paths
        (the other is :meth:`apply_settings`). It marks onboarding
        as complete without persisting any user selections — the
        config defaults remain in effect.
        """
        self.mark_complete()

    # ─── Microphone selection ────────────────────────────────────────

    def get_microphones(self) -> list[dict]:
        """Get available microphones for Step 2."""
        try:
            from voice_typer.server.server_platform import list_microphones

            return list_microphones()
        except Exception:
            return []

    def set_microphone(self, mic_id: str | None) -> None:
        """Store the selected microphone."""
        self.selected_microphone = mic_id

    # ─── Hotkey selection ────────────────────────────────────────────

    HOTKEY_PRESETS = [
        # Caps Lock is the recommended default — universally present,
        # isolated (rarely used in shortcuts), toggle suppressed by
        # the hotkey backend so it doesn't accidentally enable caps.
        "<caps_lock>",
        # F-keys remain available as alternatives for users with
        # full-size keyboards or those who prefer function keys.
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

    # ─── Permission detection (UX-4 / UX-27) ─────────────────────────

    def check_permissions(self) -> dict:
        """Probe the OS-level keyboard-monitoring permission state.

        UX-4: macOS first-run users without Accessibility permission
        complete the wizard, press their hotkey, and nothing happens.
        UX-27: Linux users not in the ``input`` group (and without
        the udev rule) hit the same silent failure.

        This method delegates to
        :func:`voice_typer.server.permissions.check_keyboard_permission`
        to detect the current state and returns a renderer-friendly
        dict containing:

        - ``platform``: ``"windows"`` / ``"macos"`` / ``"linux"`` /
          ``"unknown"``
        - ``state``: ``"granted"`` / ``"denied"`` / ``"unknown"``
          (matches :class:`PermissionState`)
        - ``needed``: bool — True iff the platform requires a
          permission and the user hasn't granted it yet
        - ``instructions``: ``None`` on Windows / unknown platforms;
          a dict with ``title``, ``steps`` (list[str]), and
          ``commands`` (list[str] | None) on macOS / Linux when
          permission is needed

        The renderer uses this in the Permissions step to show a
        platform-specific setup walkthrough.
        """
        # Import the platform helpers from ``permissions`` (which
        # re-exports them from ``platform_utils``) so tests can
        # monkeypatch a single namespace. Importing directly from
        # ``platform_utils`` here would create a second binding that
        # tests would have to remember to patch separately.
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import (
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
            instructions = (
                {
                    "title": "Accessibility Permission Required",
                    "steps": [
                        "Open System Settings → Privacy & Security → Accessibility",
                        "Add Voice Typer (and its key-listener helper) to the list",
                        "Toggle the switch ON for Voice Typer",
                    ],
                    "commands": None,
                }
                if needed
                else None
            )
        elif perm_mod.is_linux():
            platform_name = "linux"
            needed = state != PermissionState.GRANTED
            # UX-27: mirror the macOS step but for the input group +
            # udev rule. ``commands`` includes both the user-facing
            # ``sudo usermod -aG input $USER`` and the udev rule
            # snippet that scripts/linux/install_permissions.py
            # installs system-wide (so a power user can apply them
            # manually without pkexec).
            instructions = (
                {
                    "title": "Input Group + udev Rule Required",
                    "steps": [
                        "Add yourself to the 'input' group",
                        "Install the udev rule granting group-read on /dev/input/event*",
                        "Log out and back in (or reboot) for the group change to take effect",
                    ],
                    "commands": [
                        "sudo usermod -aG input $USER",
                        "# udev rule (installed by scripts/linux/install_permissions.py):",
                        '# KERNEL=="event*", SUBSYSTEM=="input", GROUP="input", MODE="0640"',
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

    # ─── Model selection ─────────────────────────────────────────────

    # UX-13: each entry now carries ``vram_gb`` (estimated VRAM for
    # GPU inference / RAM for CPU inference, in gigabytes) and
    # ``languages`` (``None`` means "all languages" / multilingual;
    # a list like ``["en"]`` means English-only). The renderer
    # renders these as badges on each model card.
    #
    # UX-32: previously this list only contained the three English-
    # only Whisper variants (tiny.en / small.en / medium.en). The
    # multilingual Whisper variants (tiny / small / medium without
    # the ``.en`` suffix) and the NVIDIA Parakeet model were
    # excluded, so non-English users had no in-wizard path to pick
    # a multilingual model. They're now first-class entries.
    MODEL_OPTIONS = [
        # ── English-only Whisper variants ───────────────────────────
        {
            "name": "tiny.en",
            "size": "~75MB",
            "speed": "Fastest",
            "description": "Best for quick notes",
            "vram_gb": 0.5,
            "languages": ["en"],
        },
        {
            "name": "small.en",
            "size": "~466MB",
            "speed": "Fast",
            "description": "Recommended for most users",
            "vram_gb": 1.0,
            "languages": ["en"],
        },
        {
            "name": "medium.en",
            "size": "~1.5GB",
            "speed": "Slow",
            "description": "Best accuracy",
            "vram_gb": 2.0,
            "languages": ["en"],
        },
        # ── Multilingual Whisper variants (UX-32) ──────────────────
        # ``languages: None`` means "all languages" — the renderer
        # should render a "Multilingual" badge for these entries.
        {
            "name": "tiny",
            "size": "~75MB",
            "speed": "Fastest",
            "description": "Multilingual — best for quick notes",
            "vram_gb": 0.5,
            "languages": None,
        },
        {
            "name": "small",
            "size": "~466MB",
            "speed": "Fast",
            "description": "Multilingual — recommended for most users",
            "vram_gb": 1.0,
            "languages": None,
        },
        {
            "name": "medium",
            "size": "~1.5GB",
            "speed": "Slow",
            "description": "Multilingual — best accuracy",
            "vram_gb": 2.0,
            "languages": None,
        },
        # ── Parakeet (UX-32) ────────────────────────────────────────
        # NVIDIA Parakeet RNN-T model — fast, accurate, multilingual.
        # Requires the parakeet_engine backend (auto-selected when
        # the user picks this model).
        {
            "name": "parakeet",
            "size": "~1.2GB",
            "speed": "Fast",
            "description": "NVIDIA Parakeet — fast & accurate, multilingual",
            "vram_gb": 2.0,
            "languages": None,
        },
    ]

    def set_model(self, model_name: str) -> None:
        """Store the selected model."""
        self.selected_model = model_name

    @classmethod
    def get_model_catalog(cls) -> list[dict]:
        """Return the full rich-metadata model catalog.

        UX-32: the static :attr:`MODEL_OPTIONS` list is intentionally
        short — it's the curated subset shown on the wizard's Model
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
        (defensive — the registry module is side-effect-free at
        import time so this should never trigger in practice).
        """
        try:
            from voice_typer.server.model_registry import get_all_models

            return [m.to_dict() for m in get_all_models()]
        except Exception:
            log.exception("[ONBOARDING] get_model_catalog failed")
            return []

    # ─── Apply collected settings ────────────────────────────────────

    def apply_settings(self, config) -> None:
        """Apply all collected settings to the Config object.

        UX-31: this method calls :meth:`mark_complete` *after*
        ``config.save()`` succeeds, so the onboarding marker is only
        written when the user's selections have actually been
        persisted. If ``config.save()`` raises, the marker is NOT
        written and the wizard will reappear on next launch —
        giving the user another chance to complete setup instead
        of silently dropping their choices.
        """
        if self.selected_microphone is not None:
            config.microphone = self.selected_microphone
        config.hotkey = self.selected_hotkey
        config.model_size = self.selected_model
        # CR-30: ``config.save()`` returns ``False`` on failure (errors
        # are caught and logged inside ``save()``) but previously
        # ``mark_complete()`` ran unconditionally — so a silent disk
        # failure would leave the onboarding marker written while the
        # user's selections were lost, and the wizard would NOT
        # reappear on next launch. Now we surface the failure as a
        # ``RuntimeError`` so the IPC handler's ``except`` clause
        # converts it into an error envelope and the marker is never
        # written. We use ``is False`` (not ``not ...``) so that
        # legacy mocks returning ``None`` are treated as success —
        # matching the contract that *only an explicit ``False``
        # indicates failure*. If ``save()`` raises (e.g. ``OSError``
        # for disk full), the exception propagates and we still never
        # reach ``mark_complete()``.
        save_result = config.save()
        if save_result is False:
            raise RuntimeError("failed to persist onboarding settings")
        # UX-31: only mark complete once the config has been
        # successfully persisted. If save() raises above, we never
        # reach this line and is_first_run() will remain True.
        self.mark_complete()
        log.info(
            "[ONBOARDING] Settings applied: mic=%s, hotkey=%s, model=%s",
            self.selected_microphone,
            self.selected_hotkey,
            self.selected_model,
        )
