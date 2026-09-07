/**
 * Unit tests for the shared audio-preset data registry
 * (`lib/utils/audioPresets.ts`) — the single source of truth for the
 * microphone-quality preset VALUES and their i18n label/description
 * keys, consumed by both live preset presentations (the Settings →
 * Audio Select and the Microphone page's accordion selector).
 *
 * Integrity contract under test:
 *   - the five canonical preset values, in display order, no duplicates;
 *   - every label and description key resolves in ALL 8 locale
 *     catalogues (a missing key in one locale would render the raw key
 *     string in that language's UI);
 *   - each preset has a DISTINCT description key (presets must not fall
 *     back to echoing their own label as the description).
 */
import { describe, expect, it } from "vitest";

import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";
import {
	AUDIO_PRESET_OPTIONS,
	type AudioPreset,
} from "@/lib/utils/audioPresets";

const LOCALES: Record<string, typeof en> = {
	en,
	ar,
	de,
	es,
	fr,
	hi,
	ru,
	zh,
};

function hasKey(obj: unknown, dottedKey: string): boolean {
	const parts = dottedKey.split(".");
	let cur: unknown = obj;
	for (const p of parts) {
		if (cur && typeof cur === "object" && p in (cur as object)) {
			cur = (cur as Record<string, unknown>)[p];
		} else {
			return false;
		}
	}
	return typeof cur === "string";
}

describe("AUDIO_PRESET_OPTIONS — canonical preset values", () => {
	it("contains exactly the five canonical presets in display order", () => {
		expect(AUDIO_PRESET_OPTIONS.map((o) => o.value)).toEqual([
			"auto",
			"studio",
			"noisy_room",
			"off",
			"custom",
		]);
	});

	it("has no duplicate values", () => {
		const values = AUDIO_PRESET_OPTIONS.map((o) => o.value);
		expect(new Set(values).size).toBe(values.length);
	});

	it("every entry's value is a member of the AudioPreset union", () => {
		// Runtime mirror of the compile-time guarantee: the array is the
		// exhaustive source for the type, so each value must be one of
		// the five union members (guards a future edit that adds a value
		// the type union doesn't know about).
		const union: readonly AudioPreset[] = [
			"auto",
			"studio",
			"noisy_room",
			"off",
			"custom",
		];
		for (const option of AUDIO_PRESET_OPTIONS) {
			expect(union).toContain(option.value);
		}
	});
});

describe("AUDIO_PRESET_OPTIONS — i18n key integrity", () => {
	it("every label key exists in ALL 8 locale catalogues", () => {
		for (const option of AUDIO_PRESET_OPTIONS) {
			const missing: string[] = [];
			for (const [locale, catalogue] of Object.entries(LOCALES)) {
				if (!hasKey(catalogue, option.labelKey)) missing.push(locale);
			}
			expect(
				missing,
				`label key ${option.labelKey} missing in: ${missing.join(", ")}`,
			).toEqual([]);
		}
	});

	it("every description key exists in ALL 8 locale catalogues", () => {
		for (const option of AUDIO_PRESET_OPTIONS) {
			const missing: string[] = [];
			for (const [locale, catalogue] of Object.entries(LOCALES)) {
				if (!hasKey(catalogue, option.descriptionKey)) missing.push(locale);
			}
			expect(
				missing,
				`description key ${option.descriptionKey} missing in: ${missing.join(", ")}`,
			).toEqual([]);
		}
	});

	it("label and description keys are distinct for every preset", () => {
		for (const option of AUDIO_PRESET_OPTIONS) {
			expect(option.descriptionKey).not.toBe(option.labelKey);
		}
	});

	it("no two presets share a label key", () => {
		const labelKeys = AUDIO_PRESET_OPTIONS.map((o) => o.labelKey);
		expect(new Set(labelKeys).size).toBe(labelKeys.length);
	});
});
