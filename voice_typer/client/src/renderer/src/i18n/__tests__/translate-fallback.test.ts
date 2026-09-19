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

function asLocale(s: string): Locale {
	return s as unknown as Locale;
}

function fixtureKey(key: string): string {
	return key;
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
		// No tables registered for `nonexistent.key`, `t()` should
		// fall through to the raw-key path and emit the dev warning.
		const result = t(fixtureKey("nonexistent.key"));
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
		// English has the key, the lookup succeeds at step 3 of the
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

	it("warns at most once per (locale, key) pair, subsequent calls hit the resolved cache", () => {
		// First call resolves the chain, finds nothing, warns, and
		// caches the raw key in `_resolvedCache`.
		const first = t(fixtureKey("repeat.miss"));
		expect(first).toBe("repeat.miss");
		expect(warnSpy).toHaveBeenCalledTimes(1);
		// Second call hits the cache and returns the cached raw key
		// WITHOUT re-walking the chain, so no second warning.
		const second = t(fixtureKey("repeat.miss"));
		expect(second).toBe("repeat.miss");
		expect(warnSpy).toHaveBeenCalledTimes(1);
	});
});

describe("t() primary-subtag fallback for regional locales", () => {
	// Cast the regional locale once, used by every test in this block.
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

		const result = t(fixtureKey("app.zhOnly"));
		// The primary-subtag step picks up the `zh` value rather than
		// falling back to English (which doesn't have `app.zhOnly`
		// either).
		expect(result).toBe("ZH value");
	});

	it("prefers the regional map's value over the primary subtag when both have the key", () => {
		// Both `zh-CN` and `zh` define `app.greeting`. The regional
		// value must win, primary-subtag is a FALLBACK, not an
		// override.
		registerTranslations("zh", { app: { greeting: "ZH greeting" } });
		registerTranslations(ZH_CN, {
			app: { greeting: "CN-specific greeting" },
		});
		_setCurrentLocale(ZH_CN);

		expect(t(fixtureKey("app.greeting"))).toBe("CN-specific greeting");
	});

	it("falls back to English when neither the regional map nor the primary subtag has the key", () => {
		// `app.enOnly` is only in the English table.
		registerTranslations(ZH_CN, {});
		_setCurrentLocale(ZH_CN);

		expect(t(fixtureKey("app.enOnly"))).toBe("EN value");
	});

	it("falls back to the raw key (with dev warning) when the key is missing from every step of the chain", () => {
		// Spy on console.warn so the dev-mode diagnostic doesn't leak
		// into test output AND so we can assert on its shape.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			registerTranslations(ZH_CN, {});
			_setCurrentLocale(ZH_CN);

			const result = t(fixtureKey("totally.missing"));
			// Raw key returned (defensive, no crash).
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
		// `zh` itself is a bare primary, `t()` against `zh` must not
		// redundantly re-look-up `zh` (which would be a no-op anyway).
		// We verify the chain still works end-to-end: `zh` → `en` →
		// key, picking up the English value for `app.enOnly`.
		_setCurrentLocale("zh");
		expect(t(fixtureKey("app.enOnly"))).toBe("EN value");
	});

	it("caches the primary-subtag resolution so subsequent calls skip the chain", () => {
		// First call walks the chain (zh-CN miss → zh hit) and caches
		// the resolved `zh` value under the zh-CN locale's cache entry.
		registerTranslations(ZH_CN, {});
		_setCurrentLocale(ZH_CN);
		const first = t(fixtureKey("app.zhOnly"));
		expect(first).toBe("ZH value");

		// Mutate the `zh` table AFTER the first call resolved. If the
		// cache works, the second call returns the ORIGINAL (cached)
		// value rather than the new one, proving the chain was
		// short-circuited.
		registerTranslations("zh", { app: { zhOnly: "MUTATED value" } });
		const second = t(fixtureKey("app.zhOnly"));
		expect(second).toBe("ZH value");
	});
});
