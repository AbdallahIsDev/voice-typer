// theme-bootstrap.ts — pre-React theme application to prevent FOUC.
//
// Before this module existed, the renderer painted with the
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
//   3. Calls ``loadThemePreset(preset)`` to dynamically
//      ``import()`` the preset module and populate the ``THEMES`` entry
//      in place, THEN calls ``applyThemeVars()`` so the preset CSS
//      variable overrides are in place before the first paint.
//
// Lazy themes: the preset's ``light`` / ``dark`` var maps are
// no longer statically imported — they live in a separate async chunk
// per preset. The bootstrap ``await``s ``loadThemePreset(preset)``
// (TOP-LEVEL AWAIT) so the vars are applied BEFORE React mounts. ESM
// ``<script type="module">`` tags block subsequent module scripts
// until the top-level await resolves, so ``main.tsx`` (loaded as the
// next ``<script type="module">`` in ``index.html``) does not execute
// until the theme is applied. This preserves the FOUC-prevention
// guarantee.
//
// After applying the initial theme, the module also fires-and-forgets
// ``loadThemePreset`` for ALL remaining lazy presets so the cache is
// warm by the time the user opens Settings and switches themes. This
// is a background pre-fetch — it does NOT block the initial paint or
// React mount. Under offline conditions (C-DATA-1) the chunks are
// served from the local .asar (Electron) or dist (Tauri), so the
// pre-fetch completes without network access.
//
// After this module runs, ``useTheme`` may re-apply the theme once the
// backend config is fetched — but that is a no-op when the cached
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
	LS_CUSTOM_THEME,
	LS_THEME_MODE,
	LS_THEME_PRESET,
	type ThemeMode,
} from "@/lib/theme-storage-keys";
import {
	applyThemeVars,
	type CustomThemeData,
	deriveCustomVars,
} from "@/themes";
import { lazyThemeLoaders, loadThemePreset } from "@/themes/index";

// The localStorage key strings + ThemeMode type are shared with
// ``hooks/useTheme.ts`` via the canonical source
// ``lib/theme-storage-keys.ts``. Both modules MUST use the same key
// strings — centralising them in one module makes drift impossible
// (a previous regression had the bootstrap read a stale value while
// the hook fell back to the default because the two copies diverged).
// ``lib/theme-storage-keys.ts`` is a zero-dependency module (only
// exports string literals + a type alias) so importing it here does
// NOT pull in a transitive graph before the first paint.

function readLsThemeMode(): ThemeMode {
	try {
		const v = localStorage.getItem(LS_THEME_MODE);
		if (v === "light" || v === "dark" || v === "system") return v;
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed renderer).
		// Non-fatal — fall through to the default "system" mode.
		console.warn("[renderer:theme-bootstrap] readLsThemeMode failed:", e);
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
		console.warn("[renderer:theme-bootstrap] readLsThemePreset failed:", e);
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
		console.warn(
			"[renderer:theme-bootstrap] readLsCustomTheme parse failed:",
			e,
		);
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
		console.warn(
			"[renderer:theme-bootstrap] resolveIsDark matchMedia failed:",
			e,
		);
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
 * This function is ASYNC because it must
 * ``await loadThemePreset(preset)`` to populate the lazy preset
 * light/dark maps before ``applyThemeVars`` reads them.
 * For ``default`` and ``custom`` (statically imported) the
 * ``loadThemePreset`` call is an instant no-op.
 *
 * Exposed as a named export so unit tests can invoke it directly
 * without relying on module side-effects.
 */
export async function applyBootstrapTheme(): Promise<void> {
	if (typeof document === "undefined") return;

	const mode = readLsThemeMode();
	const preset = readLsThemePreset();
	const custom = readLsCustomTheme();
	const isDark = resolveIsDark(mode);

	// 1. Apply the .dark class so dark-mode CSS rules engage.
	document.documentElement.classList.toggle("dark", isDark);

	// 2. Dynamically load the preset module so its light/dark
	//    var maps are populated in the THEMES entry. For
	//    default / custom this is an instant no-op (statically
	//    imported). For the 10 lazy presets this triggers a dynamic
	//    import() — the FIRST time only; subsequent calls hit the
	//    in-memory cache. The await guarantees applyThemeVars
	//    below sees the populated vars.
	await loadThemePreset(preset);

	// 3. Apply preset CSS variable overrides.  For the custom
	//    preset we derive the full var set from the cached 6
	//    core colours — matching what useTheme effect does
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
// (because this is the first <script type=module> in index.html).
// In tests / SSR the guard inside applyBootstrapTheme makes this a no-op.
//
// TOP-LEVEL AWAIT — the module evaluation pauses until the
// preset is loaded + vars applied. ESM <script type=module> tags
// block subsequent module scripts until top-level await resolves, so
// main.tsx (the next script tag in index.html) does not execute
// until the theme is applied. This preserves the FOUC-prevention
// guarantee.
await applyBootstrapTheme();

// Background pre-fetch the remaining lazy presets so the cache
// is warm by the time the user opens Settings and switches themes.
// This does NOT block the initial paint (the await above already
// applied the active theme) — it is a fire-and-forget pre-fetch. Each
// loadThemePreset call is idempotent + cached, so the active
// preset (already loaded above) is an instant no-op here.
//
// Under offline conditions (C-DATA-1) the chunks are served from the
// local .asar (Electron) or dist (Tauri), so the pre-fetch completes
// without network access. If a fetch fails (e.g. a chunk file is
// missing from a corrupted install), loadThemePreset catches the
// error internally and leaves the entry with empty light/dark — the
// caller falls back to the stylesheet default.
for (const id of Object.keys(lazyThemeLoaders)) {
	void loadThemePreset(id).catch(() => {
		// loadThemePreset already logs internally; the .catch
		// here prevents an unhandled-rejection warning if the
		// background pre-fetch fails.
	});
}

// invalidate the cached theme when prefers-color-scheme changes.
//
// original FOUC fix: this module reads the cached theme mode
// from localStorage ONCE at module-import time and applies it before
// React mounts. When the user OS switches between light/dark while
// the app is running (e.g. auto mode on macOS, scheduled dark mode
// at sunset), the browser fires a prefers-color-scheme change
// event on window.matchMedia. Without a listener, the cached
// system mode would NOT track the OS change — the renderer
// would stay in the old mode until the user manually toggled or
// restarted the app.
//
// The listener re-applies the bootstrap (which re-reads resolveIsDark
// against the now-current matchMedia result) on every change.
//
// The listener is installed ONCE at module import and lives for the
// lifetime of the renderer process. It is a no-op in tests / SSR
// (the typeof window === undefined / window.matchMedia
// guards short-circuit).
//
// applyBootstrapTheme is async (it awaits loadThemePreset).
// The listener fire-and-forgets the async call — the OS theme change
// is not time-critical (the user already has a rendered UI; a brief
// async delay re-applying the preset vars is invisible). For the
// default / custom presets the call is instant; for lazy presets the
// cache is already warm (pre-fetched above).

if (typeof window !== "undefined" && window.matchMedia) {
	try {
		const mql = window.matchMedia("(prefers-color-scheme: dark)");
		// Check for addEventListener first — older Safari
		// versions (< 14) only support the deprecated
		// addListener API. The cast through unknown is
		// deliberate: TS MediaQueryList type only exposes
		// addEventListener (the standard API), but the
		// runtime object on old Safari has addListener
		// instead.
		if (typeof mql.addEventListener === "function") {
			mql.addEventListener("change", () => {
				void applyBootstrapTheme();
			});
		} else if (
			typeof (mql as unknown as { addListener?: unknown }).addListener ===
			"function"
		) {
			(
				mql as unknown as {
					addListener: (cb: () => void) => void;
				}
			).addListener(() => {
				void applyBootstrapTheme();
			});
		}
	} catch (e) {
		console.warn(
			"[renderer:theme-bootstrap] failed to install prefers-color-scheme change listener:",
			e,
		);
	}
}
