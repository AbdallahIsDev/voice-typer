// @vitest-environment node
/**
 * Tests for the primary-subtag fallback added to `setMainLocale`.
 *
 * `setMainLocale` resolves a locale string pushed from the renderer
 * against MAIN_STRINGS (the main-process dialog bundle). The resolution
 * chain mirrors the renderer's `t()` lookup chain:
 *
 *   1. Exact match — `locale` is directly registered in MAIN_STRINGS
 *      (e.g. `"zh"`, `"ar"`).
 *   2. Primary subtag — when `locale` is a regional variant (contains
 *      `-`) and not directly registered, try the bare primary subtag
 *      (e.g. `"zh-CN"` → `"zh"`). This lets a regional UI locale fall
 *      back to its parent language instead of jumping straight to
 *      English when MAIN_STRINGS hasn't been extended for the regional
 *      variant.
 *   3. English fallback — if neither step resolves, fall back to
 *      `"en"` and emit a console warning so the missing locale is
 *      visible during development.
 *
 * After `setMainLocale` runs, `mainT()` looks up the key against the
 * resolved locale's MAIN_STRINGS table — so a user on `"zh-CN"` (with
 * no regional table) sees the Chinese dialogs from `MAIN_STRINGS.zh`
 * rather than English dialogs.
 *
 * These tests run in a `node` environment (no DOM) because the main-
 * process i18n bundle has no React / jsdom dependency.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mainT, setMainLocale } from "../i18n";

describe("setMainLocale primary-subtag fallback", () => {
	afterEach(() => {
		// Restore the default locale so a test that switches to a
		// regional variant doesn't leak into the next test.
		setMainLocale("en");
	});

	it("uses the locale directly when it is registered in MAIN_STRINGS", () => {
		// Exact match — no fallback needed. Probes a non-English locale
		// so the assertion can distinguish "registered locale picked up"
		// from "default English".
		setMainLocale("zh");
		const title = mainT("dialog.criticalError.title");
		expect(title).toContain("严重错误"); // Chinese title contains "严重错误"
		expect(title).not.toBe("dialog.criticalError.title");
	});

	it("falls back to the primary subtag for a regional variant (zh-CN → zh)", () => {
		// `"zh-CN"` is NOT a key in MAIN_STRINGS (only the bare `"zh"`
		// is). The primary-subtag step must resolve it to `"zh"` so the
		// user sees Chinese dialogs instead of English.
		setMainLocale("zh-CN");
		const title = mainT("dialog.criticalError.title");
		// The Chinese title from MAIN_STRINGS.zh contains "严重错误".
		expect(title).toContain("严重错误");
		// Defensive: the resolved value is not the raw key.
		expect(title).not.toBe("dialog.criticalError.title");
	});

	it("falls back to the primary subtag for a different regional variant (pt-BR → pt is NOT registered, so falls through to en)", () => {
		// `"pt-BR"` is a regional variant whose primary subtag `"pt"`
		// is also NOT in MAIN_STRINGS (Portuguese isn't shipped). The
		// chain falls through to English with a console warning.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("pt-BR");
			const title = mainT("dialog.criticalError.title");
			// English title contains "Critical Error".
			expect(title).toContain("Critical Error");
			expect(title).not.toBe("dialog.criticalError.title");
			// The fallback emitted a console warning naming the
			// unknown locale + pointing at MAIN_STRINGS.
			expect(warnSpy).toHaveBeenCalledTimes(1);
			expect(warnSpy).toHaveBeenCalledWith(
				expect.stringContaining(`unknown locale "pt-BR"`),
			);
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("falls back to en for an unknown non-regional locale with a warning", () => {
		// `"klingon"` has no `-` and isn't registered — straight to the
		// English fallback path.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("klingon");
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("Critical Error");
			expect(warnSpy).toHaveBeenCalledTimes(1);
			expect(warnSpy).toHaveBeenCalledWith(
				expect.stringContaining(`unknown locale "klingon"`),
			);
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("does not warn on the exact-match path (regional variant NOT triggered)", () => {
		// Sanity: the primary-subtag fallback is a fallback, not a
		// mandatory step. An exact match must short-circuit without
		// consulting the primary-subtag path or emitting a warning.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("ar");
			expect(warnSpy).not.toHaveBeenCalled();
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("خطأ حرج");
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("does not warn on the primary-subtag fallback path (silent resolution)", () => {
		// The primary-subtag fallback is a SUCCESSFUL resolution — it
		// found a parent-language table. The warning is reserved for
		// the final "fall back to en" step, so a regional variant
		// that resolves via the primary subtag must NOT warn.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("ar-SA");
			expect(warnSpy).not.toHaveBeenCalled();
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("خطأ حرج");
		} finally {
			warnSpy.mockRestore();
		}
	});

	it("handles case-sensitivity: locale tags are matched as-is (no lowercasing)", () => {
		// MAIN_STRINGS keys are lowercase (`"zh"`, not `"ZH"`). The
		// primary-subtag fallback splits on `-` but does NOT lowercase,
		// so `"zh-CN"` (canonical BCP-47 casing) matches `"zh"` while
		// `"ZH-CN"` (uppercase primary) would NOT match. This documents
		// the contract: callers (the renderer's `setLocale` IPC push)
		// are responsible for normalizing casing before pushing.
		setMainLocale("zh-CN");
		expect(mainT("dialog.criticalError.title")).toContain("严重错误");

		// Uppercase primary — does NOT match MAIN_STRINGS.zh.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("ZH-CN");
			expect(warnSpy).toHaveBeenCalledTimes(1);
			expect(mainT("dialog.criticalError.title")).toContain("Critical Error");
		} finally {
			warnSpy.mockRestore();
		}
	});
});

describe("setMainLocale + mainT integration (full chain)", () => {
	beforeEach(() => {
		setMainLocale("en");
	});

	afterEach(() => {
		setMainLocale("en");
	});

	it("a regional-locale user sees the parent language's dialog strings (zh-TW → zh)", () => {
		// End-to-end: push `"zh-TW"` (Traditional Chinese — not
		// registered), resolve via primary subtag to `"zh"` (Simplified
		// Chinese — registered), and verify `mainT` returns a Chinese
		// string for a representative dialog key.
		setMainLocale("zh-TW");
		const body = mainT("dialog.criticalError.body", {
			count: 3,
			logPath: "/tmp/crash.log",
		});
		// The Chinese body contains the literal "{count} 个未捕获的异常"
		// — verify the count placeholder was interpolated and the
		// Chinese text is present.
		expect(body).toContain("3");
		expect(body).toContain("未捕获的异常");
		expect(body).not.toBe("dialog.criticalError.body");
	});

	it("an unknown regional locale degrades to English (en-GB fallback path)", () => {
		// `"en-GB"` IS a regional variant of a registered primary
		// (`"en"`). The primary-subtag step resolves it to `"en"` so
		// the user sees English dialogs (no warning, since the
		// resolution succeeded).
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			setMainLocale("en-GB");
			expect(warnSpy).not.toHaveBeenCalled();
			const title = mainT("dialog.criticalError.title");
			expect(title).toContain("Critical Error");
		} finally {
			warnSpy.mockRestore();
		}
	});
});
