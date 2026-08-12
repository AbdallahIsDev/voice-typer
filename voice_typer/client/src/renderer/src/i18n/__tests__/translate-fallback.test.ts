/**
 * Tests for the `t()` lookup-chain fallback + dev-mode missing-key
 * warning.
 *
 * Covers two behaviours added to address the i18n silent-fallback gap:
 *
 *   1. Dev-mode warning: when a key is missing from BOTH the current
 *      locale AND the English fallback, `t()` returns the raw key
 *      string (defensive — callers must not crash on a typo) but also
 *      emits a single `console.warn("[renderer:i18n] missing key:", key,
 *      "for locale:", currentLocale)` so the typo is visible during
 *      QA. Production builds skip the warning (`import.meta.env?.DEV`
 *      is `false` in production per Vite). Vitest runs with
 *      `DEV=true`, so the warning fires under test — these tests
 *      spy on `console.warn` to assert the diagnostic shape.
 *
 *   2. Primary-subtag fallback: when the current locale is a regional
 *      variant (contains `-`) and the key is missing from the
 *      regional map, `t()` tries the bare primary subtag's map before
 *      falling back to English. The chain is:
 *
 *        currentLocale → primary subtag → en → raw key
 *
 *      e.g. `zh-CN` → `zh` → `en` → key. A translator adding a
 *      regional override for a handful of keys therefore does not
 *      silently lose the parent language's coverage for the rest.
 *
 * Test isolation: the i18n runtime state (`_translations`,
 * `_resolvedCache`, `_currentLocale`) is module-level and persists
 * across tests in the same file. Each test registers fresh translation
 * tables for the locales it touches, calls `_invalidateResolvedCache`
 * to drop any cached resolved strings, and uses `_setCurrentLocale` to
 * set a regional locale that `setLocale` would reject (because
 * regional variants aren't in `SUPPORTED_LOCALES`). `afterEach`
 * restores the locale to `"en"` and deletes the regional map entry so
 * later tests in the file start from a clean baseline.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { APP_NAME } from "@/branding";
import type { Locale } from "@/i18n/locale";
import {
	_setCurrentLocale,
	_translations,
	registerTranslations,
	setLocale,
} from "@/i18n/store";
import { _invalidateResolvedCache, t } from "@/i18n/translate";

/**
 * Cast helper: the `Locale` type is a closed union of the 8 shipped
 * locales and does NOT include regional variants like `zh-CN`. At
 * runtime, the i18n store accepts any string as a locale key (the
 * `Map<Locale, ...>` typing is advisory — JavaScript Maps don't enforce
 * key types). Tests that exercise the primary-subtag fallback need to
 * register a regional variant, so we cast through `unknown` to satisfy
 * TypeScript without changing the production `Locale` union.
 */
function asLocale(s: string): Locale {
	return s as unknown as Locale;
}

describe("t() dev-mode missing-key warning", () => {
	let warnSpy: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		// Start each test in English with a fresh English table.
		setLocale("en");
		registerTranslations("en", {});
		_invalidateResolvedCache("en");
		warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
	});

	afterEach(() => {
		warnSpy.mockRestore();
		setLocale("en");
	});

	it("emits a console.warn when a key is missing from current locale AND English", () => {
		// No tables registered for `nonexistent.key` — `t()` should
		// fall through to the raw-key path and emit the dev warning.
		const result = t("nonexistent.key");
		expect(result).toBe("nonexistent.key");
		expect(warnSpy).toHaveBeenCalledTimes(1);
		expect(warnSpy).toHaveBeenCalledWith(
			"[renderer:i18n] missing key:",
			"nonexistent.key",
			"for locale:",
			"en",
		);
	});

	it("does NOT warn when the key resolves via the English fallback", () => {
		// English has the key — the lookup succeeds at step 3 of the
		// chain (currentLocale → primary subtag → en). No warning.
		registerTranslations("en", { app: { name: APP_NAME } });
		const result = t("app.name");
		expect(result).toBe(APP_NAME);
		expect(warnSpy).not.toHaveBeenCalled();
	});

	it("does NOT warn when the key resolves via the current locale directly", () => {
		registerTranslations("en", { app: { name: APP_NAME } });
		// Register a fresh non-English locale and switch to it.
		registerTranslations("ar", { app: { name: "كاتب الصوت" } });
		_setCurrentLocale("ar");
		_invalidateResolvedCache("ar");
		const result = t("app.name");
		expect(result).toBe("كاتب الصوت");
		expect(warnSpy).not.toHaveBeenCalled();
	});

	it("warns at most once per (locale, key) pair — subsequent calls hit the resolved cache", () => {
		// First call resolves the chain, finds nothing, warns, and
		// caches the raw key in `_resolvedCache`.
		const first = t("repeat.miss");
		expect(first).toBe("repeat.miss");
		expect(warnSpy).toHaveBeenCalledTimes(1);
		// Second call hits the cache and returns the cached raw key
		// WITHOUT re-walking the chain — so no second warning.
		const second = t("repeat.miss");
		expect(second).toBe("repeat.miss");
		expect(warnSpy).toHaveBeenCalledTimes(1);
	});
});

describe("t() primary-subtag fallback for regional locales", () => {
	// Cast the regional locale once — used by every test in this block.
	const ZH_CN = asLocale("zh-CN");

	beforeEach(() => {
		// Start from a clean slate so prior registrations don't leak
		// into the chain assertions. We register empty English + zh
		// tables and then test-specific zh-CN tables per test.
		setLocale("en");
		registerTranslations("en", { app: { enOnly: "EN value" } });
		// Register a fresh `zh` table per test (overwritten in each
		// test body as needed).
		registerTranslations("zh", { app: { zhOnly: "ZH value" } });
		// Drop any cached resolved strings for the locales we touch.
		_invalidateResolvedCache("en");
		_invalidateResolvedCache("zh");
		_invalidateResolvedCache(ZH_CN);
	});

	afterEach(() => {
		// Restore the locale to English and drop the regional zh-CN
		// entry from `_translations` so it doesn't leak into later
		// tests in the file (or into other test files via the shared
		// module state).
		setLocale("en");
		_translations.delete(ZH_CN);
		_invalidateResolvedCache(ZH_CN);
	});

	it("falls back to the primary subtag when the regional map lacks the key (zh-CN → zh)", () => {
		// Register a regional `zh-CN` table WITHOUT `app.zhOnly` —
		// the key is only present in the `zh` parent table.
		registerTranslations(ZH_CN, {
			app: { regionalOnly: "CN-specific override" },
		});
		_setCurrentLocale(ZH_CN);

		const result = t("app.zhOnly");
		// The primary-subtag step picks up the `zh` value rather than
		// falling back to English (which doesn't have `app.zhOnly`
		// either).
		expect(result).toBe("ZH value");
	});

	it("prefers the regional map's value over the primary subtag when both have the key", () => {
		// Both `zh-CN` and `zh` define `app.greeting`. The regional
		// value must win — primary-subtag is a FALLBACK, not an
		// override.
		registerTranslations("zh", { app: { greeting: "ZH greeting" } });
		registerTranslations(ZH_CN, {
			app: { greeting: "CN-specific greeting" },
		});
		_setCurrentLocale(ZH_CN);

		expect(t("app.greeting")).toBe("CN-specific greeting");
	});

	it("falls back to English when neither the regional map nor the primary subtag has the key", () => {
		// `app.enOnly` is only in the English table.
		registerTranslations(ZH_CN, {});
		_setCurrentLocale(ZH_CN);

		expect(t("app.enOnly")).toBe("EN value");
	});

	it("falls back to the raw key (with dev warning) when the key is missing from every step of the chain", () => {
		// Spy on console.warn so the dev-mode diagnostic doesn't leak
		// into test output AND so we can assert on its shape.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			registerTranslations(ZH_CN, {});
			_setCurrentLocale(ZH_CN);

			const result = t("totally.missing");
			// Raw key returned (defensive — no crash).
			expect(result).toBe("totally.missing");
			// Dev warning fired exactly once, naming the regional
			// locale (not the primary subtag) so the developer knows
			// which locale they were rendering against when the miss
			// occurred.
			expect(warnSpy).toHaveBeenCalledTimes(1);
			expect(warnSpy).toHaveBeenCalledWith(
				"[renderer:i18n] missing key:",
				"totally.missing",
				"for locale:",
				"zh-CN",
			);
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("skips the primary-subtag step for bare-primary locales (no `-` in the tag)", () => {
		// `zh` itself is a bare primary — `t()` against `zh` must not
		// redundantly re-look-up `zh` (which would be a no-op anyway).
		// We verify the chain still works end-to-end: `zh` → `en` →
		// key, picking up the English value for `app.enOnly`.
		_setCurrentLocale("zh");
		expect(t("app.enOnly")).toBe("EN value");
	});

	it("caches the primary-subtag resolution so subsequent calls skip the chain", () => {
		// First call walks the chain (zh-CN miss → zh hit) and caches
		// the resolved `zh` value under the zh-CN locale's cache entry.
		registerTranslations(ZH_CN, {});
		_setCurrentLocale(ZH_CN);
		const first = t("app.zhOnly");
		expect(first).toBe("ZH value");

		// Mutate the `zh` table AFTER the first call resolved. If the
		// cache works, the second call returns the ORIGINAL (cached)
		// value rather than the new one — proving the chain was
		// short-circuited.
		registerTranslations("zh", { app: { zhOnly: "MUTATED value" } });
		const second = t("app.zhOnly");
		expect(second).toBe("ZH value");
	});
});
