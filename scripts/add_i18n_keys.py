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
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "voice_typer/client/src/renderer/src/i18n/translations"
EN_FILE = TRANSLATIONS_DIR / "en.json"
NON_ENGLISH_LOCALES = ["ar", "de", "es", "fr", "hi", "ru", "zh"]


def flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    """Recursively collect all dot-separated keys from a nested dict."""
    keys: set[str] = set()
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= flatten_keys(v, full)
        else:
            keys.add(full)
    return keys


def find_missing(en_data: dict, locale_data: dict) -> set[str]:
    """Return the set of keys present in en_data but missing from locale_data."""
    en_keys = flatten_keys(en_data)
    locale_keys = flatten_keys(locale_data)
    return en_keys - locale_keys


def add_missing_keys(en_data: dict, locale_data: dict) -> tuple[dict, set[str]]:
    """Recursively add missing keys from en_data into locale_data (in place).

    Returns the (possibly modified) locale_data and the set of added keys.

    TYPE-CONFLICT HANDLING: when en_data has a dict at a key but locale_data
    has a scalar (e.g. en has ``models.download: {resume: ...}`` but locale
    has ``models.download: "Descargar"``), the scalar is replaced with the
    dict (using English values).  This happens when a previously-scalar key
    is promoted to a nested object in en.json.  The replaced scalar is
    logged as an added key.
    """
    added: set[str] = set()

    def _recurse(en_sub: dict, loc_sub: dict, prefix: str) -> None:
        for k, v in en_sub.items():
            full = f"{prefix}.{k}" if prefix else k
            if k not in loc_sub:
                loc_sub[k] = v
                added.add(full)
            elif isinstance(v, dict) and isinstance(loc_sub[k], dict):
                _recurse(v, loc_sub[k], full)
            elif isinstance(v, dict) and not isinstance(loc_sub[k], dict):
                # Type conflict: en has a dict, locale has a scalar.
                # Replace the scalar with the dict (English values).
                # This happens when a scalar key is promoted to a nested object.
                loc_sub[k] = v
                added.add(full)
            # else: both are scalars — never overwrite the existing translation

    _recurse(en_data, locale_data, "")
    return locale_data, added


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent="\t", ensure_ascii=False)
        f.write("\n")


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
            loc_data, added = add_missing_keys(en_data, loc_data)
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
