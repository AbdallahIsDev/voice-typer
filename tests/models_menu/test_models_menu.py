"""Tray models menu tests split out of the former ``tests/test_history_and_models.py``.

Domain: tray menu — ``build_models_submenu_data`` config_provider
support + corrupt-config fallback.

The former ``TestTrayIconNoLongerReferencesStaleSvg`` /
``TestTrayIconUsesGetchannelNotSplitIndex`` classes that lived here
were merged into ``tests/test_tray_icon.py`` (Phase 4.5 / TC-15
completion) so each tray-icon regression lives next to the rest of
the tray-icon tests.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations


class TestBuildModelsSubmenuConfigProvider:
    """build_models_menu_items accepts config_provider kwarg."""

    def test_accepts_config_provider(self, tmp_path):
        from unittest.mock import MagicMock

        from voice_typer.server.tray_models import build_models_submenu_data

        config = MagicMock()
        config.model_size = "small.en"
        config.asr_backend = "whisper"

        result = build_models_submenu_data(
            lambda: tmp_path,
            lambda name: None,
            config_provider=config,
        )
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "small.en" in active_models

    def test_corrupt_config_json_falls_back_to_defaults_and_logs(self, tmp_path, caplog):
        """PI-19 regression: a corrupt ``config.json`` must NOT silently
        fall through to defaults. The tray menu still returns defaults
        (so the user sees a functional menu), but a ``log.debug`` line
        records the failure so it can be diagnosed from
        ``voice-typer.log``. Mirrors the pattern at ``config.py:1043``.
        """
        import logging

        from voice_typer.server.tray_models import build_models_submenu_data

        config_dir = tmp_path
        config_path = config_dir / "config.json"
        # Write corrupt JSON that json.load will reject.
        config_path.write_text("{not valid json at all", encoding="utf-8")

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.tray_models"):
            result = build_models_submenu_data(
                lambda: config_dir,
                lambda name: None,
                config_provider=None,
            )

        # Defaults must be returned so the tray menu is still usable.
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "tiny.en" in active_models

        # The corrupt-config log line must be present.
        corrupt_log_lines = [r.message for r in caplog.records if "failed to read config.json" in r.message]
        assert corrupt_log_lines, (
            "PI-19 regression: expected a log.debug line recording the config.json read failure, got none"
        )
