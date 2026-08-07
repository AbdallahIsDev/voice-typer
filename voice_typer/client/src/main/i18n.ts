/**
 * Minimal main-process i18n bundle.
 *
 * The main process has no React/i18next. We bundle ~10 dialog
 * strings here so native Electron dialogs (single-instance error, critical
 * error, model folder picker, export save-as dialogs) can be localized in
 * the same language the user picked in the renderer.
 *
 * Locale sync:
 *   - At module load, `currentLocale` is seeded from `app.getLocale()`
 *     (Electron's OS-locale API) via {@link seedLocaleFromOs}. This
 *     ensures non-English users see their language in crash / early-
 *     failure dialogs (`bootstrap.ts` criticalError dialog,
 *     `start-python.ts` singleInstance dialog) that can fire BEFORE the
 *     renderer has mounted and pushed its own locale. The seed uses
 *     the same primary-subtag fallback as the renderer-pushed path
 *     (e.g. `"fr-FR"` → `"fr"`).
 *   - The renderer persists its locale to `localStorage["voice-typer-ui-locale"]`
 *     (see src/renderer/src/i18n/i18n.ts). The renderer cannot read main's
 *     memory, so it pushes its locale via an `i18n:set-locale` IPC channel.
 *     The handler in `main/ipc/window-handlers.ts` invokes
 *     {@link setMainLocale}, which reassigns {@link currentLocale} so that
 *     native main-process UI (tray tooltips, OS notifications routed
 *     through main) can be localized in the user's chosen language. The
 *     renderer's explicit IPC push ALWAYS overrides the OS-locale seed —
 *     the user's chosen UI language wins once the renderer mounts.
 *
 * The bundle covers all 8 locales that the renderer ships
 * (en, es, ar, de, fr, hi, ru, zh). Adding a new locale requires:
 *   1. Adding a new entry to {@link MAIN_STRINGS} below.
 *   2. Adding the locale to the renderer's `SUPPORTED_LOCALES` in
 *      `src/renderer/src/i18n/i18n.ts`.
 *
 * The shape of every locale entry MUST match the `en` entry (same keys).
 * TypeScript enforces this via the `MainStrings` mapped type.
 *
 * Dead-code cleanup: `getMainLocale()` and the
 * `export` modifier on the `MainLocale` type were removed — no consumer
 * outside this module ever imported either. `MainLocale` stays as a
 * module-local type alias so internal references (`currentLocale`)
 * remain typed.
 *
 * `setMainLocale()` was re-added and is invoked by the
 * `i18n:set-locale` IPC handler in `main/ipc/window-handlers.ts`. The
 * handler pushes locale changes from the renderer to the main process
 * so that native main-process UI (tray tooltips, OS notifications
 * routed through main) can be localized. `currentLocale` is `let`
 * because `setMainLocale` reassigns it. `MAIN_STRINGS` is still read by
 * `mainT()` (used by the `model:import-dialog` handler).
 *
 * Fallback chain: when the renderer pushes a locale, {@link setMainLocale}
 * resolves it against MAIN_STRINGS in this order:
 *
 *   1. `currentLocale` — exact match (e.g. `"zh"`, `"ar"`).
 *   2. `primary subtag` — when the pushed locale is a regional variant
 *      (contains `-`), try the bare primary subtag (e.g. `"zh-CN"` →
 *      `"zh"`). This mirrors the renderer's `t()` lookup chain so a
 *      regional UI locale that hasn't yet been registered in
 *      MAIN_STRINGS falls back to its parent language instead of
 *      English — e.g. a user on `"zh-CN"` sees Chinese dialogs (with
 *      English fallback only for keys the Chinese table lacks), not
 *      English dialogs outright.
 *   3. `"en"` — the universal fallback when neither step resolves.
 *      `mainT()` then looks up the key against the resolved locale's
 *      table, then against `MAIN_STRINGS.en`, then returns the raw key
 *      (defensive — should never happen for keys declared in
 *      {@link MAIN_STRINGS.en}).
 */

import { app } from "electron";

import { APP_NAME } from "./branding";
// Static JSON imports (not `readFileSync` at module init).
//
// The locale tables were originally loaded via
// `readFileSync(join(__dirname, "i18n", "locales", ...))` — the
// comment there claimed "the bundler inlines the JSON file content at
// build time when the readFileSync call is statically analyzable".
// That is FALSE: Rollup/electron-vite never inlines `readFileSync`
// calls. The JSON files exist under `src/main/i18n/locales/` in the
// source tree (so vitest passed — `__dirname` points at source there),
// but nothing copies them into the electron-vite output
// (`out/main/i18n/locales/`), so every dev AND packaged build failed to
// load all 8 locales with ENOENT and fell back to empty tables.
//
// Static JSON imports ARE bundled into the JS output by Vite/Rollup's
// built-in JSON plugin, so the strings ship inside `out/main/index.js`
// and work identically in dev, preview, and packaged builds. Each file
// is tiny (~9 dialog keys), so inlining all 8 adds a few KB to the
// main bundle in exchange for the locales always being present.
import ar from "./i18n/locales/ar.json";
import de from "./i18n/locales/de.json";
import en from "./i18n/locales/en.json";
import es from "./i18n/locales/es.json";
import fr from "./i18n/locales/fr.json";
import hi from "./i18n/locales/hi.json";
import ru from "./i18n/locales/ru.json";
import zh from "./i18n/locales/zh.json";

/**
 * Locale string tables, bundled at build time by the static imports
 * above. Keyed by the same 8 locale ids the renderer ships.
 */
const LOCALE_TABLES: Record<string, Record<string, string>> = {
	ar,
	de,
	en,
	es,
	fr,
	hi,
	ru,
	zh,
};

/**
 * Look up a locale table from the statically-bundled JSON imports.
 *
 * Never throws and never touches the filesystem: every supported
 * locale is compiled into the bundle, so an unknown key degrades to
 * an empty table (same fallback the old runtime-read had on ENOENT),
 * and `mainT`'s English-fallback chain covers the rest.
 */
function _loadLocaleJson(locale: string): Record<string, string> {
	return LOCALE_TABLES[locale] ?? {};
}

/**
 * The set of locales that ship dialog strings for the main process.
 * Must stay in sync with the renderer's `SUPPORTED_LOCALES`.
 *
 * Loaded from the statically-bundled JSON imports above (see
 * `LOCALE_TABLES`) — never from disk at runtime. The `{appName}`
 * placeholder in `dialog.singleInstance.title` is substituted with
 * `APP_NAME` from `./branding` so the JSON files stay free of
 * hardcoded product names (see the branding rule in AGENTS.md).
 */
function _withAppName(table: Record<string, string>): Record<string, string> {
	// Substitute the {appName} placeholder with the canonical
	// APP_NAME constant on every value. Only `dialog.singleInstance.title`
	// uses the placeholder today, but the helper is generic so future
	// strings that embed the app name don't need a special case.
	const result: Record<string, string> = {};
	for (const [key, value] of Object.entries(table)) {
		result[key] = value.split("{appName}").join(APP_NAME);
	}
	return result;
}

// TypeScript can't infer that every locale key (en, ar, de, ...) is
// always present in MAIN_STRINGS — the runtime loader guarantees it
// (each `i18n/locales/<locale>.json` file is committed and tested by
// `i18n-locale-contract.test.ts`), but the `Record<string, ...>` type
// widens the key set to `string`. We assert the narrower type so
// `MAIN_STRINGS.en` is non-undefined at the lookup sites.
type MainLocale = "ar" | "de" | "en" | "es" | "fr" | "hi" | "ru" | "zh";
type MainStringsTable = Record<string, string>;

const MAIN_STRINGS: Record<MainLocale, MainStringsTable> = {
	ar: _withAppName(_loadLocaleJson("ar")),
	de: _withAppName(_loadLocaleJson("de")),
	en: _withAppName(_loadLocaleJson("en")),
	es: _withAppName(_loadLocaleJson("es")),
	fr: _withAppName(_loadLocaleJson("fr")),
	hi: _withAppName(_loadLocaleJson("hi")),
	ru: _withAppName(_loadLocaleJson("ru")),
	zh: _withAppName(_loadLocaleJson("zh")),
};

/** English reference keys — every locale must provide exactly these keys. */
type MainStrings = typeof MAIN_STRINGS.en;
type MainStringsKey = keyof MainStrings;

/**
 * Canonical, hand-maintained list of dialog keys that `mainT` accepts.
 *
 * The renderer's i18n layer infers its key set from the JSON imports
 * directly (TypeScript can read `import en from "./en.json"`), and the
 * main process now does the same via its own static JSON imports —
 * but because the bundled tables are typed `Record<string, string>`,
 * the type system still cannot narrow the key set. `MAIN_KEYS` is the
 * manual bridge: it lets
 * `MainKey = typeof MAIN_KEYS[number]` act as the literal-union type
 * for `mainT`'s `key` parameter, catching typos like
 * `mainT("dialog.criticalError.titl")` at compile time.
 *
 * Keep this array in lockstep with `i18n/locales/en.json` for the
 * core dialog keys. The contract is enforced by
 * `main/__tests__/i18n-main-keys-contract.test.ts`.
 */
export const MAIN_KEYS = [
	"dialog.criticalError.title",
	"dialog.criticalError.body",
	"dialog.singleInstance.title",
	"dialog.singleInstance.message",
	"dialog.selectModelFolder.title",
	"dialog.export.config",
	"dialog.export.history",
	"dialog.export.templates",
	"dialog.export.vocabulary",
	"dialog.preloadError.body",
	"notify.app.config_load_failed_body",
	"state.app.starting",
	"dialog.crashLoop.title",
	"dialog.crashLoop.mainBody",
	"dialog.crashLoop.bubbleBody",
] as const;

/** Literal-union type of {@link MAIN_KEYS} — narrows `mainT`'s `key` parameter. */
export type MainKey = (typeof MAIN_KEYS)[number];

/**
 * The locale used by {@link mainT} for subsequent lookups.
 *
 * Seeded from `app.getLocale()` (Electron's OS-locale API) at module
 * load so non-English users see their language in crash / early-failure
 * dialogs even when the renderer never gets a chance to push its own
 * locale via the `i18n:set-locale` IPC channel. The criticalError
 * dialog (fired by `bootstrap.ts` when the app is crashing) and the
 * singleInstance dialog (fired by `start-python.ts` when Python exits
 * early) are the two main beneficiaries — both can fire BEFORE the
 * renderer has mounted, so the prior `"en"` hard-default meant every
 * non-English user saw English crash text.
 *
 * Seeding is best-effort: if `app.getLocale()` throws (rare race before
 * `app.whenReady()` on some platforms) or returns an unregistered
 * locale whose primary subtag is also unregistered, the seed falls back
 * to `"en"` — identical to the prior default. The renderer's explicit
 * IPC push (via {@link setMainLocale}) ALWAYS overrides the seed, so
 * the user's chosen UI language wins once the renderer mounts.
 */
let currentLocale: MainLocale = seedLocaleFromOs();

/**
 * Best-effort OS-locale seed. Reads `app.getLocale()` and resolves it
 * against {@link MAIN_STRINGS} using the same primary-subtag fallback
 * chain that {@link setMainLocale} uses for renderer-pushed locales.
 *
 * Wrapped in try/catch because `app.getLocale()` is documented to
 * require the `ready` event in some Electron versions; in practice it
 * returns a usable value before `ready` on Linux/macOS/Windows, but
 * the catch guarantees the seed can never crash the import.
 *
 * Returns `"en"` on any failure (identical to the prior hard-default).
 */
function seedLocaleFromOs(): MainLocale {
	try {
		const osLocale = app?.getLocale?.();
		if (typeof osLocale === "string" && osLocale.length > 0) {
			return resolveLocale(osLocale).locale;
		}
	} catch {
		// Best-effort — if app.getLocale() is unavailable at module
		// load (rare pre-ready race), fall back to "en". The
		// renderer's IPC push will override the seed once it loads.
	}
	return "en";
}

/**
 * Resolve a locale string (BCP-47 — e.g. `"zh"`, `"zh-CN"`, `"pt-BR"`)
 * against {@link MAIN_STRINGS}. Pure function — no side effects, no
 * warning emission. Used by both the OS-locale seed
 * ({@link seedLocaleFromOs}) and the renderer-pushed locale
 * ({@link setMainLocale}) so the resolution chain stays in one place.
 *
 * Resolution chain (mirrors the renderer's `t()` lookup chain):
 *
 *   1. Exact match — if the locale is directly registered in
 *      {@link MAIN_STRINGS} (e.g. `"zh"`, `"ar"`), use it as-is.
 *   2. Primary subtag — if the locale is a regional variant
 *      (contains `-`) and not directly registered, try the bare
 *      primary subtag (e.g. `"zh-CN"` → `"zh"`). This lets a regional
 *      UI locale fall back to its parent language instead of English
 *      when MAIN_STRINGS hasn't yet been extended for the regional
 *      variant.
 *   3. English fallback — if neither step resolves, fall back to
 *      `"en"`. The caller is responsible for emitting any user-facing
 *      warning (the seed path is silent; {@link setMainLocale} warns
 *      so a missing renderer-pushed locale is visible during dev).
 *
 * The returned `registered` flag is `true` for steps 1 and 2, `false`
 * for step 3 — letting {@link setMainLocale} distinguish "successfully
 * resolved via fallback" from "fell all the way through to en".
 */
function resolveLocale(locale: string): {
	locale: MainLocale;
	registered: boolean;
} {
	if (locale in MAIN_STRINGS) {
		return { locale: locale as MainLocale, registered: true };
	}
	if (locale.includes("-")) {
		const parts = locale.split("-");
		const primary = parts[0];
		if (primary !== undefined && primary in MAIN_STRINGS) {
			return { locale: primary as MainLocale, registered: true };
		}
	}
	return { locale: "en", registered: false };
}

/**
 * Update the main-process locale from the renderer's locale
 * selection. Called by the `i18n:set-locale` IPC handler in
 * `window-handlers.ts` whenever the user changes the UI language.
 * ALWAYS overrides the OS-locale seed set at module load by
 * {@link seedLocaleFromOs}.
 *
 * Resolution chain (mirrors the renderer's `t()` lookup chain):
 *
 *   1. Exact match — if the pushed locale is directly registered in
 *      {@link MAIN_STRINGS} (e.g. `"zh"`, `"ar"`), use it as-is.
 *   2. Primary subtag — if the pushed locale is a regional variant
 *      (contains `-`) and not directly registered, try the bare
 *      primary subtag (e.g. `"zh-CN"` → `"zh"`). This lets a regional
 *      UI locale fall back to its parent language instead of English
 *      when MAIN_STRINGS hasn't yet been extended for the regional
 *      variant.
 *   3. English fallback — if neither step resolves, fall back to
 *      `"en"` and emit a console warning so the missing locale is
 *      visible during development. The user still gets English dialogs
 *      rather than a crash.
 */
export function setMainLocale(locale: string): void {
	const { locale: resolved, registered } = resolveLocale(locale);
	if (!registered) {
		console.warn(
			`[i18n] setMainLocale: unknown locale "${locale}" — falling back to "en". ` +
				`Add dialog strings for this locale (or its primary subtag) to i18n/locales/<locale>.json.`,
		);
	}
	currentLocale = resolved;
}

/**
 * Translate a dialog key for the current main-process locale.
 *
 * Lookup chain: `MAIN_STRINGS[currentLocale]` → `MAIN_STRINGS.en` →
 * raw key. The locale itself was already normalized by
 * {@link setMainLocale} (regional variant → primary subtag → en), so
 * `currentLocale` here is always one of the keys present in
 * MAIN_STRINGS — the `table?.[...]` access therefore always resolves,
 * and the English fallback is only a defensive guard against a key
 * that was somehow declared outside {@link MainStringsKey}.
 *
 * Placeholders: `{name}` is replaced with `fmt.name` if provided. Missing
 * interpolation args are left as the literal `{name}` so the bug is
 * visible during testing instead of silently dropping data.
 */
export function mainT(
	key: MainKey,
	fmt?: Record<string, string | number>,
): string {
	const table = MAIN_STRINGS[currentLocale] as MainStrings | undefined;
	const en = MAIN_STRINGS.en;
	const raw =
		(table?.[key as MainStringsKey] as string | undefined) ??
		(en[key as MainStringsKey] as string | undefined) ??
		key;
	if (!fmt) return raw;
	return raw.replace(/\{(\w+)\}/g, (_, k: string) =>
		Object.hasOwn(fmt, k) ? String(fmt[k]) : `{${k}}`,
	);
}
