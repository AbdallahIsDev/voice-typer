"""Backfill missing i18n keys into all non-English locale files.

The recent RW-01 (keyring) and §3.2-§3.7 (punctuation cheat sheet) sub-agents
added new English keys but didn't update the other 7 locale files. This script
copies the English values as placeholders — translators can refine later.

Run: python scripts/backfill_i18n_keys.py
"""

from __future__ import annotations

import json
from pathlib import Path

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


def collect_keys(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out |= collect_keys(v, key + ".")
        else:
            out.add(key)
    return out


def get_path(d: dict, dotted: str):
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    return cur, parts[-1]


def backfill_locale(locale: str, en: dict, en_keys: set[str]) -> tuple[int, int]:
    path = TRANSLATIONS_DIR / f"{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    locale_keys = collect_keys(data)
    missing = en_keys - locale_keys
    if not missing:
        return 0, 0

    def get_en_value(dotted: str):
        cur = en
        for p in dotted.split("."):
            cur = cur[p]
        return cur

    added = 0
    for key in missing:
        parent, leaf = get_path(data, key)
        parent[leaf] = get_en_value(key)
        added += 1

    # Sort keys at every level for stable diffs (matching existing file order).
    def sort_recursive(d):
        if isinstance(d, dict):
            return {k: sort_recursive(v) for k, v in sorted(d.items())}
        return d

    # Preserve existing top-level order — only insert new keys at the end of their parent.
    # To do that, walk the en.json structure and append any missing keys in en's order.
    def merge_in_en_order(en_node, locale_node):
        for k, v in en_node.items():
            if k not in locale_node:
                locale_node[k] = v if not isinstance(v, dict) else json.loads(json.dumps(v))
            elif isinstance(v, dict) and isinstance(locale_node[k], dict):
                merge_in_en_order(v, locale_node[k])

    # Reload original to preserve order, then merge.
    original = json.loads(path.read_text(encoding="utf-8"))
    merge_in_en_order(en, original)
    path.write_text(json.dumps(original, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
    return added, 0


def main() -> int:
    en = json.loads(EN_FILE.read_text(encoding="utf-8"))
    en_keys = collect_keys(en)
    total_added = 0
    for loc in LOCALES:
        added, _ = backfill_locale(loc, en, en_keys)
        if added:
            print(f"  {loc}.json: +{added} keys backfilled")
            total_added += added
        else:
            print(f"  {loc}.json: up to date")
    print(f"Total: {total_added} keys backfilled across {len(LOCALES)} locales")
    return 0 if total_added >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
