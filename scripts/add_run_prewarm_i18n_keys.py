#!/usr/bin/env python3
"""Task 3: add 'Run Prewarm Now' i18n keys to all locale files.

Adds the following keys to the ``about`` section of every translation
JSON file:

  runPrewarmNow       — button label ("Run Prewarm Now")
  prewarmStarting     — toast: prewarm started ("Prewarm started…")
  prewarmComplete     — toast: prewarm finished successfully
  prewarmFailed       — toast: prewarm failed to start
  prewarmAlreadyHot   — toast: cache already hot (button was disabled but
                         user somehow clicked it)
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/z/my-project/voice-typer")
I18N_DIR = ROOT / "voice_typer" / "client" / "src" / "renderer" / "src" / "i18n" / "translations"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "runPrewarmNow": "Run Prewarm Now",
        "prewarmStarting": "Prewarm started\u2026",
        "prewarmComplete": "Prewarm complete \u2014 cache warmed",
        "prewarmFailed": "Prewarm failed to start",
        "prewarmAlreadyHot": "Cache is already hot \u2014 no need to prewarm",
    },
    "ar": {
        "runPrewarmNow": "\u062a\u0634\u063a\u064a\u0644 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642 \u0627\u0644\u0622\u0646",
        "prewarmStarting": "\u0628\u062f\u0623 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642\u2026",
        "prewarmComplete": "\u0627\u0643\u062a\u0645\u0644 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642 \u2014 \u062a\u0645 \u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0630\u0627\u0643\u0631\u0629 \u0627\u0644\u0645\u062e\u0628\u0623\u0629",
        "prewarmFailed": "\u0641\u0634\u0644 \u062a\u0634\u063a\u064a\u0644 \u0627\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642",
        "prewarmAlreadyHot": "\u0627\u0644\u0630\u0627\u0643\u0631\u0629 \u0627\u0644\u0645\u062e\u0628\u0623\u0629 \u0633\u0627\u062e\u0646\u0629 \u0628\u0627\u0644\u0641\u0639\u0644 \u2014 \u0644\u0627 \u062d\u0627\u062c\u0629 \u0644\u0644\u062a\u0633\u062e\u064a\u0646 \u0627\u0644\u0645\u0633\u0628\u0642",
    },
    "de": {
        "runPrewarmNow": "Pr\u00e4warming jetzt starten",
        "prewarmStarting": "Pr\u00e4warming gestartet\u2026",
        "prewarmComplete": "Pr\u00e4warming abgeschlossen \u2014 Cache gew\u00e4rmt",
        "prewarmFailed": "Pr\u00e4warming konnte nicht gestartet werden",
        "prewarmAlreadyHot": "Cache ist bereits hei\u00df \u2014 kein Pr\u00e4warming n\u00f6tig",
    },
    "es": {
        "runPrewarmNow": "Ejecutar precalentamiento ahora",
        "prewarmStarting": "Precalentamiento iniciado\u2026",
        "prewarmComplete": "Precalentamiento completado \u2014 cach\u00e9 calentada",
        "prewarmFailed": "No se pudo iniciar el precalentamiento",
        "prewarmAlreadyHot": "La cach\u00e9 ya est\u00e1 caliente \u2014 no es necesario precalentar",
    },
    "fr": {
        "runPrewarmNow": "Lancer le pr\u00e9chauffage maintenant",
        "prewarmStarting": "Pr\u00e9chauffage d\u00e9marr\u00e9\u2026",
        "prewarmComplete": "Pr\u00e9chauffage termin\u00e9 \u2014 cache r\u00e9chauff\u00e9e",
        "prewarmFailed": "\u00c9chec du d\u00e9marrage du pr\u00e9chauffage",
        "prewarmAlreadyHot": "Le cache est d\u00e9j\u00e0 chaud \u2014 pr\u00e9chauffage inutile",
    },
    "hi": {
        "runPrewarmNow": "\u0905\u092d\u0940 \u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u091a\u0932\u093e\u090f\u0902",
        "prewarmStarting": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0936\u0941\u0930\u0942 \u0939\u0941\u0906\u2026",
        "prewarmComplete": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u092a\u0942\u0930\u094d\u0923 \u2014 \u0915\u0948\u091a \u0917\u0930\u094d\u092e \u0939\u094b \u0917\u092f\u093e",
        "prewarmFailed": "\u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0936\u0941\u0930\u0942 \u0915\u0930\u0928\u0947 \u092e\u0947\u0902 \u0935\u093f\u092b\u0932",
        "prewarmAlreadyHot": "\u0915\u0948\u091a \u092a\u0939\u0932\u0947 \u0938\u0947 \u0917\u0930\u094d\u092e \u0939\u0948 \u2014 \u092a\u094d\u0930\u0940\u0935\u093e\u0930\u094d\u092e \u0915\u0940 \u0906\u0935\u0936\u094d\u092f\u0915\u0924\u093e \u0928\u0939\u0940\u0902",
    },
    "ru": {
        "runPrewarmNow": "\u0417\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432 \u0441\u0435\u0439\u0447\u0430\u0441",
        "prewarmStarting": "\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432 \u0437\u0430\u043f\u0443\u0449\u0435\u043d\u2026",
        "prewarmComplete": "\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d \u2014 \u043a\u044d\u0448 \u0440\u0430\u0437\u043e\u0433\u0440\u0435\u0442",
        "prewarmFailed": "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432",
        "prewarmAlreadyHot": "\u041a\u044d\u0448 \u0443\u0436\u0435 \u0433\u043e\u0440\u044f\u0447\u0438\u0439 \u2014 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0433\u0440\u0435\u0432 \u043d\u0435 \u043d\u0443\u0436\u0435\u043d",
    },
    "zh": {
        "runPrewarmNow": "\u7acb\u5373\u8fd0\u884c\u9884\u70ed",
        "prewarmStarting": "\u9884\u70ed\u5df2\u5f00\u59cb\u2026",
        "prewarmComplete": "\u9884\u70ed\u5b8c\u6210 \u2014 \u7f13\u5b58\u5df2\u52a0\u70ed",
        "prewarmFailed": "\u9884\u70ed\u542f\u52a8\u5931\u8d25",
        "prewarmAlreadyHot": "\u7f13\u5b58\u5df2\u662f\u70ed\u72b6\u6001 \u2014 \u65e0\u9700\u9884\u70ed",
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
