#!/usr/bin/env python3
"""Propagate missing i18n keys from en.json to all non-English locale files.

This script is IDEMPOTENT: it only adds keys that are missing from the
target locale, and it always uses the English value as the placeholder.
Existing translated values are NEVER overwritten.

Usage:
    python scripts/add_i18n_keys.py                # check all locales
    python scripts/add_i18n_keys.py --locale es    # check one locale
    python scripts/add_i18n_keys.py --all          # write missing keys to all locales

Exit codes:
    0 — all locales are complete (no missing keys)
    1 — one or more locales had missing keys added (or --all was used)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# shared helpers live in _i18n_common (canonical flatten / load
# save / merge routines). This script previously duplicated these ~50
# LOC alongside backfill_i18n_keys.py and apply_translations.py.
from _i18n_common import (
    flatten_keys,
    load_json,
    merge_en_into_locale,
    save_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "voice_typer/client/src/renderer/src/i18n/translations"
EN_FILE = TRANSLATIONS_DIR / "en.json"
NON_ENGLISH_LOCALES = ["ar", "de", "es", "fr", "hi", "ru", "zh"]


def find_missing(en_data: dict, locale_data: dict) -> set[str]:
    """Return the set of keys present in en_data but missing from locale_data."""
    en_keys = flatten_keys(en_data)
    locale_keys = flatten_keys(locale_data)
    return en_keys - locale_keys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--locale",
        type=str,
        help="Process only this locale (e.g. 'es').",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Write missing keys to all non-English locale files.",
    )
    args = parser.parse_args()

    if not EN_FILE.exists():
        print(f"ERROR: {EN_FILE} not found", file=sys.stderr)
        return 2

    en_data = load_json(EN_FILE)

    locales = [args.locale] if args.locale else NON_ENGLISH_LOCALES
    any_added = False

    for loc in locales:
        loc_file = TRANSLATIONS_DIR / f"{loc}.json"
        if not loc_file.exists():
            print(f"  {loc}: FILE MISSING ({loc_file})")
            continue
        loc_data = load_json(loc_file)
        missing = find_missing(en_data, loc_data)
        if not missing:
            print(f"  {loc}: complete (0 missing)")
            continue

        if args.all or args.locale:
            # The "replace_scalar_with_dict" conflict policy preserves the
            # historical TYPE-CONFLICT HANDLING: when en has a dict at a
            # key but locale has a scalar (e.g. en has
            # ``models.download: {resume: ...}`` but locale has
            # ``models.download: "Descargar"``), the scalar is replaced
            # with the dict (using English values). This happens when a
            # previously-scalar key is promoted to a nested object in
            # en.json. Existing scalar translations are NEVER overwritten.
            loc_data, added = merge_en_into_locale(en_data, loc_data, on_conflict="replace_scalar_with_dict")
            save_json(loc_file, loc_data)
            print(f"  {loc}: added {len(added)} missing keys -> {loc_file.name}")
            any_added = True
        else:
            print(f"  {loc}: {len(missing)} missing keys (use --all to add)")
            any_added = True

    if any_added and not (args.all or args.locale):
        print("\nRe-run with --all to add the missing keys with English values.")
        return 1
    if args.all or args.locale:
        return 1 if any_added else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
