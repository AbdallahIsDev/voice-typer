"""NH-17 regression: canonical force-cancel-transcription wording."""

from __future__ import annotations

import json
import pathlib

from voice_typer.server import tray_i18n

_RENDERER_TRANSLATIONS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "i18n"
    / "translations"
    / "en.json"
)


def test_force_cancel_stuck_transcription_key_is_gone_from_all_locales() -> None:
    """The dead ``force_cancel_stuck_transcription`` key was removed from"""
    for locale, labels in tray_i18n._TRAY_LABELS_LOCALES.items():
        assert "force_cancel_stuck_transcription" not in labels, (
            f"locale {locale!r} still defines the dead "
            f"force_cancel_stuck_transcription key, NH-17 canonicalisation "
            f"removed it; the tray menu reads force_cancel_transcription."
        )


def test_force_cancel_transcription_canonical_label_is_present_in_all_locales() -> None:
    """Every locale dict defines the canonical"""
    for locale, labels in tray_i18n._TRAY_LABELS_LOCALES.items():
        assert "force_cancel_transcription" in labels, (
            f"locale {locale!r} missing the canonical force_cancel_transcription key"
        )
        # The label should be a non-empty string.
        assert isinstance(labels["force_cancel_transcription"], str)
        assert labels["force_cancel_transcription"].strip() != ""


def test_canonical_english_label_uses_lowercase_cancel() -> None:
    """The canonical English label is ``\"Force cancel transcription\"`` —"""
    assert tray_i18n._TRAY_LABELS_EN["force_cancel_transcription"] == "Force cancel transcription"


def test_renderer_force_cancel_hint_uses_canonical_wording() -> None:
    """The renderer's ``home.forceCancelHint`` English string contains"""
    en = json.loads(_RENDERER_TRANSLATIONS.read_text(encoding="utf-8"))
    home = en.get("home", {})
    hint = home.get("forceCancelHint", "")
    assert "Force cancel transcription" in hint, (
        f"home.forceCancelHint should contain the canonical 'Force cancel transcription' wording (NH-17); got: {hint!r}"
    )
    # The legacy "Stuck" wording must NOT be present.
    assert "Stuck" not in hint
