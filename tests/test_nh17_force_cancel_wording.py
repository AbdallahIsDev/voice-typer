"""NH-17 regression: canonical force-cancel-transcription wording.

The tray menu and the renderer's Home button must use the same
canonical wording for the "force cancel transcription" recovery action.
Before NH-17, three different wordings existed:

  1. ``tray_i18n.py`` ``force_cancel_transcription`` → "Force cancel transcription"
  2. ``tray_i18n.py`` ``force_cancel_stuck_transcription`` → "Force Cancel Stuck
     Transcription" (DEAD KEY — ``tray.py`` never read it).
  3. renderer ``home.forceCancelHint`` → "Taking too long? Force cancel transcription"

NH-17 deleted the dead ``force_cancel_stuck_transcription`` key from all 8
locale dicts in ``tray_i18n.py``. The canonical label "Force cancel
transcription" (lowercase 'c') is now used by both the tray menu and the
renderer's ``home.forceCancelHint`` (which prefixes it with the
"Taking too long?" context cue).

This test pins the contract:
  * No locale dict in ``tray_i18n.py`` defines the dead key.
  * The English dict defines the canonical key with the canonical wording.
  * The renderer's ``home.forceCancelHint`` English string contains the
    canonical wording (so the visible Home button text matches the tray
    menu item).
"""

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
    """The dead ``force_cancel_stuck_transcription`` key was removed from
    every locale dict in ``tray_i18n.py`` (NH-17)."""
    for locale, labels in tray_i18n._TRAY_LABELS_LOCALES.items():
        assert "force_cancel_stuck_transcription" not in labels, (
            f"locale {locale!r} still defines the dead "
            f"force_cancel_stuck_transcription key — NH-17 canonicalisation "
            f"removed it; the tray menu reads force_cancel_transcription."
        )


def test_force_cancel_transcription_canonical_label_is_present_in_all_locales() -> None:
    """Every locale dict defines the canonical
    ``force_cancel_transcription`` key (used by ``tray.py``)."""
    for locale, labels in tray_i18n._TRAY_LABELS_LOCALES.items():
        assert "force_cancel_transcription" in labels, (
            f"locale {locale!r} missing the canonical force_cancel_transcription key"
        )
        # The label should be a non-empty string.
        assert isinstance(labels["force_cancel_transcription"], str)
        assert labels["force_cancel_transcription"].strip() != ""


def test_canonical_english_label_uses_lowercase_cancel() -> None:
    """The canonical English label is ``"Force cancel transcription"`` —
    lowercase 'c' in 'cancel' (NH-17 wording, distinct from the legacy
    'Force Cancel Stuck Transcription' capital-C wording)."""
    assert tray_i18n._TRAY_LABELS_EN["force_cancel_transcription"] == "Force cancel transcription"


def test_renderer_force_cancel_hint_uses_canonical_wording() -> None:
    """The renderer's ``home.forceCancelHint`` English string contains
    the canonical 'Force cancel transcription' phrase (NH-17)."""
    en = json.loads(_RENDERER_TRANSLATIONS.read_text(encoding="utf-8"))
    home = en.get("home", {})
    hint = home.get("forceCancelHint", "")
    assert "Force cancel transcription" in hint, (
        f"home.forceCancelHint should contain the canonical 'Force cancel transcription' wording (NH-17); got: {hint!r}"
    )
    # The legacy "Stuck" wording must NOT be present.
    assert "Stuck" not in hint
