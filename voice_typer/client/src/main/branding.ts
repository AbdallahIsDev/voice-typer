/**
 * Centralized branding constants for the Electron main process
 * (mirrors voice_typer/client/src/renderer/src/branding.ts and
 * voice_typer/server/branding.py).
 *
 * BRAND-001: kept in a main-process-only file because the main and
 * renderer tsconfigs include disjoint directory trees; both files
 * must stay in sync when the product is renamed.
 */
export const APP_NAME = "Voice Typer";
export const APP_DESCRIPTION = "Background voice-to-text utility";
