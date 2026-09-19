"""Regression tests for config-load-warning surfacing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from voice_typer.server.config import Config
from voice_typer.server.config_sanitizer import sanitize_config_for_ipc


@pytest.fixture
def isolated_config_dir(tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir()`` at an empty tmp_path for the duration"""
    monkeypatch.delenv("VOICE_TYPER_CONFIG_DIR", raising=False)
    return tmp_config_dir


class TestSanitizeSurfacesLastLoadWarnings:
    """``sanitize_config_for_ipc`` must include ``last_load_warnings``"""

    def test_warnings_present_in_sanitized_output(self) -> None:
        """A Config with ``last_load_warnings`` set must surface those"""
        cfg = Config()
        cfg.last_load_warnings = ["validate_config: asr_backend: invalid value 'invalid_backend'"]

        out = sanitize_config_for_ipc(cfg)

        assert "last_load_warnings" in out, (
            "AP-21: sanitize_config_for_ipc must include 'last_load_warnings' "
            f"in its output. Got keys: {sorted(out.keys())[:10]}..."
        )
        assert out["last_load_warnings"] == ["validate_config: asr_backend: invalid value 'invalid_backend'"], (
            "AP-21: last_load_warnings should be passed through verbatim when "
            f"no redaction is needed. Got: {out['last_load_warnings']!r}"
        )

    def test_warnings_default_to_empty_list_when_unset(self) -> None:
        """A Config without ``last_load_warnings`` set (e.g. a fresh"""
        cfg = Config()
        # ``__post_init__`` sets last_load_warnings = None.
        assert cfg.last_load_warnings is None

        out = sanitize_config_for_ipc(cfg)

        assert "last_load_warnings" in out
        assert out["last_load_warnings"] == [], (
            "AP-21: a None last_load_warnings should be normalized to [] "
            f"so the renderer always sees a list. Got: {out['last_load_warnings']!r}"
        )

    def test_warnings_truncated_to_200_chars(self) -> None:
        """Each warning is truncated to 200 chars (plus an ellipsis)"""
        cfg = Config()
        # Use a long sentence (with spaces) so ``redact_secret``'s
        long_warning = "this is a long load warning " * 20  # ~520 chars
        cfg.last_load_warnings = [long_warning]

        out = sanitize_config_for_ipc(cfg)

        assert len(out["last_load_warnings"]) == 1
        truncated = out["last_load_warnings"][0]
        # 200 chars of content + 1 ellipsis char.
        assert len(truncated) == 201, (
            f"AP-21: warning should be truncated to 200 chars + ellipsis. Got len={len(truncated)} text={truncated!r}"
        )
        assert truncated.endswith("…")

    def test_warnings_redact_api_keys(self) -> None:
        """Warnings may embed field values (e.g. a malformed API key"""
        cfg = Config()
        secret = "sk-abcd1234efgh5678ijkl9012mnop3456"
        cfg.last_load_warnings = [f"validate_config: llm_api_key: invalid value {secret!r}"]

        out = sanitize_config_for_ipc(cfg)

        assert "last_load_warnings" in out
        warning = out["last_load_warnings"][0]
        assert secret not in warning, (
            f"AP-21: API keys embedded in load warnings must be redacted via redact_pii. Got: {warning!r}"
        )

    def test_warnings_redact_email_pii(self) -> None:
        """Email addresses in warnings must be redacted to ``[EMAIL]``."""
        cfg = Config()
        cfg.last_load_warnings = ["cloud_api_url 'https://user@example.com/v1' is invalid"]

        out = sanitize_config_for_ipc(cfg)

        warning = out["last_load_warnings"][0]
        assert "user@example.com" not in warning, (
            f"AP-21: email PII in load warnings must be redacted. Got: {warning!r}"
        )

    def test_warnings_returned_as_new_list_instance(self) -> None:
        """The returned list must be a NEW list, not the same object"""
        cfg = Config()
        original = ["warning one"]
        cfg.last_load_warnings = list(original)

        out = sanitize_config_for_ipc(cfg)

        assert out["last_load_warnings"] is not cfg.last_load_warnings, (
            "AP-21: sanitized last_load_warnings must be a new list instance, "
            "not the same object as Config.last_load_warnings."
        )
        # Mutating the sanitized list must not affect the Config.
        out["last_load_warnings"].append("injected")
        assert cfg.last_load_warnings == original


class TestLoadResetsInvalidEnumFields:
    """``Config.load()`` must reset invalid ``Literal[...]`` enum"""

    def test_invalid_asr_backend_reset_to_default(self, isolated_config_dir: Path) -> None:
        """A hand-edited ``asr_backend=\"invalid_backend\"`` must be"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "invalid_backend"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.asr_backend == "whisper", (
            f"AP-22: invalid asr_backend should be reset to the default 'whisper'. Got: {cfg.asr_backend!r}"
        )
        # A warning must be appended to last_load_warnings mentioning
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "asr_backend" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for asr_backend must be appended to "
            f"last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_theme_mode_reset_to_default(self, isolated_config_dir: Path) -> None:
        """default ``\"system\"`` on load."""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "theme_mode": "mauve"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.theme_mode == "system"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "theme_mode" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for theme_mode must be appended to "
            f"last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_recording_mode_reset_to_default(self, isolated_config_dir: Path) -> None:
        """default ``\"toggle\"`` on load."""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "recording_mode": "continuous"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.recording_mode == "toggle"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "recording_mode" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for recording_mode must be appended to "
            f"last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_bubble_position_reset_to_default(self, isolated_config_dir: Path) -> None:
        """Literal ``[\"top\", \"bottom\"]``) must be reset to the default"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "bubble_position": "middle"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.bubble_position == "bottom"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "bubble_position" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for bubble_position must be appended "
            f"to last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_tray_left_click_action_reset_to_default(self, isolated_config_dir: Path) -> None:
        """A hand-edited ``tray_left_click_action=\"open_menu\"`` (not"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "tray_left_click_action": "open_menu",
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.tray_left_click_action == "open_app"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "tray_left_click_action" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for tray_left_click_action must be "
            f"appended to last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_theme_preset_reset_to_default(self, isolated_config_dir: Path) -> None:
        """Literal preset allowlist) must be reset to the default"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "theme_preset": "hot_pink"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.theme_preset == "default"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "theme_preset" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for theme_preset must be appended "
            f"to last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_invalid_bubble_behavior_reset_to_default(self, isolated_config_dir: Path) -> None:
        """Literal ``[\"show_on_record\", \"always_visible\"]``) must be"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "bubble_behavior": "hover_only"}),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.bubble_behavior == "show_on_record"
        reset_warnings = [w for w in (cfg.last_load_warnings or []) if "bubble_behavior" in w and "reset" in w]
        assert reset_warnings, (
            "AP-22: a reset warning for bubble_behavior must be appended "
            f"to last_load_warnings. Got: {cfg.last_load_warnings!r}"
        )

    def test_valid_enum_values_are_not_reset(self, isolated_config_dir: Path) -> None:
        """A valid enum value must NOT be reset and must NOT produce"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "asr_backend": "qwen",
                    "theme_mode": "dark",
                    "recording_mode": "push_to_talk",
                    "bubble_position": "top",
                    "bubble_behavior": "always_visible",
                    "tray_left_click_action": "toggle_dictation",
                    "theme_preset": "dracula",
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load()

        # All values must be preserved verbatim.
        assert cfg.asr_backend == "qwen"
        assert cfg.theme_mode == "dark"
        assert cfg.recording_mode == "push_to_talk"
        assert cfg.bubble_position == "top"
        assert cfg.bubble_behavior == "always_visible"
        assert cfg.tray_left_click_action == "toggle_dictation"
        assert cfg.theme_preset == "dracula"
        # No reset warnings for any of these fields.
        reset_warnings = [
            w
            for w in (cfg.last_load_warnings or [])
            if "reset" in w
            and any(
                field in w
                for field in (
                    "asr_backend",
                    "theme_mode",
                    "recording_mode",
                    "bubble_position",
                    "bubble_behavior",
                    "tray_left_click_action",
                    "theme_preset",
                )
            )
        ]
        assert reset_warnings == [], (
            f"AP-22: valid enum values must NOT produce reset warnings. Got: {reset_warnings!r}"
        )

    def test_reset_works_for_multiple_invalid_fields(self, isolated_config_dir: Path) -> None:
        """When MULTIPLE enum fields are invalid, each must be reset"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "asr_backend": "invalid_a",
                    "theme_mode": "invalid_b",
                    "recording_mode": "invalid_c",
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load()

        assert cfg.asr_backend == "whisper"
        assert cfg.theme_mode == "system"
        assert cfg.recording_mode == "toggle"

        warnings = cfg.last_load_warnings or []
        # Each invalid field should have its own reset warning.
        for field in ("asr_backend", "theme_mode", "recording_mode"):
            field_reset_warnings = [w for w in warnings if field in w and "reset" in w]
            assert field_reset_warnings, f"AP-22: expected a reset warning for {field}. Got warnings: {warnings!r}"


class TestWarningsSurfaceThroughSanitizer:
    """End-to-end: a ``Config.load()`` that produces reset warnings"""

    def test_load_then_sanitize_surfaces_reset_warning(self, isolated_config_dir: Path) -> None:
        """A hand-edited ``asr_backend=\"invalid\"`` loaded via"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "invalid_backend"}),
            encoding="utf-8",
        )

        cfg = Config.load()
        sanitized = sanitize_config_for_ipc(cfg)

        # The field was reset (AP-22).
        assert sanitized["asr_backend"] == "whisper"
        # The warning surfaces through the sanitizer (AP-21).
        assert "last_load_warnings" in sanitized
        reset_warnings = [w for w in sanitized["last_load_warnings"] if "asr_backend" in w and "reset" in w]
        assert reset_warnings, (
            "AP-21+AP-22: the reset warning for asr_backend must surface "
            "through sanitize_config_for_ipc. Got: "
            f"{sanitized['last_load_warnings']!r}"
        )


def _set_valid_optional_numeric_fields(cfg: Config) -> Config:
    """Set the four ``Optional[int]`` / ``Optional[float]`` Config fields"""
    cfg.bubble_x = 0
    cfg.bubble_y = 0
    cfg.bubble_scale = 1.0
    cfg.test_duration_seconds = 5
    return cfg


class _FakeTray:
    """assertion. Mirrors the ``TrayIcon.notify(title, message)``"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.calls.append((title, message))


class _FakeApp:
    """Minimal app double exposing the three attributes"""

    def __init__(self, config: Config, tray: _FakeTray) -> None:
        self.config = config
        self.tray = tray
        # Use a real RLock so the ``with`` context works. The launcher
        import threading

        self._config_mutation_lock = threading.RLock()


class TestEditorReloadFeedback:
    """``ConfigEditorLauncher.launch`` must call ``tray.notify`` after"""

    def test_no_notification_when_no_warnings(self, isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A clean reload (no warnings) must NOT trigger a tray"""
        # Write a valid config so load() produces no warnings.
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps({"schema_version": 3, "asr_backend": "whisper"}),
            encoding="utf-8",
        )

        from voice_typer.server import config_editor

        def _noop_launcher(_path: Any) -> None:
            return None

        monkeypatch.setitem(config_editor._PLATFORM_LAUNCHERS, "linux", _noop_launcher)
        monkeypatch.setattr(config_editor, "_current_platform", lambda: "linux")

        # Save a valid config first so the launcher's ``config.save()``
        cfg = Config()
        # Set the four Optional[int]/Optional[float] fields (bubble_x,
        _set_valid_optional_numeric_fields(cfg)
        cfg.save()
        tray = _FakeTray()
        app = _FakeApp(cfg, tray)

        launcher = config_editor.ConfigEditorLauncher(app)
        launcher.launch(config_file)

        assert tray.calls == [], (
            f"AP-25: no tray.notify should fire when the reload produces no warnings. Got: {tray.calls!r}"
        )

    def test_notification_fires_when_warnings_present(
        self, isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reload that produces warnings must trigger a tray"""
        from voice_typer.server import config_editor

        def _noop_launcher(_path: Any) -> None:
            return None

        monkeypatch.setitem(config_editor._PLATFORM_LAUNCHERS, "linux", _noop_launcher)
        monkeypatch.setattr(config_editor, "_current_platform", lambda: "linux")

        # Construct an in-memory Config with an INVALID asr_backend.
        cfg = Config(asr_backend="invalid_backend")
        # Set the four Optional[int]/Optional[float] fields to valid
        _set_valid_optional_numeric_fields(cfg)
        tray = _FakeTray()
        app = _FakeApp(cfg, tray)

        launcher = config_editor.ConfigEditorLauncher(app)
        launcher.launch(isolated_config_dir / "config.json")

        assert app.config.asr_backend == "whisper"
        warnings = getattr(app.config, "last_load_warnings", []) or []
        assert warnings, (
            "AP-25: expected last_load_warnings to be non-empty after loading a config with an invalid asr_backend."
        )

        # The tray must have been notified.
        assert len(tray.calls) >= 1, (
            f"AP-25: tray.notify must fire when last_load_warnings is non-empty. Got calls: {tray.calls!r}"
        )
        title, message = tray.calls[0]
        assert "warning" in message.lower(), f"AP-25: the tray notification must mention 'warning(s)'. Got: {message!r}"
        assert "asr_backend" in message, (
            "AP-25: the tray notification must include the first warning "
            f"text (which should mention 'asr_backend'). Got: {message!r}"
        )

    def test_notification_truncates_long_first_warning(
        self, isolated_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the first warning is longer than 160 chars, the tray"""
        # A long invalid asr_backend value produces a long
        long_invalid_value = "x" * 300
        from voice_typer.server import config_editor

        def _noop_launcher(_path: Any) -> None:
            return None

        monkeypatch.setitem(config_editor._PLATFORM_LAUNCHERS, "linux", _noop_launcher)
        monkeypatch.setattr(config_editor, "_current_platform", lambda: "linux")

        cfg = Config(asr_backend=long_invalid_value)
        # Set the four Optional[int]/Optional[float] fields to valid
        _set_valid_optional_numeric_fields(cfg)
        tray = _FakeTray()
        app = _FakeApp(cfg, tray)

        launcher = config_editor.ConfigEditorLauncher(app)
        launcher.launch(isolated_config_dir / "config.json")

        assert len(tray.calls) >= 1
        _, message = tray.calls[0]
        assert len(message) < 250, (
            "AP-25: the tray notification must truncate the first warning "
            f"to keep the message readable. Got len={len(message)}: {message!r}"
        )


class TestOptionalNumericFieldsNoSpuriousWarnings:
    """``bubble_x`` / ``bubble_y`` / ``bubble_scale`` /"""

    def test_default_config_produces_no_none_warnings(self, isolated_config_dir) -> None:
        """A default-constructed Config saved + reloaded (with all"""
        cfg = Config()
        # Sanity: the four fields really default to None.
        assert cfg.bubble_x is None
        assert cfg.bubble_y is None
        assert cfg.bubble_scale is None
        assert cfg.test_duration_seconds is None
        cfg.save()

        reloaded = Config.load()

        warnings = reloaded.last_load_warnings or []
        none_reset_warnings = [w for w in warnings if "None" in w and "resetting to default" in w]
        assert none_reset_warnings == [], (
            "VT-1: optional numeric fields at their None defaults must not "
            f"produce spurious None-reset warnings. Got: {none_reset_warnings!r}"
        )
        # The fields must survive reload unchanged (None stays None).
        assert reloaded.bubble_x is None
        assert reloaded.bubble_y is None
        assert reloaded.bubble_scale is None
        assert reloaded.test_duration_seconds is None

    def test_null_on_disk_is_accepted(self, isolated_config_dir) -> None:
        """keep ``None``."""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "bubble_x": None,
                    "bubble_y": None,
                    "bubble_scale": None,
                    "test_duration_seconds": None,
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load()

        warnings = cfg.last_load_warnings or []
        none_reset_warnings = [w for w in warnings if "None" in w and "resetting to default" in w]
        assert none_reset_warnings == [], (
            "VT-1: null on disk for optional numeric fields must be accepted "
            f"without a reset warning. Got: {none_reset_warnings!r}"
        )
        assert cfg.bubble_x is None
        assert cfg.bubble_y is None
        assert cfg.bubble_scale is None
        assert cfg.test_duration_seconds is None

    def test_invalid_numeric_value_still_warns(self, isolated_config_dir) -> None:
        """The None-skip must NOT suppress warnings for genuinely"""
        config_file = isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "bubble_x": "not-a-number",
                    "test_duration_seconds": [1, 2, 3],
                }
            ),
            encoding="utf-8",
        )

        cfg = Config.load()

        warnings = cfg.last_load_warnings or []
        bubble_warnings = [w for w in warnings if "bubble_x" in w and "resetting to default" in w]
        assert bubble_warnings, (
            f"VT-1: a genuinely invalid bubble_x value must still warn + reset. Got warnings: {warnings!r}"
        )
        assert cfg.bubble_x is None  # reset to the dataclass default
        duration_warnings = [w for w in warnings if "test_duration_seconds" in w and "resetting to default" in w]
        assert duration_warnings, (
            f"VT-1: a genuinely invalid test_duration_seconds value must still warn + reset. Got warnings: {warnings!r}"
        )
        assert cfg.test_duration_seconds is None
