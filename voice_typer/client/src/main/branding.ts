/**
 * Centralized branding constants for the Electron main process
 * (mirrors voice_typer/client/src/renderer/src/branding.ts and
 * voice_typer/server/branding.py).
 *
 * BRAND-001: kept in a main-process-only file because the main and
 * renderer tsconfigs include disjoint directory trees; both files
 * must stay in sync when the product is renamed.
 *
 * SYNC REQUIREMENT: the value of APP_NAME MUST be identical across all
 * three branding modules:
 *   1. voice_typer/server/branding.py        (Python backend)
 *   2. voice_typer/client/src/main/branding.ts (this file, main process)
 *   3. voice_typer/client/src/renderer/src/branding.ts (renderer)
 *
 * Enforcement:
 *   - The TS-side equality is asserted by the vitest test
 *     `src/renderer/src/__tests__/branding-sync.test.ts` (CI gate).
 *   - `scripts/check_branding.py` is the canonical CI gate that
 *     catches hardcoded "Voice Typer" strings outside these three
 *     files. Run it locally with `python scripts/check_branding.py`.
 *   - For a Python-side equality assertion, a separate Python test
 *     would need to import branding.py and read the TS files at
 *     runtime (extend `scripts/check_branding.py` if needed).
 *
 * XS-66: the previous module-local `const _APP_DESCRIPTION` was removed —
 * it was declared but never read. The renderer's `branding.ts` still
 * exports its own `APP_DESCRIPTION` for its renderer-side consumers;
 * the main process has no consumer (its only export here is APP_NAME).
 */
export const APP_NAME = "Voice Typer";
