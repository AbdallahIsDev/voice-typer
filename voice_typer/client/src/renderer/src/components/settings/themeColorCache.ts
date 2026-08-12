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

// type narrowing (kept non-exported): ``ThemeColorCacheEntry`` is only
// used by this module (no external importer — verified by grep across
// the renderer tree). Keep the type NON-exported so the surface area
// stays minimal and future renames don't ripple into other files.
type ThemeColorCacheEntry = {
	light: Record<string, string>;
	dark: Record<string, string>;
};

export const _themeColorCache = new Map<string, ThemeColorCacheEntry>();

// contrast-cache-invalidate: when the OS-level "high
// contrast" / "increased contrast" preference changes, the cached
// hex values for every preset are stale (the browser may remap
// colours to high-contrast variants).  Clear the cache so the next
// ``getCurrentThemeColors`` call re-reads from the DOM with the new
// effective colour scheme.  The listener is installed once at module
// load and lives for the lifetime of the renderer process.
//
// Guarded with ``typeof window !== "undefined"`` so the module can
// be imported in SSR / Vitest-without-jsdom contexts without crashing.
//
// HMR leak fix: in dev mode with Vite HMR, every module reload
// re-executed this top-level block, installing a NEW listener
// without removing the previous one.  Each old listener kept its
// captured ``_themeColorCache`` reference alive (the OLD module's
// Map, not the new one), so the leak was both a memory leak AND a
// correctness leak (the old listener would ``.clear()`` the OLD
// Map, which was no longer the one the rest of the app was reading).
// We now use Vite's ``import.meta.hot?.dispose()`` hook to remove
// the listener when the module is unloaded for HMR. In production
// (no ``import.meta.hot``), the listener lives for the renderer
// process lifetime as before.
//
// Feature-detect ``addEventListener`` vs the legacy ``addListener``
// (Safari < 14 / older Electron versions only exposed the latter).

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
		let remove: ((mq: MediaQueryList, handler: () => void) => void) | null =
			null;
		// addEventListener is the modern API (Safari 14+); some
		// older Chromium/Electron versions only expose
		// ``addListener`` on MediaQueryList.  Feature-detect.
		if (typeof mq.addEventListener === "function") {
			mq.addEventListener("change", handler);
			remove = (m, h) => m.removeEventListener("change", h);
		} else if (typeof (mq as MediaQueryList).addListener === "function") {
			(mq as MediaQueryList).addListener(handler);
			remove = (m, h) => {
				(m as MediaQueryList).removeListener(h);
			};
		}

		// Vite HMR cleanup. ``import.meta.hot`` is only defined
		// in dev (the production build tree-shakes this branch away).
		// When Vite hot-replaces this module, the dispose callback
		// fires BEFORE the new module is installed — we remove the
		// listener here so the old module's `handler` closure (which
		// captures the OLD `_themeColorCache` Map) is no longer
		// referenced by the MediaQueryList.
		if (import.meta.hot && remove) {
			import.meta.hot.dispose(() => {
				remove(mq, handler);
			});
		}
	} catch (e) {
		// matchMedia may throw in restricted sandboxes — non-fatal.
		// The cache simply won't auto-invalidate on contrast change;
		// the per-color-change invalidation in
		// ``handleCustomColorChange`` still applies.
		console.warn(
			"[renderer:themeColorCache] matchMedia listener setup failed:",
			e,
		);
	}
}
