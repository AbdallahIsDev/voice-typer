// @vitest-environment node
/**
 * Tests for the OS-locale seed added to `main/i18n.ts`.
 *
 * Background
 * ----------
 * Pre-fix: `currentLocale` was hard-defaulted to `"en"` at module
 * load. The renderer's `i18n:set-locale` IPC push (the only thing
 * that reassigns `currentLocale`) requires the renderer to have
 * mounted AND sent the IPC. But `mainT()` is called by:
 *
 *   - `bootstrap.ts` criticalError dialog (fires when the app is
 *     crashing — the renderer may NEVER have loaded).
 *   - `start-python.ts` singleInstance dialog (fires when Python
 *     exits early — the renderer may not have loaded).
 *   - `window-handlers.ts` selectModelFolder dialog.
 *   - `export-handlers.ts` export dialogs.
 *
 * Non-English users saw English dialogs in those crash / early-failure
 * paths because `currentLocale` was `"en"` until the renderer pushed.
 *
 * Post-fix: `currentLocale` is seeded from `app.getLocale()` at module
 * load (with the same primary-subtag fallback `setMainLocale` uses).
 * The renderer's IPC push ALWAYS overrides the seed — the user's
 * explicit UI-language choice wins once the renderer mounts.
 *
 * These tests verify:
 *   1. When `app.getLocale()` returns `"fr-FR"`, `currentLocale` is
 *      initialized to `"fr"` (primary-subtag fallback) — proven by
 *      `mainT("dialog.criticalError.title")` returning the French
 *      title, NOT the English fallback, BEFORE any `setMainLocale`
 *      call.
 *   2. When `app.getLocale()` returns an exact-registered locale
 *      (e.g. `"zh"`), `currentLocale` is initialized to that locale.
 *   3. When `app.getLocale()` returns an unregistered locale whose
 *      primary subtag is also unregistered (e.g. `"klingon-Latn"`),
 *      the seed falls back to `"en"` silently (no warning — the seed
 *      is best-effort, NOT a user-facing locale push).
 *   4. When `app.getLocale()` throws, the seed falls back to `"en"`
 *      silently (the seed must never crash the import).
 *   5. The renderer's `setMainLocale` IPC push ALWAYS overrides the
 *      seed — proving the user's explicit preference wins.
 *
 * Vitest 4 hoists `vi.mock()` above all top-level statements, so the
 * electron mock is in place BEFORE `import("../i18n")` evaluates the
 * `seedLocaleFromOs()` IIFE at module load. Each test sets the
 * hoisted `getLocale` mock implementation, then `vi.resetModules()` +
 * dynamic `import("../i18n")` re-runs the seed with the new mock.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// `vi.hoisted()` runs BEFORE `vi.mock()` factories are evaluated, so
// variables declared here are in scope inside the factory. The
// `getLocale` mock is re-implemented per-test via `mockImplementation`
// (after `vi.clearAllMocks()` in `beforeEach`).
const mocks = vi.hoisted(() => {
	return {
		getLocale: vi.fn<() => string>(),
	};
});

vi.mock("electron", () => ({
	app: {
		getLocale: mocks.getLocale,
		// Other `app.*` methods the i18n module doesn't touch, but the
		// mock shape stays realistic so future additions to i18n.ts
		// don't silently break the test.
		getPath: () => "/tmp/vt-i18n-seed-test-userdata",
		isPackaged: false,
	},
}));

describe("OS-locale seed: currentLocale initialized from app.getLocale()", () => {
	beforeEach(() => {
		// Clear per-test mock state so each test starts fresh.
		vi.clearAllMocks();
		// Default: `app.getLocale()` returns the empty string (the
		// Electron "unable to detect" sentinel). Tests that need a
		// specific locale override this with `mockImplementation`.
		mocks.getLocale.mockImplementation(() => "");
	});

	it("seeds currentLocale to 'fr' when app.getLocale() returns 'fr-FR' (primary-subtag fallback)", async () => {
		// `"fr-FR"` is NOT a key in MAIN_STRINGS (only the bare `"fr"` is).
		// The primary-subtag fallback must resolve it to `"fr"` so a French
		// user sees French dialogs in the crash path BEFORE the renderer
		// has mounted and pushed its own locale.
		mocks.getLocale.mockImplementation(() => "fr-FR");
		vi.resetModules();
		const { mainT } = await import("../i18n");
		// Prove the seed fired by reading a localized title BEFORE any
		// setMainLocale call. The French title contains "Erreur critique".
		const title = mainT("dialog.criticalError.title");
		expect(title).toContain("Erreur critique");
		// Defensive: NOT the English fallback.
		expect(title).not.toContain("Critical Error");
		// Defensive: NOT the raw key.
		expect(title).not.toBe("dialog.criticalError.title");
	});

	it("seeds currentLocale to the exact locale when app.getLocale() returns a registered locale (zh)", async () => {
		// `"zh"` IS a key in MAIN_STRINGS. The exact-match step must pick
		// it up directly (no primary-subtag fallback needed).
		mocks.getLocale.mockImplementation(() => "zh");
		vi.resetModules();
		const { mainT } = await import("../i18n");
		const title = mainT("dialog.criticalError.title");
		// The Chinese title contains "严重错误".
		expect(title).toContain("严重错误");
		expect(title).not.toContain("Critical Error");
	});

	it("falls back to 'en' silently when the OS locale is unregistered (klingon-Latn)", async () => {
		// `"klingon-Latn"` is not registered, and its primary subtag
		// `"klingon"` is also not registered. The seed must fall back to
		// `"en"` WITHOUT emitting a console warning — the seed is
		// best-effort and silent (unlike `setMainLocale`, which warns so
		// a missing renderer-pushed locale is visible during dev).
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			mocks.getLocale.mockImplementation(() => "klingon-Latn");
			vi.resetModules();
			const { mainT } = await import("../i18n");
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("Critical Error");
			// The seed path must NOT warn — the warning is reserved for
			// `setMainLocale` (renderer-pushed unknown locales). The OS
			// locale is not user-controlled, so warning on it would be
			// noise.
			expect(warnSpy).not.toHaveBeenCalled();
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("falls back to 'en' silently when app.getLocale() throws", async () => {
		// `app.getLocale()` is documented to require the `ready` event
		// in some Electron versions. If it throws (rare pre-ready race),
		// the seed must fall back to `"en"` so the import never crashes.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			mocks.getLocale.mockImplementation(() => {
				throw new Error("app is not ready");
			});
			vi.resetModules();
			const { mainT } = await import("../i18n");
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("Critical Error");
			expect(warnSpy).not.toHaveBeenCalled();
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("falls back to 'en' silently when app.getLocale() returns an empty string", async () => {
		// An empty string is the documented "unable to detect" return
		// value from `app.getLocaleCountryCode()` (not `getLocale`, but
		// we defend against the same shape here for robustness).
		mocks.getLocale.mockImplementation(() => "");
		vi.resetModules();
		const { mainT } = await import("../i18n");
		const title = mainT("dialog.criticalError.title");
		expect(title).toContain("Critical Error");
	});

	it("falls back to 'en' silently when app.getLocale() returns a non-string", async () => {
		// Defensive: the Electron types say `getLocale(): string`, but
		// a buggy Electron build / test stub could return undefined.
		// The seed must never crash on a hostile return value.
		mocks.getLocale.mockImplementation(() => undefined as unknown as string);
		vi.resetModules();
		const { mainT } = await import("../i18n");
		const title = mainT("dialog.criticalError.title");
		expect(title).toContain("Critical Error");
	});

	it("the renderer's setMainLocale push ALWAYS overrides the OS-locale seed", async () => {
		// Seed is "fr-FR" → "fr". The renderer then pushes "de" (German).
		// The push MUST win — the user's explicit UI-language choice
		// overrides the OS-locale seed once the renderer mounts.
		mocks.getLocale.mockImplementation(() => "fr-FR");
		vi.resetModules();
		const { mainT, setMainLocale } = await import("../i18n");
		// Before the push: French (from the OS-locale seed).
		expect(mainT("dialog.criticalError.title")).toContain("Erreur critique");
		// After the push: German (the renderer's explicit choice).
		setMainLocale("de");
		expect(mainT("dialog.criticalError.title")).toContain("Kritischer Fehler");
		expect(mainT("dialog.criticalError.title")).not.toContain(
			"Erreur critique",
		);
	});

	it("a regional renderer push (zh-TW) overrides the OS-locale seed (fr-FR)", async () => {
		// Seed is "fr-FR" → "fr". The renderer pushes "zh-TW" (regional
		// variant not directly registered — primary-subtag fallback
		// resolves to "zh"). The push MUST win over the seed.
		mocks.getLocale.mockImplementation(() => "fr-FR");
		vi.resetModules();
		const { mainT, setMainLocale } = await import("../i18n");
		expect(mainT("dialog.criticalError.title")).toContain("Erreur critique");
		setMainLocale("zh-TW");
		expect(mainT("dialog.criticalError.title")).toContain("严重错误");
	});
});
