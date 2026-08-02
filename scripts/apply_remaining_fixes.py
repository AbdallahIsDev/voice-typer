#!/usr/bin/env python3
"""Apply remaining Dashboard config-dir display and theme-dedup fixes."""
import re
from pathlib import Path

REPO = Path("/home/z/my-project/voice-typer")

# ============================================================
# useDashboardData.ts — add configDir
# ============================================================
f = REPO / "voice_typer/client/src/renderer/src/pages/dashboard/hooks/useDashboardData.ts"
src = f.read_text()

# Add configDir to the result interface
src = src.replace(
    "\tconfigRaw: VoiceTyperConfig | null;\n",
    "\tconfigRaw: VoiceTyperConfig | null;\n"
    "\t/** Backend config directory (from get_status) for the data-path display. */\n"
    "\tconfigDir: string;\n",
    1,
)

# Add configDir state after configRaw state
src = src.replace(
    "const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);",
    "const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);\n"
    "\tconst [configDir, setConfigDir] = useState<string>(\"\");",
    1,
)

# Add get_status to the Promise.all
src = src.replace(
    'call<{ count: number }>("get_history_count").catch(() => ({\n'
    "\t\t\t\t\t\tcount: 0,\n"
    "\t\t\t\t\t})),\n"
    "\t\t\t]);",
    'call<{ count: number }>("get_history_count").catch(() => ({\n'
    "\t\t\t\t\t\tcount: 0,\n"
    "\t\t\t\t\t})),\n"
    "\t\t\t\tcall<{ config_dir?: string }>(\"get_status\").catch(() => null),\n"
    "\t\t\t]);",
    1,
)

# Destructure the new status result from Promise.all
# The Promise.all returns [cfg, todayStats, history, totalCount, status]
src = src.replace(
    "const [cfg, todayStats, history, totalCount] = await Promise.all([",
    "const [cfg, todayStats, history, totalCount, status] = await Promise.all([",
    1,
)

# Set configDir from status
src = src.replace(
    "setConfigRaw(cfg ?? null);",
    "setConfigRaw(cfg ?? null);\n\t\t\tif (status?.config_dir) setConfigDir(status.config_dir);",
    1,
)

# Add configDir to the return object
# Find the return statement and add configDir
src = src.replace(
    "\treturn {\n\t\tdata,\n\t\tconfigRaw,",
    "\treturn {\n\t\tdata,\n\t\tconfigRaw,\n\t\tconfigDir,",
    1,
)

f.write_text(src)
print(f"Updated: {f.name}")

# ============================================================
# SevenDayActivityChart.tsx — tChoice migration
# ============================================================
f = REPO / "voice_typer/client/src/renderer/src/pages/dashboard/components/SevenDayActivityChart.tsx"
src = f.read_text()

# Check current imports
if "tChoice" not in src:
    # Replace t import with t + tChoice
    src = src.replace(
        'import { t } from "@/i18n/i18n";',
        'import { t, tChoice } from "@/i18n/i18n";',
    )
    # If no t import exists, add it
    if 'import { t, tChoice }' not in src:
        # Try alternate import pattern
        for pat in ['import { t } from', 'import {t} from']:
            if pat in src:
                src = src.replace(pat, 'import { t, tChoice } from')
                break

# Replace the binary plural pattern with tChoice
# Pattern: t("analytics.dayCountTooltipSingular", { count: ... }) : t("analytics.dayCountTooltipPlural", { count: ... })
old_pattern = (
    r't\("analytics\.dayCountTooltipSingular",\s*\{[^}]+\}\)\s*\?\s*\n?\s*:'
    r'\s*t\("analytics\.dayCountTooltipPlural",\s*\{[^}]+\}\)'
)
# Simpler: just replace the two t() calls with a single tChoice
src = re.sub(
    r't\("analytics\.dayCountTooltipSingular",\s*(\{[^}]+\})\)\s*\n?\s*:\s*t\("analytics\.dayCountTooltipPlural",\s*\{[^}]+\}\)',
    lambda m: 'tChoice("analytics.dayCountTooltip", ' + m.group(1).replace('count:', '').strip('{} ').strip() + ')',
    src,
)

# If the above didn't match (different formatting), try a broader approach
if "dayCountTooltipSingular" in src or "dayCountTooltipPlural" in src:
    # Find the ternary and replace
    lines = src.split('\n')
    new_lines = []
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        if "dayCountTooltipSingular" in line:
            # Extract the count value from the line
            # Pattern: ? t("analytics.dayCountTooltipSingular", { count: String(count) })
            count_match = re.search(r'count:\s*String\(([^)]+)\)', line)
            if count_match:
                count_var = count_match.group(1)
                indent = line[:len(line) - len(line.lstrip())]
                new_lines.append(f'{indent}tChoice("analytics.dayCountTooltip", {count_var})')
                # Skip the next line (the : t("...Plural", ...) part)
                if i + 1 < len(lines) and "dayCountTooltipPlural" in lines[i+1]:
                    skip_next = True
                continue
        new_lines.append(line)
    src = '\n'.join(new_lines)

f.write_text(src)
print(f"Updated: {f.name}")

# Verify
src2 = f.read_text()
if "dayCountTooltipSingular" in src2 or "dayCountTooltipPlural" in src2:
    print("  WARNING: SevenDayActivityChart still has old plural keys")
else:
    print("  OK: SevenDayActivityChart migrated to tChoice")

# ============================================================
# theme-bootstrap.ts — use theme-storage-keys imports
# ============================================================
f = REPO / "voice_typer/client/src/renderer/src/theme-bootstrap.ts"
src = f.read_text()

# Check what theme-storage-keys exports
tsk = (REPO / "voice_typer/client/src/renderer/src/lib/theme-storage-keys.ts").read_text()
print("theme-storage-keys.ts exports:")
for line in tsk.split('\n'):
    if 'export' in line.lower() and ('const' in line or 'type' in line or 'function' in line):
        print(f"  {line.strip()}")

# Replace local LS_* consts with imports
# First, add the import
if 'from "@/lib/theme-storage-keys"' not in src:
    # Find a good place to add the import (after existing imports)
    import_section_end = src.find('\n', src.find('import'))
    if import_section_end == -1:
        import_section_end = 0
    # Add after the last import line
    last_import = 0
    for m in re.finditer(r'^import\s.*$', src, re.MULTILINE):
        last_import = m.end()
    src = (
        src[:last_import]
        + '\nimport { LS_THEME_MODE, LS_THEME_PRESET, LS_CUSTOM_THEME } '
        + 'from "@/lib/theme-storage-keys";'
        + src[last_import:]
    )

# Remove the local const declarations
src = re.sub(r'const LS_THEME_MODE\s*=\s*"[^"]+";\n', '', src)
src = re.sub(r'const LS_THEME_PRESET\s*=\s*"[^"]+";\n', '', src)
src = re.sub(r'const LS_CUSTOM_THEME\s*=\s*"[^"]+";\n', '', src)

# Remove the "Mirror of the localStorage keys" comment block if present
src = re.sub(r'//\s*Mirror of the localStorage keys.*?(?=\n\n|\nimport|\nconst |\nexport )', '', src, flags=re.DOTALL)

f.write_text(src)
print(f"Updated: {f.name}")

# Verify
src2 = f.read_text()
local_consts = len(re.findall(r'^const LS_THEME_(MODE|PRESET|CUSTOM_THEME)\s*=', src2, re.MULTILINE))
print(f"  Local LS_* consts remaining: {local_consts} (should be 0)")

# ============================================================
# themes.ts — export pickContrastForeground
# ============================================================
f = REPO / "voice_typer/client/src/renderer/src/themes.ts"
src = f.read_text()

# Rename _pickContrastForeground to pickContrastForeground and export it
src = src.replace("function _pickContrastForeground", "export function pickContrastForeground")
# Update all internal callers
src = src.replace("_pickContrastForeground(", "pickContrastForeground(")

f.write_text(src)
print(f"Updated: {f.name}")

# ============================================================
# theme-contrast.ts — import from themes, normalize non-hex
# ============================================================
f = REPO / "voice_typer/client/src/renderer/src/lib/theme-contrast.ts"
src = f.read_text()

# Add import of pickContrastForeground from themes
if 'pickContrastForeground' not in src or 'from "@/themes"' not in src:
    # Check existing imports from @/themes
    themes_import_match = re.search(r'import\s*\{([^}]+)\}\s*from\s*"@/themes"', src)
    if themes_import_match:
        existing_imports = themes_import_match.group(1)
        if 'pickContrastForeground' not in existing_imports:
            new_imports = existing_imports.rstrip().rstrip(',') + ', pickContrastForeground'
            src = src.replace(existing_imports, new_imports)
    else:
        # Add new import line
        last_import = 0
        for m in re.finditer(r'^import\s.*$', src, re.MULTILINE):
            last_import = m.end()
        src = src[:last_import] + '\nimport { pickContrastForeground } from "@/themes";' + src[last_import:]

# Remove the local _pickContrastForeground function
src = re.sub(r'function _pickContrastForeground\([^)]*\):\s*string\s*\{[^}]+\}\n', '', src)

# Update the caller to use the imported version
src = src.replace("_pickContrastForeground(", "pickContrastForeground(")

# Normalize non-hex colors in getContrastPair
# Find getContrastPair and add cssColorToHex normalization
# Check if cssColorToHex is already imported
if 'cssColorToHex' not in src:
    # Import it from color-utils
    cu_import_match = re.search(r'import\s*\{([^}]+)\}\s*from\s*"@/lib/color-utils"', src)
    if cu_import_match:
        existing = cu_import_match.group(1)
        if 'cssColorToHex' not in existing:
            new_imports = existing.rstrip().rstrip(',') + ', cssColorToHex'
            src = src.replace(existing, new_imports)
    else:
        last_import = 0
        for m in re.finditer(r'^import\s.*$', src, re.MULTILINE):
            last_import = m.end()
        src = src[:last_import] + '\nimport { cssColorToHex } from "@/lib/color-utils";' + src[last_import:]

# In getContrastPair, wrap get(k) calls with cssColorToHex
# The pattern is: const fg = get("--foreground"); etc.
# Replace: const X = get("--Y"); with: const X = cssColorToHex(get("--Y"));
src = re.sub(
    r'const (fg|bg|subtleBg|mutedText)\s*=\s*get\("(--[^"]+)"\);',
    r'const \1 = cssColorToHex(get("\2"));',
    src,
)

f.write_text(src)
print(f"Updated: {f.name}")

# Verify
src2 = f.read_text()
if '_pickContrastForeground' in src2:
    print("  WARNING: theme-contrast.ts still has _pickContrastForeground")
else:
    print("  OK: theme-contrast.ts uses imported pickContrastForeground")

print("\nAll fixes applied.")
