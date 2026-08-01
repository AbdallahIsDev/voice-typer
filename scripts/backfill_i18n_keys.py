"""Backfill missing i18n keys into all non-English locale files.

The recent  (keyring) and §3.2-§3.7 (punctuation cheat sheet) sub-agents
added new English keys but didn't update the other 7 locale files. This script
copies the English values as placeholders — translators can refine later.

Run: python scripts/backfill_i18n_keys.py
"""

from __future__ import annotations

from pathlib import Path

# shared helpers live in _i18n_common (canonical flatten / load
# save / merge routines). This script previously duplicated these ~50
# LOC alongside add_i18n_keys.py and apply_translations.py.
from _i18n_common import (
    flatten_keys,
    load_json,
    merge_en_into_locale,
    save_json,
)

TRANSLATIONS_DIR = (
    Path(__file__).resolve().parent.parent
    / "voice_typer"
    / "client"
    / "src"
    / "renderer"
    / "src"
    / "i18n"
    / "translations"
)
EN_FILE = TRANSLATIONS_DIR / "en.json"
LOCALES = ["ar", "de", "es", "fr", "hi", "ru", "zh"]


def backfill_locale(locale: str, en: dict, en_keys: set[str]) -> int:
    """Add any missing keys (English placeholder values) to ``locale``.

    Returns the number of keys added. Existing translations are never
    overwritten (``on_conflict="skip"``).
    """
    path = TRANSLATIONS_DIR / f"{locale}.json"
    data = load_json(path)
    locale_keys = flatten_keys(data)
    missing = en_keys - locale_keys
    if not missing:
        return 0

    # Reload original to preserve existing top-level order, then merge
    # en's missing keys into it. ``on_conflict="skip"`` preserves
    # translator-authored values — only ADDS keys that are absent.
    original = load_json(path)
    _, added = merge_en_into_locale(en, original, on_conflict="skip")
    save_json(path, original)
    return len(added)


def main() -> int:
    en = load_json(EN_FILE)
    en_keys = flatten_keys(en)
    total_added = 0
    for loc in LOCALES:
        added = backfill_locale(loc, en, en_keys)
        if added:
            print(f"  {loc}.json: +{added} keys backfilled")
            total_added += added
        else:
            print(f"  {loc}.json: up to date")
    print(f"Total: {total_added} keys backfilled across {len(LOCALES)} locales")
    return 0 if total_added >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
