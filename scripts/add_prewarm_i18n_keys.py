#!/usr/bin/env python3
"""ADR-0009 Issue 3: add prewarm cache status i18n keys to all locale files.

Adds the following keys to the ``about`` section of every translation
JSON file in voice_typer/client/src/renderer/src/i18n/translations/:

  cacheTitle            — section heading ("Cache Status")
  cacheDescription      — section description
  prewarmStatus         — row label ("Prewarm Status")
  lastRun               — row label ("Last Run")
  cacheHealth           — row label ("Cache Health")
  prewarmElapsed        — row label ("Elapsed")
  cacheHot              — status value ("Hot")
  cachePartial          — status value ("Partial")
  cacheCold             — status value ("Cold")
  cacheUnknown          — status value ("Unknown")
  cacheRunning          — status value ("Running…")
  refreshCacheStatus    — button label ("Refresh")
  neverRun              — last-run placeholder ("Never")

Translations are best-effort (native scripts for ar/de/es/fr/hi/ru/zh);
the English source is the canonical reference.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/z/my-project/voice-typer")
I18N_DIR = ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations"

# Per-locale translations. English is the source of truth; the others
# are best-effort native translations.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "cacheTitle": "Cache Status",
        "cacheDescription": "Live state of the OS file cache for the speech model. Prewarm runs at boot to keep the "
        "model files in RAM for fast startup.",
        "prewarmStatus": "Prewarm Status",
        "lastRun": "Last Run",
        "cacheHealth": "Cache Health",
        "prewarmElapsed": "Elapsed",
        "cacheHot": "Hot",
        "cachePartial": "Partial",
        "cacheCold": "Cold",
        "cacheUnknown": "Unknown",
        "cacheRunning": "Running\u2026",
        "refreshCacheStatus": "Refresh",
        "neverRun": "Never",
    },
    "ar": {
        "cacheTitle": "\u062d\u0627\u0644\u0629 \u0627\u0644\u0630\u0627\u0643\u0631\u0629 "
        "\u0627\u0644\u0645\u062e\u0628\u0623\u0629",
        "cacheDescription": "\u0627\u0644\u062d\u0627\u0644\u0629 \u0627\u0644\u062d\u0627\u0644\u064a\u0629 "
        "\u0644\u0630\u0627\u0643\u0631\u0629 \u0627\u0644\u0645\u062e\u0628\u0623\u0629 "
        "\u0644\u0646\u0638\u0627\u0645 \u0627\u0644\u062a\u0634\u063a\u064a\u0644 "
        "\u0644\u0644\u0646\u0645\u0648\u0630\u062c. \u064a\u0639\u0645\u0644 "
        "\u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642 \u0639\u0646\u062f "
        "\u0627\u0644\u062a\u0634\u063a\u064a\u0644 \u0644\u0625\u0628\u0642\u0627\u0621 "
        "\u0645\u0644\u0641\u0627\u062a \u0627\u0644\u0646\u0645\u0648\u0630\u062c \u0641\u064a "
        "\u0627\u0644\u0630\u0627\u0643\u0631\u0629 \u0644\u0628\u062f\u0621 \u0633\u0631\u064a\u0639.",
        "prewarmStatus": "\u062d\u0627\u0644\u0629 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 "
        "\u0627\u0644\u0645\u0633\u0628\u0642",
        "lastRun": "\u0622\u062e\u0631 \u062a\u0634\u063a\u064a\u0644",
        "cacheHealth": "\u0635\u062d\u0629 \u0627\u0644\u0630\u0627\u0643\u0631\u0629 "
        "\u0627\u0644\u0645\u062e\u0628\u0623\u0629",
        "prewarmElapsed": "\u0627\u0644\u0645\u062f\u0629 \u0627\u0644\u0645\u0646\u0642\u0636\u064a\u0629",
        "cacheHot": "\u0633\u0627\u062e\u0646",
        "cachePartial": "\u062c\u0632\u0626\u064a",
        "cacheCold": "\u0628\u0627\u0631\u062f",
        "cacheUnknown": "\u063a\u064a\u0631 \u0645\u0639\u0631\u0648\u0641",
        "cacheRunning": "\u0642\u064a\u062f \u0627\u0644\u062a\u0634\u063a\u064a\u0644\u2026",
        "refreshCacheStatus": "\u062a\u062d\u062f\u064a\u062b",
        "neverRun": "\u0623\u0628\u062f\u064b\u0627",
    },
    "de": {
        "cacheTitle": "Cache-Status",
        "cacheDescription": "Live-Zustand des Dateicaches f\u00fcr das Sprachmodell. Prewarm l\u00e4uft beim Booten, "
        "um die Modelldateien f\u00fcr einen schnellen Start im RAM zu halten.",
        "prewarmStatus": "Prewarm-Status",
        "lastRun": "Letzte Ausf\u00fchrung",
        "cacheHealth": "Cache-Gesundheit",
        "prewarmElapsed": "Dauer",
        "cacheHot": "Hei\u00df",
        "cachePartial": "Teilweise",
        "cacheCold": "Kalt",
        "cacheUnknown": "Unbekannt",
        "cacheRunning": "L\u00e4uft\u2026",
        "refreshCacheStatus": "Aktualisieren",
        "neverRun": "Nie",
    },
    "es": {
        "cacheTitle": "Estado de la cach\u00e9",
        "cacheDescription": "Estado en vivo de la cach\u00e9 del sistema para el modelo de voz. El precalentamiento "
        "se ejecuta en el arranque para mantener los archivos del modelo en RAM para un inicio r\u00e1pido.",
        "prewarmStatus": "Estado del precalentamiento",
        "lastRun": "\u00daltima ejecuci\u00f3n",
        "cacheHealth": "Salud de la cach\u00e9",
        "prewarmElapsed": "Duraci\u00f3n",
        "cacheHot": "Caliente",
        "cachePartial": "Parcial",
        "cacheCold": "Fr\u00edo",
        "cacheUnknown": "Desconocido",
        "cacheRunning": "Ejecut\u00e1ndose\u2026",
        "refreshCacheStatus": "Actualizar",
        "neverRun": "Nunca",
    },
    "fr": {
        "cacheTitle": "\u00c9tat du cache",
        "cacheDescription": "\u00c9tat en direct du cache de fichiers du syst\u00e8me pour le mod\u00e8le vocal. Le "
        "pr\u00e9chauffage s\u2019ex\u00e9cute au d\u00e9marrage pour garder les fichiers du mod\u00e8le en RAM pour "
        "un lancement rapide.",
        "prewarmStatus": "\u00c9tat du pr\u00e9chauffage",
        "lastRun": "Derni\u00e8re ex\u00e9cution",
        "cacheHealth": "Sant\u00e9 du cache",
        "prewarmElapsed": "Dur\u00e9e",
        "cacheHot": "Chaud",
        "cachePartial": "Partiel",
        "cacheCold": "Froid",
        "cacheUnknown": "Inconnu",
        "cacheRunning": "En cours\u2026",
        "refreshCacheStatus": "Actualiser",
        "neverRun": "Jamais",
    },
    "hi": {
        "cacheTitle": "\u0915\u0948\u091a \u0938\u094d\u0925\u093f\u0924\u093f",
        "cacheDescription": "\u0935\u0949\u092f\u093f\u0938 \u092e\u0949\u0921\u0932 \u0915\u0947 \u0932\u093f\u090f "
        "\u0913\u0935\u0930 \u092b\u093c\u093e\u0907\u0932 \u0915\u0948\u091a \u0915\u0940 \u0932\u093e\u0907\u0935 "
        "\u0938\u094d\u0925\u093f\u0924\u093f\u0964 \u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e "
        "\u092c\u0942\u091f \u092a\u0930 \u091a\u0932\u0924\u093e \u0939\u0948 \u0924\u093e\u0915\u093f "
        "\u092e\u0949\u0921\u0932 \u092b\u093c\u093e\u0907\u0932\u0947\u0902 \u0924\u0947\u091c "
        "\u0936\u0941\u0930\u0942\u0906\u0924 \u0915\u0947 \u0932\u093f\u090f RAM \u092e\u0947\u0902 "
        "\u0930\u0939\u0947\u0902\u0964",
        "prewarmStatus": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0938\u094d\u0925\u093f\u0924\u093f",
        "lastRun": "\u0905\u0902\u0924\u093f\u092e \u091a\u0932\u093e\u0928\u093e",
        "cacheHealth": "\u0915\u0948\u091a \u0938\u094d\u0935\u093e\u0938\u094d\u0925\u094d\u092f",
        "prewarmElapsed": "\u0905\u0935\u0927\u093f",
        "cacheHot": "\u0917\u0930\u094d\u092e",
        "cachePartial": "\u0906\u0902\u0936\u093f\u0915",
        "cacheCold": "\u0920\u0902\u0921\u093e",
        "cacheUnknown": "\u0905\u091c\u094d\u091e\u093e\u0924",
        "cacheRunning": "\u091a\u0932 \u0930\u0939\u093e \u0939\u0948\u2026",
        "refreshCacheStatus": "\u0930\u093f\u092b\u094d\u0930\u0947\u0936",
        "neverRun": "\u0915\u092d\u0940 \u0928\u0939\u0940\u0902",
    },
    "ru": {
        "cacheTitle": "\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 \u043a\u044d\u0448\u0430",
        "cacheDescription": "\u0416\u0438\u0432\u043e\u0435 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 "
        "\u0444\u0430\u0439\u043b\u043e\u0432\u043e\u0433\u043e \u043a\u044d\u0448\u0430 \u041e\u0421 "
        "\u0434\u043b\u044f \u0440\u0435\u0447\u0435\u0432\u043e\u0439 \u043c\u043e\u0434\u0435\u043b\u0438. "
        "\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432 "
        "\u0437\u0430\u043f\u0443\u0441\u043a\u0430\u0435\u0442\u0441\u044f \u043f\u0440\u0438 "
        "\u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0435, \u0447\u0442\u043e\u0431\u044b "
        "\u0444\u0430\u0439\u043b\u044b \u043c\u043e\u0434\u0435\u043b\u0438 "
        "\u043e\u0441\u0442\u0430\u0432\u0430\u043b\u0438\u0441\u044c \u0432 RAM \u0434\u043b\u044f "
        "\u0431\u044b\u0441\u0442\u0440\u043e\u0433\u043e \u0437\u0430\u043f\u0443\u0441\u043a\u0430.",
        "prewarmStatus": "\u0421\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u0435 "
        "\u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432\u0430",
        "lastRun": "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0437\u0430\u043f\u0443\u0441\u043a",
        "cacheHealth": "\u0417\u0434\u043e\u0440\u043e\u0432\u044c\u0435 \u043a\u044d\u0448\u0430",
        "prewarmElapsed": "\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c",  # noqa: E501
        "cacheHot": "\u0413\u043e\u0440\u044f\u0447\u0438\u0439",
        "cachePartial": "\u0427\u0430\u0441\u0442\u0438\u0447\u043d\u043e",
        "cacheCold": "\u0425\u043e\u043b\u043e\u0434\u043d\u044b\u0439",
        "cacheUnknown": "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e",
        "cacheRunning": "\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f\u2026",
        "refreshCacheStatus": "\u041e\u0431\u043d\u043e\u0432\u0438\u0442\u044c",
        "neverRun": "\u041d\u0438\u043a\u043e\u0433\u0434\u0430",
    },
    "zh": {
        "cacheTitle": "\u7f13\u5b58\u72b6\u6001",
        "cacheDescription": "\u8bed\u97f3\u6a21\u578b\u7684\u64cd\u4f5c\u7cfb\u7edf\u6587\u4ef6\u7f13\u5b58\u5b9e\u65f6\u72b6\u6001\u3002\u9884\u70ed\u5728\u5f00\u673a\u65f6\u8fd0\u884c\uff0c\u5c06\u6a21\u578b\u6587\u4ef6\u4fdd\u7559\u5728 "  # noqa: E501
        "RAM \u4e2d\u4ee5\u5b9e\u73b0\u5feb\u901f\u542f\u52a8\u3002",
        "prewarmStatus": "\u9884\u70ed\u72b6\u6001",
        "lastRun": "\u4e0a\u6b21\u8fd0\u884c",
        "cacheHealth": "\u7f13\u5b58\u5065\u5eb7\u5ea6",
        "prewarmElapsed": "\u8017\u65f6",
        "cacheHot": "\u70ed",
        "cachePartial": "\u90e8\u5206",
        "cacheCold": "\u51b7",
        "cacheUnknown": "\u672a\u77e5",
        "cacheRunning": "\u8fd0\u884c\u4e2d\u2026",
        "refreshCacheStatus": "\u5237\u65b0",
        "neverRun": "\u4ece\u672a",
    },
}


def add_keys_to_locale(locale: str, keys: dict[str, str]) -> bool:
    """Add the given keys to the ``about`` section of the locale file.

    Returns True if the file was modified, False if all keys were
    already present with the same values.
    """
    path = I18N_DIR / f"{locale}.json"
    if not path.exists():
        print(f"[i18n] SKIP {locale}: file not found at {path}")
        return False

    # Read with preserve order (Python 3.7+ dicts preserve insertion
    # order; json.load respects this).
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    about = data.setdefault("about", {})
    modified = False
    for k, v in keys.items():
        if about.get(k) != v:
            about[k] = v
            modified = True

    if modified:
        # Write with ensure_ascii=False so the native scripts render
        # readably in the JSON file (the existing files use this style).
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
