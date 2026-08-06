/**
 * unit tests for `components/audio/audioFilterLabels.ts` — the pure
 * builder for the AudioFilterChain labels dictionary.
 *
 * The `buildAudioFilterLabels(t)` function resolves every i18n key
 * referenced by `audioFilterRowDescriptors` against the supplied `t`
 * function. CONSTRAINT C-I18N-1 requires every user-facing string be in
 * ALL 8 locale files (`en`, `ar`, `de`, `es`, `fr`, `hi`, `ru`, `zh`).
 *
 * Coverage:
 *   1. Every i18n key referenced by the descriptor registry (labelKey,
 *      infoSearchKey, sectionTitleKey, infoKey, ariaKey, and any
 *      `options[].labelKey`) is PRESENT in every one of the 8 shipped
 *      locale JSON files. Catches the regression where a key was added
 *      to the registry (or to en.json) but a translator forgot to add
 *      it to (say) hi.json — without this check, Hindi users silently
 *      fall back to English.
 *   2. `buildAudioFilterLabels` calls `t()` exactly once per unique key
 *      (deduplicates the section title key shared across all descriptors).
 *   3. The returned dictionary is keyed by the full i18n key — callers
 *      can look up by `descriptor.labelKey` etc. without mangling.
 *   4. The `t` function receives only the key (no params) for these
 *      label/info/aria lookups.
 */
import { describe, expect, it, vi } from "vitest";
import { flatten } from "@/i18n/store";
import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";

import { buildAudioFilterLabels } from "../audioFilterLabels";
import { audioFilterRowDescriptors } from "../audioFilterRowDescriptors";

type TranslationDict = Record<string, unknown>;

// All 8 shipped locales (C-I18N-1). `en` is the canonical reference set;
// the others are parity-checked against it.
const LOCALES: Record<string, TranslationDict> = {
	ar: ar as TranslationDict,
	de: de as TranslationDict,
	en: en as TranslationDict,
	es: es as TranslationDict,
	fr: fr as TranslationDict,
	hi: hi as TranslationDict,
	ru: ru as TranslationDict,
	zh: zh as TranslationDict,
};

// Flatten each locale JSON into a Set<dotKey> using the same `flatten`
// helper the runtime uses, so the parity check compares the exact key
// surface the renderer sees after `flatten()` (not the raw nested shape).
const localeKeySets: Record<string, Set<string>> = Object.fromEntries(
	Object.entries(LOCALES).map(([loc, dict]) => [
		loc,
		new Set(flatten(dict).keys()),
	]),
);

// Collect every i18n key referenced by the descriptor registry. Each
// descriptor contributes: labelKey, infoSearchKey, sectionTitleKey,
// infoKey, ariaKey, and (for `kind: "select"`) every option's `labelKey`
// (plain `label` strings like "RNNoise" are NOT i18n keys — they're
// rendered verbatim).
const registryKeys: string[] = [];
for (const d of audioFilterRowDescriptors) {
	registryKeys.push(d.labelKey);
	registryKeys.push(d.infoSearchKey);
	registryKeys.push(d.sectionTitleKey);
	registryKeys.push(d.infoKey);
	registryKeys.push(d.ariaKey);
	for (const opt of d.options ?? []) {
		if (typeof opt.labelKey === "string") {
			registryKeys.push(opt.labelKey);
		}
	}
}
const uniqueRegistryKeys = Array.from(new Set(registryKeys));

describe("audioFilterLabels — i18n key parity across all 8 locales (C-I18N-1)", () => {
	// Sanity: the registry actually references keys. If this fails, the
	// descriptor registry has been emptied (regression) — every other
	// test below would silently pass with 0 assertions.
	it("registry references a non-empty set of i18n keys", () => {
		expect(uniqueRegistryKeys.length).toBeGreaterThan(0);
	});

	// One describe-block per locale so a failure reports exactly which
	// locale is missing which key (mirrors `locale-key-parity.test.ts`).
	for (const [locale, keySet] of Object.entries(localeKeySets)) {
		it(`every registry key is present in ${locale}.json`, () => {
			const missing = uniqueRegistryKeys.filter((k) => !keySet.has(k));
			expect(missing).toEqual([]);
		});
	}
});

describe("audioFilterLabels — buildAudioFilterLabels(t) dedup + keying", () => {
	it("calls t() exactly once per unique key (no duplicate resolutions)", () => {
		const t = vi.fn((key: string) => `resolved:${key}`);
		const labels = buildAudioFilterLabels(t);

		// `t` is called once per unique key. The registry references
		// the section title key from every descriptor, but the
		// builder dedupes via a `seen` Set, so `t` is invoked exactly
		// `uniqueRegistryKeys.length` times.
		expect(t).toHaveBeenCalledTimes(uniqueRegistryKeys.length);

		// Every call received exactly one arg (the key) — no params
		// for label/info/aria lookups.
		for (const call of t.mock.calls) {
			expect(call).toHaveLength(1);
			expect(typeof call[0]).toBe("string");
		}

		// Returned dictionary has exactly one entry per unique key.
		expect(Object.keys(labels).length).toBe(uniqueRegistryKeys.length);
	});

	it("returns a dictionary keyed by the full i18n key", () => {
		const t = vi.fn((key: string) => `[${key}]`);
		const labels = buildAudioFilterLabels(t);

		// Lookups by descriptor.labelKey / .infoKey / .ariaKey all work
		// without any name-mangling — the dictionary is flat.
		for (const d of audioFilterRowDescriptors) {
			expect(labels[d.labelKey]).toBe(`[${d.labelKey}]`);
			expect(labels[d.infoKey]).toBe(`[${d.infoKey}]`);
			expect(labels[d.ariaKey]).toBe(`[${d.ariaKey}]`);
			expect(labels[d.infoSearchKey]).toBe(`[${d.infoSearchKey}]`);
			expect(labels[d.sectionTitleKey]).toBe(`[${d.sectionTitleKey}]`);
		}
	});

	it("the shared sectionTitleKey is resolved exactly once across all descriptors", () => {
		const t = vi.fn((key: string) => key);
		buildAudioFilterLabels(t);

		// Count how many descriptors reference the section title key —
		// it should be ALL of them (the constant AUDIO_SECTION_TITLE_KEY
		// is shared). But `t` should be called with that key exactly ONCE.
		const sectionKey = audioFilterRowDescriptors[0]?.sectionTitleKey;
		expect(sectionKey).toBeDefined();
		const descriptorCountReferencingSection = audioFilterRowDescriptors.filter(
			(d) => d.sectionTitleKey === sectionKey,
		).length;
		expect(descriptorCountReferencingSection).toBe(
			audioFilterRowDescriptors.length,
		);
		const sectionKeyCallCount = t.mock.calls.filter(
			(c) => c[0] === sectionKey,
		).length;
		expect(sectionKeyCallCount).toBe(1);
	});

	it("resolves select-option labelKey entries too (noneOption)", () => {
		const t = vi.fn((key: string) => `T<${key}>`);
		const labels = buildAudioFilterLabels(t);

		// The noiseSuppression select descriptor has a `none` option
		// with labelKey "settings.audioEnhancement.noneOption".
		const noneOptionKey = "settings.audioEnhancement.noneOption";
		expect(labels[noneOptionKey]).toBe(`T<${noneOptionKey}>`);
	});
});
