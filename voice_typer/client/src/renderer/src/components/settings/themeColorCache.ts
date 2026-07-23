/**
 * Module-level cache for theme color computations.
 *
 * Extracted from ThemeSettingsSection.tsx to avoid the
 * react-refresh/only-export-components lint warning (mixing component
 * and non-component exports in the same file breaks Fast Refresh).
 *
 * The cache stores computed {light, dark} color objects keyed by preset
 * ID, avoiding redundant getComputedStyle + cssColorToHex DOM queries
 * on repeated calls with the same preset.
 *
 * Cache invalidation:
 *   - 'custom' and 'default' entries are deleted when the user changes
 *     a custom color (see handleCustomColorChange in ThemeSettingsSection)
 *   - All entries are cleared on ThemeSettingsSection unmount (see the
 *     cleanup useEffect in ThemeSettingsSection)
 *   - All entries are cleared when the user toggles their OS-level
 *     "high contrast" / "increased contrast" preference (see the
 *     matchMedia listener installed at module load below).  This is
 *     necessary because the listener clears the cache so the next
 *     ``getCurrentThemeColors`` call re-reads from the DOM with the
 *     new effective colour scheme — otherwise the cached hex values
 *     from the normal-contrast reading would persist and produce
 *     stale swatches in the custom-theme editor.
 */

// PVT-XXX (session-5 type narrowing): ``ThemeColorCacheEntry`` is only
// used by this module (no external importer — verified by grep across
// the renderer tree). Keep the type NON-exported so the surface area
// stays minimal and future renames don't ripple into other files.
type ThemeColorCacheEntry = {
	light: Record<string, string>;
	dark: Record<string, string>;
};

export const _themeColorCache = new Map<string, ThemeColorCacheEntry>();

// PVT-043 / CONTRAST-CACHE-INVALIDATE: when the OS-level "high
// contrast" / "increased contrast" preference changes, the cached
// hex values for every preset are stale (the browser may remap
// colours to high-contrast variants).  Clear the cache so the next
// ``getCurrentThemeColors`` call re-reads from the DOM with the new
// effective colour scheme.  The listener is installed once at module
// load and lives for the lifetime of the renderer process.
//
// Guarded with ``typeof window !== "undefined"`` so the module can
// be imported in SSR / Vitest-without-jsdom contexts without crashing.
if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
	try {
		const mq = window.matchMedia("(prefers-contrast: high)");
		// The 'change' callback: clear the entire cache.  Using
		// ``.clear()`` rather than per-key deletes because any
		// preset may be affected (and it's cheaper than
		// enumerating keys).
		const handler = () => {
			_themeColorCache.clear();
		};
		// addEventListener is the modern API (Safari 14+); some
		// older Chromium/Electron versions only expose
		// ``addListener`` on MediaQueryList.  Feature-detect.
		if (typeof mq.addEventListener === "function") {
			mq.addEventListener("change", handler);
		} else if (typeof (mq as MediaQueryList).addListener === "function") {
			(mq as MediaQueryList).addListener(handler);
		}
	} catch (e) {
		// matchMedia may throw in restricted sandboxes — non-fatal.
		// The cache simply won't auto-invalidate on contrast change;
		// the per-color-change invalidation in
		// ``handleCustomColorChange`` still applies.
		console.warn("[themeColorCache] matchMedia listener setup failed:", e);
	}
}
