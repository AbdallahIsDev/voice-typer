// lib/theme-storage-keys.ts — single source of truth for the localStorage
// key strings and shared theme-mode type used by both the pre-React
// bootstrap (``theme-bootstrap.ts``) and the React ``useTheme`` hook
// (``hooks/useTheme.ts``).
//
// BG-81: previously both modules declared their own private copies of
// ``LS_THEME_MODE`` / ``LS_THEME_PRESET`` / ``LS_CUSTOM_THEME`` /
// ``LS_TEXT_SIZE`` and a local ``ThemeMode`` type alias. The two copies
// had to be kept in sync manually; if one drifted, the bootstrap would
// read a stale value or the hook would silently fall back to the
// default. Centralising them here makes drift impossible — both modules
// import the same constant.
//
// BG-82: this module also exports a single ``isValidThemePresetId``
// validator that both modules use to gate ``localStorage`` reads. The
// previous implementations used different strategies (the bootstrap
// accepted any non-empty string, the hook hard-coded a 12-entry allow-
// list) which could disagree when a new preset was added. The shared
// validator derives the allow-list from the canonical ``THEMES`` array
// so it stays in sync automatically.

/** Effective colour-scheme mode the user has selected. */
export type ThemeMode = "light" | "dark" | "system";

/** localStorage key for the persisted ``ThemeMode`` value. */
export const LS_THEME_MODE = "voice-typer-theme-mode";

/** localStorage key for the persisted theme preset id. */
export const LS_THEME_PRESET = "voice-typer-theme-preset";

/** localStorage key for the persisted custom-theme colour map JSON. */
export const LS_CUSTOM_THEME = "voice-typer-custom-theme";

/** localStorage key for the persisted text-size value (integer px). */
export const LS_TEXT_SIZE = "voice-typer-text-size";
