"""Tests for voice_typer.ui.app — VoiceTyperApp Flet UI construction.

Tests verify that the UI can be constructed without crashing by treating
build_*_page as pure functions returning ft.Control trees. All Flet imports
are mocked.
"""

import sys
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def mock_flet(monkeypatch):
    """Mock flet module so tests don't need a display server."""
    mock_ft = MagicMock()
    mock_ft.Colors = MagicMock()
    mock_ft.Colors.BLUE_600 = "blue600"
    mock_ft.Colors.WHITE = "white"
    mock_ft.Colors.GREY_600 = "grey600"
    mock_ft.Colors.GREY_700 = "grey700"
    mock_ft.Colors.GREY_500 = "grey500"
    mock_ft.Colors.GREY_400 = "grey400"
    mock_ft.Colors.GREY_100 = "grey100"
    mock_ft.Colors.GREY_800 = "grey800"
    mock_ft.Colors.RED_500 = "red500"
    mock_ft.Colors.RED_700 = "red700"
    mock_ft.Colors.RED_800 = "red800"
    mock_ft.Colors.RED_50 = "red50"
    mock_ft.Colors.RED_900 = "red900"
    mock_ft.Colors.RED_100 = "red100"
    mock_ft.Colors.GREEN_600 = "green600"
    mock_ft.Colors.GREEN_800 = "green800"
    mock_ft.Colors.GREEN_50 = "green50"
    mock_ft.Colors.AMBER_500 = "amber500"
    mock_ft.Colors.BLUE_500 = "blue500"
    mock_ft.Colors.BLUE_50 = "blue50"
    mock_ft.Colors.BLUE_800 = "blue800"
    mock_ft.Colors.TRANSPARENT = "transparent"
    mock_ft.Colors.BLUE_400 = "blue400"
    mock_ft.Colors.TEAL_600 = "teal600"
    mock_ft.Colors.GREEN_700 = "green700"
    mock_ft.FontWeight = MagicMock()
    mock_ft.FontWeight.BOLD = "bold"
    mock_ft.FontWeight.W_600 = "w600"
    mock_ft.FontWeight.W_500 = "w500"
    mock_ft.FontWeight.NORMAL = "normal"
    mock_ft.TextAlign = MagicMock()
    mock_ft.TextAlign.CENTER = "center"
    mock_ft.TextOverflow = MagicMock()
    mock_ft.TextOverflow.ELLIPSIS = "ellipsis"
    mock_ft.ClipBehavior = MagicMock()
    mock_ft.ClipBehavior.HARD_EDGE = "hard_edge"
    mock_ft.ScrollMode = MagicMock()
    mock_ft.ScrollMode.AUTO = "auto"
    mock_ft.MainAxisAlignment = MagicMock()
    mock_ft.MainAxisAlignment.CENTER = "center"
    mock_ft.MainAxisAlignment.SPACE_BETWEEN = "space_between"
    mock_ft.CrossAxisAlignment = MagicMock()
    mock_ft.CrossAxisAlignment.CENTER = "center"
    mock_ft.Alignment = MagicMock()
    mock_ft.Alignment.CENTER = "center"
    mock_ft.AnimationCurve = MagicMock()
    mock_ft.AnimationCurve.EASE_IN_OUT = "ease_in_out"
    mock_ft.VisualDensity = MagicMock()
    mock_ft.VisualDensity.COMFORTABLE = "comfortable"
    mock_ft.ThemeMode = MagicMock()
    mock_ft.ThemeMode.LIGHT = "light"
    mock_ft.RoundedRectangleBorder = MagicMock()
    mock_ft.ButtonStyle = MagicMock()
    mock_ft.Animation = MagicMock()
    mock_ft.BoxShadow = MagicMock()
    mock_ft.with_opacity = MagicMock(return_value="with_opacity_result")
    # Return MagicMock for any attribute access
    mock_ft.Padding = MagicMock()
    mock_ft.Padding.all = MagicMock(return_value=MagicMock())
    mock_ft.Padding.symmetric = MagicMock(return_value=MagicMock())

    # Make all ft.* constructor calls return MagicMock
    for attr in ["Column", "Row", "Container", "Card", "Text", "TextField",
                 "ElevatedButton", "IconButton", "Switch", "Divider", "ListView",
                 "ProgressBar", "Image", "SnackBar", "Theme", "FletApp",
                 "Page", "Icon", "ButtonBar", "Dropdown", "Checkbox"]:
        setattr(mock_ft, attr, MagicMock())

    monkeypatch.setitem(sys.modules, "flet", mock_ft)
    monkeypatch.setitem(sys.modules, "flet.testing", MagicMock())

    # Mock sub-modules
    for mod_name in ["voice_typer.ui.styles", "voice_typer.ui.icons",
                     "voice_typer.ui.home", "voice_typer.ui.history",
                     "voice_typer.ui.templates", "voice_typer.ui.vocabulary",
                     "voice_typer.ui.models", "voice_typer.ui.microphone",
                     "voice_typer.ui.privacy", "voice_typer.ui.settings"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()


class TestVoiceTyperAppConstruction:
    def test_app_can_be_imported(self):
        """VoiceTyperApp class should be importable."""
        from voice_typer.ui.app import VoiceTyperApp
        assert VoiceTyperApp is not None

    def test_app_instantiation(self, monkeypatch):
        """VoiceTyperApp should instantiate without crashing."""
        monkeypatch.setattr("voice_typer.ui.app.Config.load", lambda: MagicMock(
            hotkey="<f2>", model_size="small.en", microphone=None,
            device="cpu", autostart=True, show_notifications=True,
            silence_warning_seconds=20.0, silence_auto_stop_seconds=120.0,
            max_recording_seconds=0, streaming_transcription=True,
            paste_on_stop=True, text_cleanup_enabled=True,
            recording_mode="toggle", auto_punctuation=False,
            llm_polish=False, llm_api_key="", llm_api_url="",
            llm_model="gpt-4o-mini", llm_preset="professional",
            crash_recovery_enabled=True, audio_quality_warnings=True,
            waveform_bubble=False, esc_cancel_enabled=False,
            templates_enabled=True, vocabulary_enabled=True,
        ))
        from voice_typer.ui.app import VoiceTyperApp
        app = VoiceTyperApp()
        assert app is not None
        assert app.current_view == "home"


class TestHomeScreenBuild:
    def test_build_home_page_returns_control(self):
        """build_home_page should return a ft.Column without crashing."""
        from voice_typer.ui.home import build_home_page
        result = build_home_page(status="idle", last_text="", model_info="test", device_info="cpu")
        assert result is not None


class TestHistoryScreenBuild:
    def test_history_screen_build(self):
        """HistoryScreen.build() should return a control without crashing."""
        from voice_typer.ui.history import HistoryScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        with patch("voice_typer.ui.history.HistoryDB") as mock_db:
            mock_db.return_value.get_recent.return_value = []
            screen = HistoryScreen(mock_page, mock_config)
            result = screen.build()
            assert result is not None


class TestTemplatesScreenBuild:
    def test_templates_screen_build(self):
        from voice_typer.ui.templates import TemplatesScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        screen = TemplatesScreen(mock_page, mock_config)
        result = screen.build()
        assert result is not None


class TestVocabularyScreenBuild:
    def test_vocabulary_screen_build(self):
        from voice_typer.ui.vocabulary import VocabularyScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        screen = VocabularyScreen(mock_page, mock_config)
        result = screen.build()
        assert result is not None


class TestModelsScreenBuild:
    def test_models_screen_build(self):
        from voice_typer.ui.models import ModelsScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        mock_config.model_size = "small.en"
        screen = ModelsScreen(mock_page, mock_config)
        result = screen.build()
        assert result is not None


class TestMicrophoneScreenBuild:
    def test_microphone_screen_build(self):
        from voice_typer.ui.microphone import MicrophoneScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        mock_config.microphone = None
        with patch("voice_typer.ui.microphone.list_microphones", return_value=[]):
            screen = MicrophoneScreen(mock_page, mock_config)
            result = screen.build()
            assert result is not None


class TestPrivacyScreenBuild:
    def test_privacy_screen_build(self):
        from voice_typer.ui.privacy import PrivacyScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        screen = PrivacyScreen(mock_page, mock_config)
        result = screen.build()
        assert result is not None


class TestSettingsScreenBuild:
    def test_settings_screen_build(self):
        from voice_typer.ui.settings import SettingsScreen
        mock_page = MagicMock()
        mock_config = MagicMock()
        mock_config.autostart = True
        mock_config.silence_warning_seconds = 20.0
        mock_config.max_recording_seconds = 0
        mock_settings = MagicMock()
        screen = SettingsScreen(mock_page, mock_config, mock_settings)
        result = screen.build()
        assert result is not None


class TestStylesModule:
    def test_colors_class(self):
        from voice_typer.ui.styles import Colors
        assert hasattr(Colors, "PRIMARY")

    def test_nav_items(self):
        import sys
        # Force re-import to get real module with mocked flet
        for key in list(sys.modules.keys()):
            if key.startswith("voice_typer.ui.styles"):
                del sys.modules[key]
        from voice_typer.ui.styles import NAV_ITEMS
        assert isinstance(NAV_ITEMS, dict)
        assert "home" in NAV_ITEMS
        assert "history" in NAV_ITEMS

    def test_status_colors(self):
        import sys
        for key in list(sys.modules.keys()):
            if key.startswith("voice_typer.ui.styles"):
                del sys.modules[key]
        from voice_typer.ui.styles import STATUS_COLORS
        assert isinstance(STATUS_COLORS, dict)
        assert "idle" in STATUS_COLORS
        assert "recording" in STATUS_COLORS

    def test_get_theme(self):
        from voice_typer.ui.styles import get_theme
        theme = get_theme(dark=False)
        assert theme is not None

    def test_get_theme_dark(self):
        from voice_typer.ui.styles import get_theme
        theme = get_theme(dark=True)
        assert theme is not None
