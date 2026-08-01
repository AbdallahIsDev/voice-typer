/**
 * Compile-time + runtime guard: every locale in ``RTL_LOCALES`` MUST be a
 * member of ``SUPPORTED_LOCALES``.
 *
 * The RTL flipping logic in ``setLocale`` (store.ts) and ``initI18n``
 * (index.ts) looks up ``isRtlLocale(next)`` to decide whether to set
 * ``document.documentElement.dir = "rtl"``. The ``isRtlLocale`` lookup
 * is a ``Set.has`` against ``RTL_LOCALES`` — but ``RTL_LOCALES`` is
 * typed as ``Set<Locale>``, and ``Locale`` is the union derived from
 * ``SUPPORTED_LOCALES``. So if a locale is added to ``RTL_LOCALES``
 * without also being added to ``SUPPORTED_LOCALES``, the TypeScript
 * compiler catches it at build time.
 *
 * This test is the runtime backstop: it asserts the subset relationship
 * directly so a future refactor that loosens the ``Locale`` type (e.g.
 * changing ``RTL_LOCALES`` to ``Set<string>`` to "fix" a build error)
 * still fails loudly in CI.
 *
 * Platform: Linux sandbox / Windows host / macOS host (pure static
 * check — no DOM, no jsdom). Validation:
 *   VALIDATE ON LINUX HOST: cd voice_typer/client && npx vitest run \
 *     src/renderer/src/i18n/__tests__/rtl-locale-guard.test.ts
 */
import { describe, expect, it } from "vitest";

import { SUPPORTED_LOCALES } from "@/i18n/locale";
import { RTL_LOCALES } from "@/i18n/rtl";

describe("RTL_LOCALES ⊆ SUPPORTED_LOCALES guard", () => {
	it("RTL_LOCALES is non-empty (sanity check — guard runs against a real set)", () => {
		// If RTL_LOCALES is ever accidentally emptied, the subset assertion
		// below passes trivially (empty set ⊆ anything). This sanity check
		// ensures the guard has teeth: we KNOW at least Arabic is RTL.
		expect(
			RTL_LOCALES.size,
			"RTL_LOCALES must contain at least 'ar'",
		).toBeGreaterThan(0);
	});

	it("every locale in RTL_LOCALES is also in SUPPORTED_LOCALES", () => {
		const supported = new Set<string>(SUPPORTED_LOCALES);
		const offenders: string[] = [];
		for (const locale of RTL_LOCALES) {
			if (!supported.has(locale)) {
				offenders.push(locale);
			}
		}
		expect(
			offenders,
			[
				"RTL_LOCALES contains locales that are NOT in SUPPORTED_LOCALES.",
				"Either add the missing locale to SUPPORTED_LOCALES (locale.ts) OR",
				"remove it from RTL_LOCALES (rtl.ts). A locale that's RTL-flipped",
				"but not in the supported list would never be selectable by the",
				"user — the flip logic would be dead code, and the Locale union",
				"wouldn't type-check the Set membership.",
				"Offending locales:",
				...offenders.map((l) => `  - ${l}`),
			].join("\n"),
		).toEqual([]);
	});

	it("'ar' is in RTL_LOCALES (regression — Arabic is the canonical RTL locale)", () => {
		// Defensive: if someone refactors RTL_LOCALES to e.g. a Record or
		// an array, the `.has` API changes and the test above silently
		// passes with zero iterations. Pinning 'ar' here catches that.
		expect(RTL_LOCALES.has("ar")).toBe(true);
	});

	it("'ar' is in SUPPORTED_LOCALES (regression — Arabic must be shippable)", () => {
		// If 'ar' is ever dropped from SUPPORTED_LOCALES (e.g. the Arabic
		// translation file is removed), the RTL flip logic for Arabic
		// becomes unreachable through normal locale-switching. This test
		// makes the dependency explicit.
		expect(SUPPORTED_LOCALES).toContain("ar");
	});
});
