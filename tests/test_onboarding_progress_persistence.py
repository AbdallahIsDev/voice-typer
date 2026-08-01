"""NH-23 regression: in-progress onboarding wizard state persists across
Python-process restarts.

Before NH-23, ``OnboardingController`` stored ``_current_step``,
``selected_microphone``, ``selected_hotkey``, and ``selected_model`` as
INSTANCE variables only — never written to disk. ``onboarding_start()``
always created a NEW ``OnboardingController()``. When the Python process
restarted (app close/reopen), ``self._onboarding`` was lost. Only
``apply_settings`` (called from the Done step via ``onboarding_apply``)
persisted selections to ``config.json``. If a user closed the app
mid-wizard, they lost ALL selections and restarted at the Welcome step
on next launch.

NH-23 added a ``.onboarding_progress`` marker file alongside the
existing ``.onboarding_started`` marker. State mutations (next/prev/
set_microphone/set_hotkey/set_model) call ``_persist_progress()``;
terminal transitions (mark_complete/skip/reset/apply_settings) call
``_clear_progress()``. ``__init__`` calls ``_load_progress()`` to
resume from the marker if it exists.

This test pins the contract:
  * ``next_step`` writes a progress file.
  * A fresh ``OnboardingController`` instance reads it and resumes.
  * ``apply_settings`` (via ``mark_complete``) clears the marker.
  * ``skip`` clears the marker.
  * ``reset`` clears the marker.
  * Corrupt marker file is silently ignored (best-effort restore).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from voice_typer.server.onboarding import OnboardingController


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Isolated config directory per test (no leaked state)."""
    d = tmp_path / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_controller(config_dir: Path) -> OnboardingController:
    return OnboardingController(config_dir=config_dir)


def test_next_step_writes_progress_marker(config_dir: Path) -> None:
    """``next_step()`` writes the ``.onboarding_progress`` file with the
    current step + default selections."""
    ctrl = _new_controller(config_dir)
    ctrl.next_step()  # step 0 → step 1
    progress_file = config_dir / ".onboarding_progress"
    assert progress_file.exists(), "next_step should persist progress to disk"
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    assert data["current_step"] == 1
    # Defaults are persisted alongside the step.
    assert data["selected_hotkey"] == "<caps_lock>"
    assert data["selected_model"] == "small.en"
    assert "selected_microphone" in data


def test_new_controller_resumes_from_progress_marker(config_dir: Path) -> None:
    """Closing the app mid-wizard and reopening resumes at the saved step
    with the saved selections."""
    # First instance: walk the wizard forward and pick selections.
    ctrl1 = _new_controller(config_dir)
    ctrl1.next_step()  # → Microphone
    ctrl1.set_microphone("mic-99")
    ctrl1.next_step()  # → Permissions
    ctrl1.next_step()  # → Hotkey
    ctrl1.set_hotkey("<f5>")
    ctrl1.next_step()  # → Model
    ctrl1.set_model("medium.en")

    # Simulate process restart: create a NEW controller in the same dir.
    ctrl2 = _new_controller(config_dir)
    # Resume state should match what ctrl1 left.
    assert ctrl2.current_step == 4, f"Expected step 4 (Model), got {ctrl2.current_step}"
    assert ctrl2.selected_microphone == "mic-99"
    assert ctrl2.selected_hotkey == "<f5>"
    assert ctrl2.selected_model == "medium.en"


def test_prev_step_persists_progress(config_dir: Path) -> None:
    """``prev_step()`` writes the marker so going back survives restart."""
    ctrl1 = _new_controller(config_dir)
    ctrl1.next_step()  # 1
    ctrl1.next_step()  # 2
    ctrl1.prev_step()  # back to 1

    ctrl2 = _new_controller(config_dir)
    assert ctrl2.current_step == 1


def test_set_methods_persist_selections(config_dir: Path) -> None:
    """``set_microphone`` / ``set_hotkey`` / ``set_model`` each persist
    their selection so the user doesn't lose it on restart."""
    ctrl1 = _new_controller(config_dir)
    ctrl1.set_microphone("device-A")
    ctrl1.set_hotkey("<f8>")
    ctrl1.set_model("tiny.en")

    ctrl2 = _new_controller(config_dir)
    assert ctrl2.selected_microphone == "device-A"
    assert ctrl2.selected_hotkey == "<f8>"
    assert ctrl2.selected_model == "tiny.en"


def test_apply_settings_clears_progress_marker(config_dir: Path) -> None:
    """``apply_settings`` (the Done-step action) clears the marker so a
    subsequent launch starts fresh (wizard is complete)."""
    ctrl = _new_controller(config_dir)
    ctrl.next_step()
    ctrl.set_hotkey("<f3>")
    progress_file = config_dir / ".onboarding_progress"
    assert progress_file.exists()

    # Build a minimal fake config object that satisfies apply_settings.
    class _FakeConfig:
        microphone: str | None = None
        hotkey: str = ""
        model_size: str = ""

        def save(self) -> bool:
            return True

    ctrl.apply_settings(_FakeConfig())
    assert not progress_file.exists(), (
        "apply_settings should clear the progress marker so a relaunch doesn't try to resume a completed wizard"
    )


def test_skip_clears_progress_marker(config_dir: Path) -> None:
    """``skip`` (skip-onboarding action) clears the marker."""
    ctrl = _new_controller(config_dir)
    ctrl.next_step()
    progress_file = config_dir / ".onboarding_progress"
    assert progress_file.exists()

    ctrl.skip()
    assert not progress_file.exists()


def test_reset_clears_progress_marker(config_dir: Path) -> None:
    """``reset`` (re-run onboarding affordance) clears the marker so the
    next launch starts at the Welcome step."""
    ctrl = _new_controller(config_dir)
    ctrl.next_step()
    ctrl.set_microphone("temp")
    progress_file = config_dir / ".onboarding_progress"
    assert progress_file.exists()

    ctrl.reset()
    assert not progress_file.exists()
    # And the controller's in-memory state is also reset to defaults.
    assert ctrl.current_step == 0
    assert ctrl.selected_microphone is None
    assert ctrl.selected_hotkey == "<caps_lock>"
    assert ctrl.selected_model == "small.en"


def test_corrupt_progress_marker_is_ignored(config_dir: Path) -> None:
    """A corrupt progress marker file does NOT crash ``__init__`` — the
    controller falls back to defaults and lets the next mutation
    overwrite the file."""
    progress_file = config_dir / ".onboarding_progress"
    progress_file.write_text("{not valid json", encoding="utf-8")

    # Constructor must not raise.
    ctrl = _new_controller(config_dir)
    # Defaults remain in place.
    assert ctrl.current_step == 0
    assert ctrl.selected_microphone is None
    assert ctrl.selected_hotkey == "<caps_lock>"
    assert ctrl.selected_model == "small.en"


def test_progress_marker_uses_secure_atomic_write(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The progress marker is written via ``_secure_atomic_write`` (0o600
    on POSIX, O_NOFOLLOW symlink protection) — matches the security
    posture of ``mark_complete``. We assert the helper is invoked."""
    from voice_typer.server import config as cfg_mod

    calls: list[Any] = []

    def fake_write(path: Path, data: str) -> None:
        calls.append((path, data))

    monkeypatch.setattr(cfg_mod, "_secure_atomic_write", fake_write)

    ctrl = _new_controller(config_dir)
    ctrl.next_step()
    assert len(calls) >= 1
    written_path, _payload = calls[0]
    assert written_path.name == ".onboarding_progress"
