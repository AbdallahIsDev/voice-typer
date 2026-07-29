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

from voice_typer.server.config import DEFAULT_HOTKEY

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
        # PVT-006: a second marker tracks that the wizard has *started*
        # rendering (as opposed to "completed"). ``startup_sequence.py``'s
        # auto-heal logic should ONLY fire when this marker is absent —
        # if the wizard has started, the user is genuinely in first-run
        # flow and auto-healing would clobber their in-progress selections.
        # See the docstring on :meth:`mark_started` for the full rationale.
        self._started_marker_path = config_dir / ".onboarding_started"
        # Progress marker — persists the in-progress
        # wizard state (current step + selected mic/hotkey/model) so that
        # closing the app mid-wizard doesn't lose all selections. The
        # file is JSON: {"version": 1, "current_step": int,
        # "selected_microphone": str|null, "selected_hotkey": str,
        # "selected_model": str}. Cleared on mark_complete / skip / reset /
        # apply_settings.
        self._progress_path = config_dir / ".onboarding_progress"
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
        self.selected_hotkey: str = DEFAULT_HOTKEY
        self.selected_model: str = "small.en"
        # NEW-DEAD-033: removed the ``on_step_change`` and ``on_complete``
        # callbacks — they were declared but never set by any caller.
        # The renderer tracks step changes via the IPC response
        # (``onboarding_next_step`` / ``onboarding_prev_step`` return
        # the new step number), and completion is tracked via
        # ``onboarding_completed`` in the config.  The callbacks were
        # dead infrastructure.

        # Restore in-progress wizard state from the
        # progress marker file. If the file exists and is well-formed,
        # the user closed the app mid-wizard and we resume from the
        # saved step + selections. If the file is absent or corrupt,
        # we start fresh (defaults already set above).
        self._load_progress()

    # ── First-run detection ──────────────────────────────────────────

    # In-progress wizard state persistence. The
    # wizard state (current step + selected mic/hotkey/model) lives in
    # instance memory and is lost when the Python process restarts
    # (app close/reopen). Without persistence, a user who closes the
    # app mid-wizard loses all selections and restarts at the Welcome
    # step on next launch — friction that may cause them to skip
    # onboarding entirely. The progress marker file is written on every
    # state mutation (next/prev/set_*) and cleared on terminal transitions
    # (mark_complete/skip/reset/apply_settings).
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
            # current_step — int in [0, total_steps)
            cs = data.get("current_step")
            if isinstance(cs, int) and 0 <= cs < self._total_steps:
                self._current_step = cs
            # selected_microphone — str | None
            sm = data.get("selected_microphone")
            if sm is None or isinstance(sm, str):
                self.selected_microphone = sm
            # selected_hotkey — str
            sh = data.get("selected_hotkey")
            if isinstance(sh, str) and sh:
                self.selected_hotkey = sh
            # selected_model — str
            smd = data.get("selected_model")
            if isinstance(smd, str) and smd:
                self.selected_model = smd
            log.info(
                "[ONBOARDING] Resumed in-progress wizard state from %s (step=%d)",
                self._progress_path.name,
                self._current_step,
            )
        except Exception:
            # Corrupt progress file — leave defaults in place and let the
            # next state mutation overwrite it.
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
                    "version": 1,
                    "current_step": self._current_step,
                    "selected_microphone": self.selected_microphone,
                    "selected_hotkey": self.selected_hotkey,
                    "selected_model": self.selected_model,
                }
            )
            # DJ-55: durability=False — onboarding progress is transient UI
            # state recreated on every step. The atomic os.replace still
            # guarantees consistency (no half-written files); only the
            # fsync-on-every-save is skipped. Saves 2 fsyncs (~10-50ms on
            # SSD) per onboarding step.
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
        #
        # Legitimate fresh-snapshot read — OnboardingController does
        # NOT hold a reference to the live ``app.config`` object, and
        # the renderer's ``onboarding_is_first_run`` IPC probe runs
        # before the app is fully wired in some early-startup paths.
        # ``Config.load()`` here is a read-only fresh snapshot from
        # disk; no mutation follows, so the config-mutation lock is not
        # required (the lock serializes read-modify-write cycles, not
        # pure reads). The disk read may observe a stale value if a
        # concurrent ``set_config`` is mid-write, but that's acceptable
        # for a first-run probe — the next launch re-reads.
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
            # PVT-006: the started marker is no longer needed once the
            # wizard completes — remove it so a future first-run (after a
            # :meth:`reset` call) starts with a clean slate.
            self._started_marker_path.unlink(missing_ok=True)
            # The progress marker is also no longer
            # needed — the wizard is done.
            self._clear_progress()
            log.info("[ONBOARDING] Marked as complete")
        except Exception:
            log.exception("[ONBOARDING] Failed to mark complete")

    def mark_started(self) -> None:
        """Mark that the onboarding wizard has started rendering.

        PVT-006: ``startup_sequence.py``'s auto-heal logic (see lines
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
        onboarding complete — silently dropping the user's
        in-progress selections.

        The fix: this marker is created as soon as the wizard renders
        (via the ``onboarding_start`` IPC handler — see
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
                    # genuine first run — save default config
                    ...

        NOTE: ``startup_sequence.py`` is owned by another agent
        (Agent 22's scope per the review fix plan). This
        method + the ``onboarding_start`` IPC handler wiring are the
        renderer/controller-side prerequisites; the startup_sequence.py
        gate is the remaining piece.
        """
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            from voice_typer.server.config import _secure_atomic_write

            _secure_atomic_write(
                self._started_marker_path,
                json.dumps({"started": True, "version": 1}),
            )
        except Exception:
            # Best-effort — marker creation is non-critical. If it
            # fails, the worst case is the pre-fix auto-heal behavior
            # (which is the current production behavior anyway).
            log.debug("[ONBOARDING] Failed to write started marker", exc_info=True)

    def reset(self) -> None:
        """Reset onboarding state so the wizard shows again on next launch.

        PVT-006: deletes both the ``.onboarding_complete`` and
        ``.onboarding_started`` markers. Used by tests and by a future
        "re-run onboarding" affordance in Settings. Does NOT modify
        ``config.json`` — the caller is responsible for flipping
        ``config.onboarding_completed`` to ``False`` if they want
        :meth:`is_first_run` to return ``True`` on the next launch.
        """
        for path in (self._marker_path, self._started_marker_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                log.debug("[ONBOARDING] Failed to remove marker %s", path, exc_info=True)
        # Clear the in-progress wizard state so the
        # next launch starts at the Welcome step with default selections.
        self._clear_progress()
        self._current_step = 0
        self.selected_microphone = None
        self.selected_hotkey = DEFAULT_HOTKEY
        self.selected_model = "small.en"
        log.info("[ONBOARDING] Reset (markers removed)")

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
        # Persist progress so a mid-wizard app
        # restart resumes here.
        self._persist_progress()
        return self._current_step

    def prev_step(self) -> int:
        """Go back to the previous step. Returns the new step number."""
        if self._current_step > 0:
            self._current_step -= 1
        # Persist progress so a mid-wizard app
        # restart resumes here.
        self._persist_progress()
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
        # Persist progress so a mid-wizard app
        # restart restores this selection.
        self._persist_progress()

    # ─── Hotkey selection ────────────────────────────────────────────

    HOTKEY_PRESETS = [
        # Caps Lock is the recommended default — universally present,
        # isolated (rarely used in shortcuts), toggle suppressed by
        # the hotkey backend so it doesn't accidentally enable caps.
        DEFAULT_HOTKEY,
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
        # Persist progress so a mid-wizard app
        # restart restores this selection.
        self._persist_progress()

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
          a dict with ``title_key`` (str), ``steps_keys`` (list[str]),
          and ``commands`` (list[str] | None) on macOS / Linux when
          permission is needed. The key strings are dotted i18n keys
          (e.g. ``"onboarding.permissionsInstructionsMacosTitle"``)
          that the renderer resolves via ``t(key)``.

        The renderer uses this in the Permissions step to show a
        platform-specific setup walkthrough.

        PVT-052: the ``instructions`` dict now carries i18n *keys*
        (``title_key`` / ``steps_keys``) instead of literal English
        strings. The renderer resolves them via ``t(key)`` so the
        walkthrough is fully localized. ``commands`` remains literal
        (shell commands are not translatable). The renderer supports
        both the new key-based shape and the legacy literal shape
        (``title`` / ``steps``) for backward compatibility with older
        backends and test mocks.
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
                    "title_key": "onboarding.permissionsInstructionsMacosTitle",
                    "steps_keys": [
                        "onboarding.permissionsInstructionsMacosStep1",
                        "onboarding.permissionsInstructionsMacosStep2",
                        "onboarding.permissionsInstructionsMacosStep3",
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
                    "title_key": "onboarding.permissionsInstructionsLinuxTitle",
                    "steps_keys": [
                        "onboarding.permissionsInstructionsLinuxStep1",
                        "onboarding.permissionsInstructionsLinuxStep2",
                        "onboarding.permissionsInstructionsLinuxStep3",
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
        # Persist progress so a mid-wizard app
        # restart restores this selection.
        self._persist_progress()

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
