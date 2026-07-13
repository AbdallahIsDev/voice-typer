#!/usr/bin/env python3
"""Task 2: add 'View prewarm log' i18n keys to all locale files.

Adds the following keys to the ``about`` section of every translation
JSON file:

  viewPrewarmLog      — button label ("View prewarm log")
  prewarmLogNotFound  — toast: log file doesn't exist ("No prewarm log found")
  prewarmLogOpened    — toast: log opened successfully
  prewarmLogOpenFailed — toast: OS couldn't open the file
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/z/my-project/voice-typer")
I18N_DIR = ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "viewPrewarmLog": "View prewarm log",
        "prewarmLogNotFound": "No prewarm log found",
        "prewarmLogOpened": "Opened prewarm log",
        "prewarmLogOpenFailed": "Could not open prewarm log",
    },
    "ar": {
        "viewPrewarmLog": "\u0639\u0631\u0636 \u0633\u062c\u0644 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 "
        "\u0627\u0644\u0645\u0633\u0628\u0642",
        "prewarmLogNotFound": "\u0644\u0627 \u064a\u0648\u062c\u062f \u0633\u062c\u0644 "
        "\u062a\u0633\u062e\u064a\u0646 \u0645\u0633\u0628\u0642",
        "prewarmLogOpened": "\u062a\u0645 \u0641\u062a\u062d \u0633\u062c\u0644 "
        "\u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642",
        "prewarmLogOpenFailed": "\u062a\u0639\u0630\u0631 \u0641\u062a\u062d \u0633\u062c\u0644 "
        "\u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642",
    },
    "de": {
        "viewPrewarmLog": "Pr\u00e4warming-Protokoll anzeigen",
        "prewarmLogNotFound": "Kein Pr\u00e4warming-Protokoll gefunden",
        "prewarmLogOpened": "Pr\u00e4warming-Protokoll ge\u00f6ffnet",
        "prewarmLogOpenFailed": "Pr\u00e4warming-Protokoll konnte nicht ge\u00f6ffnet werden",
    },
    "es": {
        "viewPrewarmLog": "Ver registro de precalentamiento",
        "prewarmLogNotFound": "No se encontr\u00f3 registro de precalentamiento",
        "prewarmLogOpened": "Registro de precalentamiento abierto",
        "prewarmLogOpenFailed": "No se pudo abrir el registro de precalentamiento",
    },
    "fr": {
        "viewPrewarmLog": "Voir le journal de pr\u00e9chauffage",
        "prewarmLogNotFound": "Aucun journal de pr\u00e9chauffage trouv\u00e9",
        "prewarmLogOpened": "Journal de pr\u00e9chauffage ouvert",
        "prewarmLogOpenFailed": "Impossible d'ouvrir le journal de pr\u00e9chauffage",
    },
    "hi": {
        "viewPrewarmLog": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0932\u0949\u0917 "
        "\u0926\u0947\u0916\u0947\u0902",
        "prewarmLogNotFound": "\u0915\u094b\u0908 \u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e "
        "\u0932\u0949\u0917 \u0928\u0939\u0940\u0902 \u092e\u093f\u0932\u093e",
        "prewarmLogOpened": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0932\u0949\u0917 "
        "\u0916\u0941\u0932\u093e",
        "prewarmLogOpenFailed": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0932\u0949\u0917 "
        "\u0928\u0939\u0940\u0902 \u0916\u0941\u0932 \u0938\u0915\u093e",
    },
    "ru": {
        "viewPrewarmLog": "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c "
        "\u0436\u0443\u0440\u043d\u0430\u043b \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430",
        "prewarmLogNotFound": "\u0416\u0443\u0440\u043d\u0430\u043b "
        "\u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430 \u043d\u0435 "
        "\u043d\u0430\u0439\u0434\u0435\u043d",
        "prewarmLogOpened": "\u0416\u0443\u0440\u043d\u0430\u043b "
        "\u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430 \u043e\u0442\u043a\u0440\u044b\u0442",
        "prewarmLogOpenFailed": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c "
        "\u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0436\u0443\u0440\u043d\u0430\u043b "
        "\u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430",
    },
    "zh": {
        "viewPrewarmLog": "\u67e5\u770b\u9884\u70ed\u65e5\u5fd7",
        "prewarmLogNotFound": "\u672a\u627e\u5230\u9884\u70ed\u65e5\u5fd7",
        "prewarmLogOpened": "\u5df2\u6253\u5f00\u9884\u70ed\u65e5\u5fd7",
        "prewarmLogOpenFailed": "\u65e0\u6cd5\u6253\u5f00\u9884\u70ed\u65e5\u5fd7",
    },
}


def add_keys_to_locale(locale: str, keys: dict[str, str]) -> bool:
    path = I18N_DIR / f"{locale}.json"
    if not path.exists():
        print(f"[i18n] SKIP {locale}: file not found at {path}")
        return False
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    about = data.setdefault("about", {})
    modified = False
    for k, v in keys.items():
        if about.get(k) != v:
            about[k] = v
            modified = True
    if modified:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent="\t")
            f.write("\n")
        print(f"[i18n] UPDATED {locale}: {len(keys)} keys checked/added")
    else:
        print(f"[i18n] OK {locale}: all keys already present")
    return modified


def main() -> int:
    print(f"[i18n] Locale dir: {I18N_DIR}")
    total = 0
    for locale, keys in TRANSLATIONS.items():
        if add_keys_to_locale(locale, keys):
            total += 1
    print(f"[i18n] Done. {total} file(s) modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
