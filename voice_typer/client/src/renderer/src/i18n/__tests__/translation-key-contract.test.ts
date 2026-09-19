/**
 * Compile-time + runtime contract tests for the translation-catalog key
 * typing on `t()` / `tChoice()`.
 * The catalog contract (see `i18n/translation-keys.ts`) derives a flat
 * The `// @ts-expect-error` directives below are TYPE-LEVEL assertions:
 * their matching error and `tsc` FAILS the file ("Unused '@ts-expect-error'
 */
import { describe, expect, it, vi } from "vitest";

// Import t through the package barrel (not "@/i18n/translate" directly):
// the barrel loads ./store before ./translate, which the module-level
// `_invalidateResolvedCache("en")` call in store.ts requires, a direct
// translate-first import order deadlocks the ESM cycle (TDZ on
// `_resolvedCache`). This mirrors how production code imports t().
import { t } from "@/i18n/i18n";
import type {
	TranslationChoiceKey,
	TranslationKey,
} from "@/i18n/translation-keys";

// ── Type-level assertions (checked by tsc; values verified at runtime
// in the tests below) ────────────────────────────────────────────────
// Assignments marked `@ts-expect-error` MUST fail to compile; the valid
// assignments MUST compile. Together they pin the derived union to the
// real catalog shape.

/** A real catalog key must be assignable to the strict key union. */
const validKey: TranslationKey =
	"microphoneTest.qualityFeedback.qualityNotApplicable";

/** A plural-family base must be assignable to the tChoice key union. */
const validChoiceBase: TranslationChoiceKey = "analytics.dayCountTooltip";

/** A bare catalog key must also be a valid tChoice key (single-form fallback). */
const validBareChoiceKey: TranslationChoiceKey = "common.lastUpdated";

// @ts-expect-error, the typo'd flat path is absent from the catalog.
const invalidKey: TranslationKey = "microphoneTest.qualityNotApplicable";

// @ts-expect-error, an unknown plural base is absent from the catalog.
const invalidChoiceKey: TranslationChoiceKey = "not.a.plural.base";

describe("t() compile-time catalog contract (type-level assertions)", () => {
	it("accepts a real catalog key and rejects a typo'd path at compile time", () => {
		// Valid: the full nested path exists in en.json (all 8 locales).
		expect(t("microphoneTest.qualityFeedback.qualityNotApplicable")).toBe(
			"N/A: transcription unavailable",
		);

		// The typo'd key (missing `qualityFeedback.` segment) resolves to
		// the raw key at RUNTIME, the exact production bug the compile
		// guard exists to prevent. Kept as a dynamic (string-typed) key so
		// this test can document the fallback behavior:
		const typoKey: string = "microphoneTest.qualityNotApplicable";
		expect(t(typoKey)).toBe("microphoneTest.qualityNotApplicable");

		// The type-assertion constants above resolve to exactly the
		// literals they declare, pinning both halves of the contract.
		expect(validKey).toBe(
			"microphoneTest.qualityFeedback.qualityNotApplicable",
		);
		expect(validChoiceBase).toBe("analytics.dayCountTooltip");
		expect(validBareChoiceKey).toBe("common.lastUpdated");
		expect(invalidKey).toBe("microphoneTest.qualityNotApplicable");
		expect(invalidChoiceKey).toBe("not.a.plural.base");
	});

	it("falls back to the raw key for dynamic keys absent from every locale (dev warning fires once)", () => {
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		try {
			const dynamicKey: string = "some.runtime.built.key";
			expect(t(dynamicKey)).toBe("some.runtime.built.key");
			expect(warnSpy).toHaveBeenCalledTimes(1);
		} finally {
			warnSpy.mockRestore();
		}
	});
});
