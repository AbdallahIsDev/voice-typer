"""First-run detection + 5-step onboarding wizard controller.

Detects whether the app is running for the first time (no config.json
exists) and guides the user through initial setup:

Step 1: Welcome screen — brief explanation of what the app does
Step 2: Microphone selection — dropdown of detected input devices
Step 3: Hotkey selection — F2-F12 or custom combo
Step 4: Model selection — tiny.en (fastest), small.en (recommended), medium.en (best quality)
Step 5: Done — app starts loading the model
"""

import json
import logging
from pathlib import Path
from typing import Optional, Callable

log = logging.getLogger(__name__)


class OnboardingController:
    """Controls the 5-step first-run onboarding wizard."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            from voice_typer.server.config import _config_dir
            config_dir = _config_dir()
        self._config_dir = config_dir
        self._marker_path = config_dir / ".onboarding_complete"
        self._current_step = 0
        self._total_steps = 5

        # Collected settings
        self.selected_microphone: Optional[str] = None
        self.selected_hotkey: str = "<f2>"
        self.selected_model: str = "small.en"

        # Callbacks
        self.on_step_change: Optional[Callable[[int], None]] = None
        self.on_complete: Optional[Callable[[], None]] = None

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
        """Mark onboarding as complete so it doesn't show again."""
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            self._marker_path.write_text(
                json.dumps({"completed": True, "version": 1}),
                encoding="utf-8",
            )
            log.info("[ONBOARDING] Marked as complete")
        except Exception as exc:
            log.error("[ONBOARDING] Failed to mark complete: %s", exc)

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
        names = ["Welcome", "Microphone", "Hotkey", "Model", "Done"]
        if 0 <= self._current_step < len(names):
            return names[self._current_step]
        return "Unknown"

    def next_step(self) -> int:
        """Advance to the next step. Returns the new step number."""
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            if self.on_step_change:
                self.on_step_change(self._current_step)
            if self._current_step == self._total_steps - 1:
                # Last step reached
                self.mark_complete()
                if self.on_complete:
                    self.on_complete()
        return self._current_step

    def prev_step(self) -> int:
        """Go back to the previous step. Returns the new step number."""
        if self._current_step > 0:
            self._current_step -= 1
            if self.on_step_change:
                self.on_step_change(self._current_step)
        return self._current_step

    def skip(self) -> None:
        """Skip onboarding entirely."""
        self.mark_complete()
        if self.on_complete:
            self.on_complete()

    # ─── Microphone selection ────────────────────────────────────────

    def get_microphones(self) -> list[dict]:
        """Get available microphones for Step 2."""
        try:
            from voice_typer.server.platform import list_microphones
            return list_microphones()
        except Exception:
            return []

    def set_microphone(self, mic_id: Optional[str]) -> None:
        """Store the selected microphone."""
        self.selected_microphone = mic_id

    # ─── Hotkey selection ────────────────────────────────────────────

    HOTKEY_PRESETS = [
        "<f2>", "<f3>", "<f4>", "<f5>", "<f6>",
        "<f7>", "<f8>", "<f9>", "<f10>", "<f11>", "<f12>",
    ]

    def set_hotkey(self, hotkey: str) -> None:
        """Store the selected hotkey."""
        self.selected_hotkey = hotkey

    # ─── Model selection ─────────────────────────────────────────────

    MODEL_OPTIONS = [
        {"name": "tiny.en", "size": "~75MB", "speed": "Fastest", "description": "Best for quick notes"},
        {"name": "small.en", "size": "~466MB", "speed": "Fast", "description": "Recommended for most users"},
        {"name": "medium.en", "size": "~1.5GB", "speed": "Slow", "description": "Best accuracy"},
    ]

    def set_model(self, model_name: str) -> None:
        """Store the selected model."""
        self.selected_model = model_name

    # ─── Apply collected settings ────────────────────────────────────

    def apply_settings(self, config) -> None:
        """Apply all collected settings to the Config object."""
        if self.selected_microphone is not None:
            config.microphone = self.selected_microphone
        config.hotkey = self.selected_hotkey
        config.model_size = self.selected_model
        config.save()
        log.info(
            "[ONBOARDING] Settings applied: mic=%s, hotkey=%s, model=%s",
            self.selected_microphone, self.selected_hotkey, self.selected_model,
        )
