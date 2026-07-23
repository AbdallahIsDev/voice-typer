// theme-bootstrap.ts — pre-React theme application to prevent FOUC.
//
// PVT-018: Before this module existed, the renderer painted with the
// stylesheet-default theme (light mode, no preset vars) for ~1 frame
// before React mounted and ``useTheme`` ran its theme-application
// effect.  On dark-mode or themed installs this produced a visible
// white-flash-of-unstyled-content (FOUC) on every launch.
//
// This module is imported as the FIRST ``<script type="module">`` in
// ``index.html`` (see ``src/renderer/index.html``).  Because ES modules
// are deferred by default, it executes after the DOM is parsed but
// BEFORE ``main.tsx`` calls ``ReactDOM.createRoot().render()``.  It:
//
//   1. Reads the last-known theme state from ``localStorage`` (the same
//      keys ``useTheme`` uses to seed its ``useState`` initializers).
//   2. Applies the ``.dark`` class to ``document.documentElement``
//      based on ``theme_mode`` (or the system preference when mode is
//      ``"system"``).
//   3. Calls ``applyThemeVars()`` so preset CSS variable overrides are
//      in place before the first paint.
//
// After this module runs, ``useTheme`` may re-apply the theme once the
// backend config is fetched — but that's a no-op when the cached
// localStorage state matches the backend (the common case).  See the
// ``hasInitialReloadCompleted`` guard in ``useTheme.ts`` for how the
// post-backend theme-application effect is suppressed on the first
// mount to avoid a second flash.
//
// This module is safe to import in any context (Electron renderer,
// Vitest with jsdom, SSR).  All DOM access is guarded with
// ``typeof document !== "undefined"`` and localStorage access is
// wrapped in try/catch.

import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
} from "@/themes";

// Mirror of the localStorage keys in ``useTheme.ts``.  Kept in sync
// manually — both modules MUST use the same key strings.  If you
// change a key here, change it in ``useTheme.ts`` too.
const LS_THEME_MODE = "voice-typer-theme-mode";
const LS_THEME_PRESET = "voice-typer-theme-preset";
const LS_CUSTOM_THEME = "voice-typer-custom-theme";

type ThemeMode = "light" | "dark" | "system";

function readLsThemeMode(): ThemeMode {
	try {
		const v = localStorage.getItem(LS_THEME_MODE);
		if (v === "light" || v === "dark" || v === "system") return v;
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed renderer).
		// Non-fatal — fall through to the default "system" mode.
		console.warn("[theme-bootstrap] readLsThemeMode failed:", e);
	}
	return "system";
}

function readLsThemePreset(): string {
	try {
		const v = localStorage.getItem(LS_THEME_PRESET);
		if (typeof v === "string" && v.length > 0) return v;
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed renderer).
		// Non-fatal — fall through to the default "default" preset.
		console.warn("[theme-bootstrap] readLsThemePreset failed:", e);
	}
	return "default";
}

function readLsCustomTheme(): CustomThemeData | null {
	try {
		const raw = localStorage.getItem(LS_CUSTOM_THEME);
		if (!raw) return null;
		const parsed = JSON.parse(raw);
		if (
			parsed &&
			typeof parsed === "object" &&
			"light" in parsed &&
			"dark" in parsed
		) {
			return parsed as CustomThemeData;
		}
	} catch (e) {
		// malformed JSON — ignore, fall through to default. A
		// hand-edited devtools payload or a stale schema from an
		// older build can land here; logging helps diagnose those.
		console.warn("[theme-bootstrap] readLsCustomTheme parse failed:", e);
	}
	return null;
}

/**
 * Resolve the effective dark/light state for the given theme mode,
 * consulting ``matchMedia`` for the system preference when mode is
 * ``"system"``.
 *
 * Returns ``false`` (light) when ``window.matchMedia`` is unavailable
 * (e.g. SSR / restricted sandbox) so the bootstrap never crashes.
 */
function resolveIsDark(mode: ThemeMode): boolean {
	if (mode === "dark") return true;
	if (mode === "light") return false;
	// system
	try {
		if (typeof window !== "undefined" && window.matchMedia) {
			return window.matchMedia("(prefers-color-scheme: dark)").matches;
		}
	} catch (e) {
		// matchMedia may throw in some sandboxed renderers.
		// Non-fatal — fall through to light mode (default).
		console.warn("[theme-bootstrap] resolveIsDark matchMedia failed:", e);
	}
	return false;
}

/**
 * Apply the cached theme to the document.  Idempotent — calling it
 * twice with the same cached state produces the same DOM outcome
 * (toggling ``.dark`` to the same value is a no-op, and
 * ``applyThemeVars`` clears previous overrides before applying new
 * ones).
 *
 * Exposed as a named export so unit tests can invoke it directly
 * without relying on module side-effects.
 */
export function applyBootstrapTheme(): void {
	if (typeof document === "undefined") return;

	const mode = readLsThemeMode();
	const preset = readLsThemePreset();
	const custom = readLsCustomTheme();
	const isDark = resolveIsDark(mode);

	// 1. Apply the .dark class so dark-mode CSS rules engage.
	document.documentElement.classList.toggle("dark", isDark);

	// 2. Apply preset CSS variable overrides.  For the 'custom'
	//    preset we derive the full var set from the cached 6
	//    core colours — matching what ``useTheme``'s effect does
	//    post-mount, so the visible result is identical.
	let customVars: Record<string, string> | null = null;
	if (preset === "custom" && custom) {
		const modeVars = isDark ? custom.dark : custom.light;
		customVars = deriveCustomVars(modeVars, isDark);
	}
	applyThemeVars(preset, isDark, customVars);
}

// Run immediately on module import.  In production this happens
// synchronously after the DOM is parsed but before React mounts
// (because this is the first ``<script type="module">`` in
// ``index.html``).  In tests / SSR the guard inside
// ``applyBootstrapTheme`` makes this a no-op.
applyBootstrapTheme();
