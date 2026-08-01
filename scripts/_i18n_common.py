"""Shared helpers for the i18n maintenance scripts.

the three i18n scripts (``add_i18n_keys.py``,
``backfill_i18n_keys.py``, ``apply_translations.py``) each implemented
their own ``flatten_keys`` / ``collect_keys``, ``load_json``,
``save_json``, and a locale-merge routine. The duplicated contract was
~150 LOC across the three scripts and had already drifted slightly
(``flatten_keys`` vs ``collect_keys`` produce the same output but use
different recursion styles; ``add_missing_keys`` replaces scalars with
dicts on type-conflict while ``merge_in_en_order`` does not).

This module provides the canonical implementations so all three scripts
share one source of truth:

  - ``flatten_keys``      — collect every dot-separated key from a
                            nested dict (returns a ``set[str]``).
  - ``load_json``         — read a JSON file as UTF-8.
  - ``save_json``         — write a JSON file with ``indent="\\t"``,
                            ``ensure_ascii=False``, and a trailing
                            newline (matches the existing files' format
                            so diffs stay minimal).
  - ``merge_en_into_locale`` — recursively add missing keys from
                            ``en`` into ``locale`` with an explicit
                            conflict policy.
"""

from __future__ import annotations

import json
from pathlib import Path


def flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    """Recursively collect all dot-separated keys from a nested dict.

    e.g. ``{"app": {"name": "Voice Typer", "version": 1}}`` →
    ``{"app.name", "app.version"}``.

    Non-dict values (strings, numbers, booleans, nulls) all produce a
    leaf key — the value type is irrelevant for the key-set membership
    checks the scripts perform.
    """
    keys: set[str] = set()
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= flatten_keys(v, full)
        else:
            keys.add(full)
    return keys


def load_json(path: Path) -> dict:
    """Read a JSON file as UTF-8 and return the parsed dict."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    """Write ``data`` to ``path`` as JSON with tab indentation.

    The format matches the existing locale files: ``indent="\\t"``,
    ``ensure_ascii=False`` (so non-ASCII glyphs are written literally,
    not as ``\\uXXXX`` escapes), and a single trailing newline so the
    file passes the ``ends-with-newline`` lint rule.
    """
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent="\t", ensure_ascii=False)
        f.write("\n")


def merge_en_into_locale(
    en: dict,
    locale: dict,
    on_conflict: str = "skip",
) -> tuple[dict, set[str]]:
    """Recursively merge ``en`` into ``locale`` (in place), adding
    missing keys.

    Returns ``(locale, added)`` where ``added`` is the set of
    dot-separated keys that were copied from ``en`` into ``locale``
    (either because they were missing, or because a type-conflict
    replacement happened).

    Conflict policies (what to do when a key exists in BOTH ``en`` and
    ``locale``):

      - ``"skip"`` (default): leave the existing locale value untouched.
        Used by ``backfill_i18n_keys`` — translators' work is never
        clobbered. Note: when ``en`` has a dict and ``locale`` has a
        scalar at the same key (e.g. a scalar key was promoted to a
        nested object in en.json), the scalar is left in place under
        this policy — the nested en keys are NOT added.
      - ``"overwrite"``: replace the locale value with the en value.
        Used when a key was renamed and the old translation should be
        discarded.
      - ``"replace_scalar_with_dict"``: when ``en`` has a dict and
        ``locale`` has a scalar at the same key, replace the scalar
        with the en dict (using English values). This is the
        ``add_i18n_keys`` behavior — it handles the case where a
        previously-scalar key was promoted to a nested object in
        en.json. Scalar-vs-scalar conflicts are still skipped (existing
        translations are preserved).

    For both-scalar conflicts under any policy, the existing locale
    translation is preserved (``"overwrite"`` is the only exception —
    it replaces scalars too).
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
                # en has a dict, locale has a scalar at the same key.
                if on_conflict in ("overwrite", "replace_scalar_with_dict"):
                    loc_sub[k] = v
                    added.add(full)
                # else: "skip" — leave the scalar in place.
            elif not isinstance(v, dict) and isinstance(loc_sub[k], dict):
                # en has a scalar, locale has a dict at the same key.
                if on_conflict == "overwrite":
                    loc_sub[k] = v
                    added.add(full)
                # else: "skip" / "replace_scalar_with_dict" — leave the
                # locale dict in place (the locale has richer structure).
            else:
                # Both scalars (or both non-dict values).
                if on_conflict == "overwrite":
                    loc_sub[k] = v
                    added.add(full)
                # else: skip — preserve the existing translation.

    _recurse(en, locale, "")
    return locale, added
