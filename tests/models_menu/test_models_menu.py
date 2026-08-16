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
        config.model_size = "tiny"
        config.asr_backend = "whisper"

        result = build_models_submenu_data(
            lambda: tmp_path,
            lambda name: None,
            config_provider=config,
        )
        active_models = [name for name, _, is_active, _ in result if is_active]
        assert "tiny" in active_models

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
        assert "tiny" in active_models

        # The corrupt-config log line must be present.
        corrupt_log_lines = [r.message for r in caplog.records if "failed to read config.json" in r.message]
        assert corrupt_log_lines, (
            "PI-19 regression: expected a log.debug line recording the config.json read failure, got none"
        )


class TestTrayModelLabelsCarryFamilyGlyphs:
    """Family logo marks on the tray model labels.

    Native tray menus are text-only (pystray's ``MenuItem`` has no
    image support — verified 2026-08-15), so the family logos from the
    Models page (``src/assets/models/``) cannot be rendered as real
    images in the submenu. Each model label instead carries a per-family
    Unicode mark approximating its brand: ``✱`` ≈ OpenAI (Whisper),
    ``◉`` ≈ NVIDIA (Parakeet), ``⊙`` ≈ Qwen. The data builder's tuple
    contract (name, downloaded, is_active, change_fn) is untouched —
    the glyph is applied only at the MenuItem-label layer.
    """

    def _menu_labels(self, tmp_path, config):
        """Build the menu with all candidates marked downloaded and
        return the captured item labels."""
        from unittest.mock import patch

        from voice_typer.server.tray_models import build_models_menu_items

        captured: list[str] = []

        class FakeMenuItem:
            def __init__(self, text, _fn, **kwargs):
                captured.append(text)

        with patch("voice_typer.server.asr_setup.ensure_hf_env", lambda: None), patch(
            "voice_typer.server.tray_models._check_hf_model_downloaded", lambda *a, **k: True
        ), patch(
            "voice_typer.server.tray_models._check_qwen_model_downloaded", lambda *a, **k: True
        ):
            build_models_menu_items(
                lambda: tmp_path,
                lambda name: None,
                lambda fn: fn,
                lambda: None,
                menu_item_class=FakeMenuItem,
                menu_separator="---",
                config_provider=config,
            )
        return captured

    def test_every_downloaded_model_label_has_its_family_glyph(self, tmp_path):
        """Each downloaded candidate renders as ``<glyph> <name>`` with
        the glyph matching its family (✱ Whisper / ◉ NVIDIA / ⊙ Qwen)."""
        from unittest.mock import MagicMock

        config = MagicMock()
        config.model_size = "tiny"
        config.asr_backend = "whisper"
        config.qwen_model_path = None

        labels = self._menu_labels(tmp_path, config)

        expected = {
            "✱ tiny",
            "✱ large-v3",
            "✱ large-v3-turbo",
            "◉ parakeet",
            "⊙ qwen",
        }
        assert expected.issubset(set(labels)), (
            f"every downloaded model label must carry its family glyph, got: {labels}"
        )

    def test_unknown_model_falls_back_to_bare_name(self):
        """A model name outside the tray catalog renders without a glyph
        rather than raising or producing an empty prefix."""
        from voice_typer.server.tray_models import _menu_label

        assert _menu_label("future-model") == "future-model"
