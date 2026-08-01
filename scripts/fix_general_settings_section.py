#!/usr/bin/env python3
"""Surgically fix GeneralSettingsSection.tsx to delegate to pushLocaleToPythonBackend.

Removes the local `trayLabelsForLocale` and `TRAY_LABEL_KEY_MAP` (DRY violation —
the canonical copy lives in `voice_typer/client/src/renderer/src/i18n/push.ts`),
imports `pushLocaleToPythonBackend` from `@/i18n/i18n`, and replaces the inline
`window.python?.call({type: "set_tray_locale", ...})` block with a call to
`pushLocaleToPythonBackend(v as Locale)`.

Preserves the file's tab indentation. Fixes 2 pre-existing test failures in
`tests/regressions/electron_test.py::TestSettingsRendererCallsPythonBridgeCall`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGET = Path(
    "/home/z/my-project/voice-typer/voice_typer/client/src/renderer/src/components/settings/GeneralSettingsSection.tsx"
)

src = TARGET.read_text(encoding="utf-8")
T = "\t"  # tab character

# 1. Add `pushLocaleToPythonBackend` to the i18n import (after `setLocale,`).
old_import_lines = [
    "import {",
    f"{T}getLocale,",
    f"{T}getLocaleLabel,",
    f"{T}type Locale,",
    f"{T}SUPPORTED_LOCALES,",
    f"{T}setLocale,",
    f"{T}t,",
    f"{T}useT,",
    '} from "@/i18n/i18n";',
]
old_import = "\n".join(old_import_lines)
new_import_lines = [
    "import {",
    f"{T}getLocale,",
    f"{T}getLocaleLabel,",
    f"{T}type Locale,",
    f"{T}SUPPORTED_LOCALES,",
    f"{T}setLocale,",
    f"{T}pushLocaleToPythonBackend,",
    f"{T}t,",
    f"{T}useT,",
    '} from "@/i18n/i18n";',
]
new_import = "\n".join(new_import_lines)
if old_import not in src:
    print("ERROR: import block not found verbatim (tab-indented)", file=sys.stderr)
    sys.exit(1)
src = src.replace(old_import, new_import, 1)

# 2. Remove the local `TRAY_LABEL_KEY_MAP` constant + `trayLabelsForLocale`
#    function. Use a regex that matches across lines but preserves everything
#    else.
local_block_re = re.compile(
    r"//build a tray-menu label dict for the CURRENT locale\n"
    r"// from the renderer's existing translations\..*?"
    r"function trayLabelsForLocale\(\): Record<string, string> \{\n"
    r"\tconst labels: Record<string, string> = \{\};\n"
    r"\tfor \(const \[trayKey, i18nKey\] of Object\.entries\(TRAY_LABEL_KEY_MAP\)\) \{\n"
    r"\t\tconst translated = t\(i18nKey\);\n"
    r"\t\t// `t` returns the key itself when the translation is missing;\n"
    r"\t\t// skip those so the server keeps its English default\.\n"
    r"\t\tif \(translated && translated !== i18nKey\) \{\n"
    r"\t\t\tlabels\[trayKey\] = translated;\n"
    r"\t\t\}\n"
    r"\t\}\n"
    r"\treturn labels;\n"
    r"\}\n\n",
    re.DOTALL,
)
m = local_block_re.search(src)
if not m:
    print("ERROR: local trayLabelsForLocale block not found", file=sys.stderr)
    sys.exit(1)
src = src[: m.start()] + src[m.end() :]

# 3. Replace the inline `window.python?.call({type: "set_tray_locale", ...})`
#    block with `pushLocaleToPythonBackend(v as Locale)`. The block is deeply
#    indented (8 tabs). Use a regex that captures the indentation prefix.
inline_block_re = re.compile(
    r"(?P<indent>\t+)//push the locale \(and any tray-menu\n"
    r"(?P=indent)// labels the renderer can translate\) to the Python\n"
    r"(?P=indent)// backend so the tray menu localizes into this locale\.\n"
    r"(?P=indent)// `trayLabelsForLocale` only includes keys the\n"
    r"(?P=indent)// current translation actually defines, so missing\n"
    r"(?P=indent)// keys fall back to English on the server side\.\n"
    r"(?P=indent)try \{\n"
    r"(?P=indent)\tvoid window\.python\?\.call\(\{\n"
    r"(?P=indent)\t\ttype: \"set_tray_locale\",\n"
    r"(?P=indent)\t\tdata: \{\n"
    r"(?P=indent)\t\t\tlocale: v,\n"
    r"(?P=indent)\t\t\tlabels: trayLabelsForLocale\(\),\n"
    r"(?P=indent)\t\t\},\n"
    r"(?P=indent)\t\}\);\n"
    r"(?P=indent)\} catch \(e\) \{\n"
    r"(?P=indent)\t// IPC may not be available during startup or the\n"
    r"(?P=indent)\t// backend may not yet have registered the route\.\n"
    r"(?P=indent)\tconsole\.warn\(\n"
    r"(?P=indent)\t\t\"\[GeneralSettingsSection\] set_tray_locale IPC failed:\",\n"
    r"(?P=indent)\t\te,\n"
    r"(?P=indent)\t\);\n"
    r"(?P=indent)\}"
)
m = inline_block_re.search(src)
if not m:
    print("ERROR: inline window.python?.call block not found", file=sys.stderr)
    sys.exit(1)
indent = m.group("indent")
replacement = (
    f"{indent}// Delegate tray-locale dispatch to the i18n module's\n"
    f"{indent}// `pushLocaleToPythonBackend` helper so this component\n"
    f"{indent}// does not call `window.python?.call(...)` directly\n"
    f"{indent}// (the PythonBridge type only exposes `call` and\n"
    f"{indent}// `onEvent` — direct calls bypass the i18n contract\n"
    f"{indent}// and re-introduce the delegation-boundary violation).\n"
    f"{indent}try {{\n"
    f"{indent}\tpushLocaleToPythonBackend(v as Locale);\n"
    f"{indent}}} catch (e) {{\n"
    f"{indent}\t// IPC may not be available during startup or the\n"
    f"{indent}\t// backend may not yet have registered the route.\n"
    f"{indent}\tconsole.warn(\n"
    f'{indent}\t\t"[GeneralSettingsSection] set_tray_locale IPC failed:",\n'
    f"{indent}\t\te,\n"
    f"{indent}\t);\n"
    f"{indent}}}"
)
src = src[: m.start()] + replacement + src[m.end() :]

TARGET.write_text(src, encoding="utf-8")
print(f"OK: {TARGET} patched")
print("  - Added pushLocaleToPythonBackend to @/i18n/i18n import")
print("  - Removed local TRAY_LABEL_KEY_MAP + trayLabelsForLocale (DRY violation)")
print("  - Replaced inline window.python?.call(...) with pushLocaleToPythonBackend(v)")
