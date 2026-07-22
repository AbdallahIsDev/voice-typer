/**
 * Centralized branding constants (mirrors voice_typer/server/branding.py
 * and voice_typer/client/src/main/branding.ts).
 *
 * BRAND-001: single source of truth for the application name on the
 * renderer side. Change APP_NAME here and in the other two branding
 * files together.
 *
 * SYNC REQUIREMENT: the value of APP_NAME MUST be identical across all
 * three branding modules:
 *   1. voice_typer/server/branding.py        (Python backend)
 *   2. voice_typer/client/src/main/branding.ts (Electron main process)
 *   3. voice_typer/client/src/renderer/src/branding.ts (this file, renderer)
 *
 * Enforcement:
 *   - The TS-side equality is asserted by the vitest test
 *     `src/renderer/src/__tests__/branding-sync.test.ts` (CI gate).
 *   - The Python-side equality is checked by a separate Python test
 *     (a Python test would need to import branding.py and read the TS
 *     files at runtime; the existing `scripts/check_branding.py`
 *     already parses branding.py for APP_NAME and scans source files
 *     for hardcoded occurrences — extend it if you need a cross-
 *     language equality check at the Python level).
 *   - `scripts/check_branding.py` is the canonical CI gate that
 *     catches hardcoded "Voice Typer" strings outside these three
 *     files. Run it locally with `python scripts/check_branding.py`.
 */
export const APP_NAME = "Voice Typer";
export const APP_DESCRIPTION = "Background voice-to-text utility";
