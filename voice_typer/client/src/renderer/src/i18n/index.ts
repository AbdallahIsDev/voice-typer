// i18n package public surface + explicit initialization entrypoint.
//
//the 745-LOC ``i18n.ts`` monolith has been split into focused
// modules (locale / rtl / store / translate / hooks / push). This file
// re-exports the public API so existing consumers can keep importing
// from ``@/i18n/i18n`` (or update to ``@/i18n``) without touching call
// sites.
//
//sub-finding (1-N Finding 4): the module-load side effects that
// used to run at i18n.ts import time (read localStorage, detect
// browser locale, set DOM dir/lang, kick off async chunk load) are
// now wrapped in ``initI18n()``. The function is:
//
//   - exported here so ``main.tsx`` (and test setup) can call it
//     explicitly at a known point in the boot sequence; AND
//   - auto-called on first import of this module so existing behavior
//     (i18n initializes itself when any consumer first touches the
//     package) is preserved.
//
// The auto-call is idempotent — subsequent calls (from ``main.tsx``
// or tests) are no-ops.

import { detectBrowserLocale, type Locale, SUPPORTED_LOCALES } from "./locale";
import { isRtlLocale } from "./rtl";
import { _setCurrentLocale, ensureLocaleLoaded } from "./store";

// ── Public API re-exports ────────────────────────────────────────

// hooks.ts — React hooks + subscriber registry.
export {
	getLocaleSnapshot,
	subscribeLocale,
	useT,
	useTChoice,
} from "./hooks";
export type { Locale } from "./locale";
// locale.ts — Locale type, SUPPORTED_LOCALES, labels, browser-locale detection.
export {
	detectBrowserLocale,
	getLocaleLabel,
	SUPPORTED_LOCALES,
} from "./locale";
// push.ts — tray-label resolver + IPC push helpers.
//
// `pushLocaleToMainProcess` and `pushLocaleToPythonBackend` are
// exported for testability (the setLocale-propagation test spies on
// the bridge surfaces these helpers call). They are still considered
// semi-internal — production code should drive them via `setLocale`.
export {
	pushLocaleToMainProcess,
	pushLocaleToPythonBackend,
	trayLabelsForLocale,
} from "./push";
//rtl.ts — RTL helpers ( 1-N Finding 7: extracted for layout-component reuse).
export { isRtlLocale, RTL_LOCALES } from "./rtl";
// store.ts — translation state container + locale orchestrator.
export {
	ensureLocaleLoaded,
	getLocale,
	registerTranslations,
	setLocale,
} from "./store";
// translate.ts — translation + pluralization functions.
export { t, tChoice } from "./translate";

// ── Explicit initialization ──────────────────────────────────────

let _initCalled = false;

/**
 * Initialize the i18n runtime: restore the user's saved locale (or
 * auto-detect from the browser), set the document's ``dir`` / ``lang``
 * attributes, and kick off the async dynamic-import of the selected
 * locale's translation table.
 *
 *  sub-finding (1-N Finding 4): this replaces the module-load
 * side effects that used to live at the top of i18n.ts (the
 * localStorage restore + browser-detect + DOM dir/lang set + async
 * load kickoff). The function is:
 *
 *   - Idempotent: subsequent calls are no-ops (so it's safe for both
 *     ``main.tsx`` and individual test files to call it).
 *   - Auto-called on first import of this module: preserves the prior
 *     behavior where any consumer touching the i18n package triggers
 *     initialization. Consumers that want explicit control can call
 *     ``initI18n()`` from their entrypoint — the auto-call will then
 *     no-op.
 *
 * Side effects (all wrapped in try/catch so a missing DOM / localStorage
 * doesn't crash callers in SSR / sandboxed contexts):
 *   - Read ``localStorage["voice-typer-ui-locale"]`` and validate
 *     against {@link SUPPORTED_LOCALES}.
 *   - If no saved locale (or saved value invalid), fall back to
 *     {@link detectBrowserLocale} ().
 *   - Set ``document.documentElement.dir`` to ``"rtl"`` for RTL
 *     locales (currently Arabic) or ``"ltr"`` otherwise (F-4 / ).
 *   - Set ``document.documentElement.lang`` to the locale code so
 *     screen readers pronounce content in the user-selected UI locale
 *     ().
 *   - For non-English locales, fire-and-forget
 *     {@link ensureLocaleLoaded} so the dynamic-import chunk is
 *     in-flight by the time the first ``t()`` call happens ().
 *
 * NOTE: this function does NOT call {@link setLocale} because that
 * would persist the locale back to localStorage (redundant — we just
 * read it) and push to IPC bridges (which may not be installed yet at
 * module-init / boot time). It writes ``_currentLocale`` directly via
 * the internal ``_setCurrentLocale`` mutator and applies the DOM +
 * async-load side effects itself.
 *
 * Initialization timing: the auto-call at the bottom of this module
 * (see "Auto-initialization" below) is INTENTIONAL and stays for
 * backwards compatibility — any consumer that imports the i18n package
 * gets a working ``t()`` immediately, even before ``main.tsx`` runs.
 * ``main.tsx`` SHOULD still call ``initI18n()`` explicitly at the top
 * of its render sequence for deterministic ordering (so the locale is
 * restored + DOM ``dir``/``lang`` are set BEFORE the first React
 * commit, not after). The function is idempotent, so the second call
 * from ``main.tsx`` is a no-op — the auto-call only protects consumers
 * that import the package outside the React tree (tests, dev tools,
 * early ``window``-bridge shims).
 */
export function initI18n(): void {
	if (_initCalled) return;
	_initCalled = true;

	//restore from localStorage if available; otherwise
	// auto-detect from the browser/OS so first-run users see their
	// language without hunting for the Settings dropdown.
	let next: Locale = "en";
	try {
		if (typeof localStorage !== "undefined") {
			const saved = localStorage.getItem("voice-typer-ui-locale");
			if (saved && (SUPPORTED_LOCALES as readonly string[]).includes(saved)) {
				next = saved as Locale;
			} else {
				//no saved preference — auto-detect from the browser.
				next = detectBrowserLocale();
			}
		}
	} catch (e) {
		// localStorage may be unavailable in some contexts (SSR, sandboxed)
		console.warn("[renderer:i18n] module-load locale restore failed:", e);
	}

	_setCurrentLocale(next);

	//I18N-4: apply the RTL ``dir`` attribute on initial
	// module load. ``setLocale()`` (below) sets
	// ``document.documentElement.dir`` whenever the locale changes at
	// runtime, but the module-load restore path above only sets
	// ``_currentLocale`` in memory — it never touched the DOM. So a
	// user with Arabic saved who reloaded the app saw
	// ``_currentLocale === "ar"`` but
	// ``document.documentElement.dir === "ltr"`` (the browser
	// default) until they manually re-selected Arabic in Settings.
	// Setting the dir here at init time closes the gap.
	try {
		if (typeof document !== "undefined") {
			document.documentElement.dir = isRtlLocale(next) ? "rtl" : "ltr";
			//also set ``lang`` so screen readers pronounce content in
			// the user-selected UI locale (not the browser default).
			document.documentElement.lang = next;
		}
	} catch (e) {
		// document may be unavailable in some contexts (SSR, tests)
		console.warn("[renderer:i18n] initial document dir/lang set failed:", e);
	}

	//kick off the async load of the user's restored/detected
	// locale at init time so the dynamic import is in-flight by the
	// time the first ``t()`` call happens. English (the universal
	// fallback) is already registered synchronously at store.ts
	// module init, so ``t()`` returns English strings until the
	// dynamic chunk resolves — then ``useT`` subscribers are
	// notified and the UI repaints with the user's selected locale.
	// The fire-and-forget pattern means init stays synchronous.
	if (next !== "en") {
		void ensureLocaleLoaded(next);
	}
}

// ── Auto-initialization (preserves prior behavior) ───────────────
//
//sub-finding (1-N Finding 4) asked for the side-effect
// orchestration to move into ``initI18n()`` called explicitly from
// ``main.tsx`` / test setup. We do BOTH:
//
//   - Auto-call ``initI18n()`` here so any consumer that imports the
//     i18n package (the existing pattern) still gets initialized
//     i18n for free — no behavior change.
//   - Export ``initI18n`` so ``main.tsx`` (and tests) can call it
//     explicitly. The function is idempotent so the second call is a
//     no-op.
//
// The auto-call below is INTENTIONAL and stays even after
// ``main.tsx`` begins calling ``initI18n()`` explicitly. It protects
// consumers that import the package outside the React tree (tests,
// dev tools, early ``window``-bridge shims) and the function's
// idempotency guard means the redundant call from ``main.tsx`` is a
// cheap no-op. ``main.tsx`` SHOULD still call ``initI18n()`` at the
// top of its render sequence for deterministic ordering — the
// auto-call runs at module-eval time (whichever importer wins the
// race), which is not guaranteed to be before the first React commit.
initI18n();
