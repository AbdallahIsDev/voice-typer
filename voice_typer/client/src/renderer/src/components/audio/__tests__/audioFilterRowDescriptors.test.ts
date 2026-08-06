/**
 * unit tests for `components/audio/audioFilterRowDescriptors.ts` —
 * the static registry of every distinct SettingRow rendered by
 * `<AudioFilterChain>`.
 *
 * The registry is a single source of truth: the JSX in `<AudioFilterChain>`
 * is a `.map` over `audioFilterRowDescriptors`, and the lookup Map
 * `audioFilterDescriptorByConfigKey` is derived from the array. Both
 * exports are `const`s evaluated once at module load — they have STABLE
 * identities across re-imports (no factory function involved), which is
 * what makes the registry safe to share across the render graph without
 * a `useMemo` wrapper.
 *
 * Coverage:
 *   1. Stable identities: importing the module twice yields the same
 *      array reference AND the same Map reference (referential equality).
 *      A future refactor that turns the registry into a factory function
 *      returning a fresh array on every call would break the memoisation
 *      invariant in `<AudioFilterChain>` — this test pins the contract.
 *   2. Descriptor identity: each element of the array has a stable
 *      reference (the descriptors themselves are not rebuilt per access).
 *   3. `audioFilterDescriptorByConfigKey` is in sync with the array:
 *      same size, every configKey resolves, no orphan entries.
 *   4. Uniqueness invariants: every `configKey` is unique (no two rows
 *      read/write the same config field), every `labelKey` / `ariaKey` /
 *      `infoKey` is unique (so the labels dictionary doesn't silently
 *      overwrite one row's strings with another's).
 *   5. parentToggle integrity: every `parentToggle` references a
 *      `configKey` whose descriptor has `kind: "toggle"` (you can't
 *      nest a slider under another slider).
 *   6. Kind-specific field requirements:
 *      - `kind: "slider"` MUST set `min`, `max`, `step`, `suffix`.
 *      - `kind: "select"` MUST set `options` with at least one entry.
 *      - `kind: "toggle"` MUST have `defaultValue: boolean`.
 *   7. Section title is shared across all descriptors (single source of
 *      truth for the Audio Enhancement section heading).
 */
import { describe, expect, it } from "vitest";

import {
	AUDIO_SECTION_TITLE_KEY,
	type AudioFilterRowDescriptor,
	audioFilterDescriptorByConfigKey,
	audioFilterRowDescriptors,
} from "../audioFilterRowDescriptors";

describe("audioFilterRowDescriptors — stable identities", () => {
	it("the array reference is stable across module re-imports (no factory)", async () => {
		const mod1 = await import("../audioFilterRowDescriptors");
		const mod2 = await import("../audioFilterRowDescriptors");

		// Same module instance → same exports (Node/Vitest cache).
		expect(mod1.audioFilterRowDescriptors).toBe(mod2.audioFilterRowDescriptors);
		expect(mod1.audioFilterDescriptorByConfigKey).toBe(
			mod2.audioFilterDescriptorByConfigKey,
		);
		// The exported const arrays/maps are referentially identical
		// to the live bindings on the module namespace — i.e. they
		// are NOT wrapped in a factory that returns a fresh copy.
		expect(mod1.audioFilterRowDescriptors).toBe(audioFilterRowDescriptors);
	});

	it("individual descriptor elements have stable references (no per-access clones)", () => {
		// Two index accesses of the same slot return the SAME object
		// reference (the array doesn't synthesise fresh descriptor
		// objects on read).
		const first = audioFilterRowDescriptors[0];
		const firstAgain = audioFilterRowDescriptors[0];
		expect(first).toBe(firstAgain);

		// Iterating twice yields the same element references in order.
		const pass1 = [...audioFilterRowDescriptors];
		const pass2 = [...audioFilterRowDescriptors];
		expect(pass1.length).toBe(pass2.length);
		for (let i = 0; i < pass1.length; i++) {
			expect(pass1[i]).toBe(pass2[i]);
		}
	});

	it("the lookup Map reference is stable across re-imports", async () => {
		const mod1 = await import("../audioFilterRowDescriptors");
		const mod2 = await import("../audioFilterRowDescriptors");
		expect(mod1.audioFilterDescriptorByConfigKey).toBe(
			mod2.audioFilterDescriptorByConfigKey,
		);
	});
});

describe("audioFilterRowDescriptors — registry / map parity", () => {
	it("the Map has exactly one entry per descriptor (no orphans, no dupes)", () => {
		expect(audioFilterDescriptorByConfigKey.size).toBe(
			audioFilterRowDescriptors.length,
		);
	});

	it("every descriptor's configKey resolves via the Map", () => {
		for (const d of audioFilterRowDescriptors) {
			const looked = audioFilterDescriptorByConfigKey.get(
				d.configKey as string,
			);
			expect(looked).toBe(d); // referential equality, not just shape
		}
	});

	it("the Map and the array agree on length (no orphan entries)", () => {
		// If a future edit adds an entry to the Map but forgets to
		// push to the array (or vice versa), this catches it.
		expect(audioFilterDescriptorByConfigKey.size).toBe(
			audioFilterRowDescriptors.length,
		);
	});
});

describe("audioFilterRowDescriptors — uniqueness invariants", () => {
	it("every configKey is unique (no two rows write the same config field)", () => {
		const keys = audioFilterRowDescriptors.map((d) => d.configKey as string);
		const uniq = new Set(keys);
		expect(uniq.size).toBe(keys.length);
	});

	it("every labelKey is unique (labels dict doesn't silently overwrite)", () => {
		const keys = audioFilterRowDescriptors.map((d) => d.labelKey);
		const uniq = new Set(keys);
		expect(uniq.size).toBe(keys.length);
	});

	it("every ariaKey is unique (each control has a distinct SR name)", () => {
		const keys = audioFilterRowDescriptors.map((d) => d.ariaKey);
		const uniq = new Set(keys);
		expect(uniq.size).toBe(keys.length);
	});

	it("every infoKey is unique (each InfoTooltip resolves to a distinct string)", () => {
		const keys = audioFilterRowDescriptors.map((d) => d.infoKey);
		const uniq = new Set(keys);
		expect(uniq.size).toBe(keys.length);
	});

	it("every infoSearchKey is unique (search visibility check is per-row)", () => {
		const keys = audioFilterRowDescriptors.map((d) => d.infoSearchKey);
		const uniq = new Set(keys);
		expect(uniq.size).toBe(keys.length);
	});
});

describe("audioFilterRowDescriptors — parentToggle integrity", () => {
	it("every parentToggle references a configKey whose descriptor has kind='toggle'", () => {
		for (const d of audioFilterRowDescriptors) {
			if (!d.parentToggle) continue;
			const parent = audioFilterDescriptorByConfigKey.get(
				d.parentToggle as string,
			);
			expect(parent).toBeDefined();
			expect(parent?.kind).toBe("toggle");
		}
	});

	it("every descriptor with a parentToggle has kind='slider' or 'select' (you can't nest a toggle)", () => {
		for (const d of audioFilterRowDescriptors) {
			if (!d.parentToggle) continue;
			expect(d.kind === "slider" || d.kind === "select").toBe(true);
		}
	});
});

describe("audioFilterRowDescriptors — kind-specific field requirements", () => {
	it("every slider descriptor has min, max, step, and suffix set", () => {
		for (const d of audioFilterRowDescriptors) {
			if (d.kind !== "slider") continue;
			expect(typeof d.min).toBe("number");
			expect(typeof d.max).toBe("number");
			expect(typeof d.step).toBe("number");
			expect(typeof d.suffix).toBe("string");
			expect((d.suffix ?? "").length).toBeGreaterThan(0);
			// Numeric sanity: min <= max, step > 0.
			expect(d.min as number).toBeLessThanOrEqual(d.max as number);
			expect(d.step as number).toBeGreaterThan(0);
			// defaultValue is a number for sliders.
			expect(typeof d.defaultValue).toBe("number");
		}
	});

	it("every select descriptor has a non-empty options array", () => {
		for (const d of audioFilterRowDescriptors) {
			if (d.kind !== "select") continue;
			expect(d.options).toBeDefined();
			expect((d.options ?? []).length).toBeGreaterThan(0);
			// defaultValue is a string for selects.
			expect(typeof d.defaultValue).toBe("string");
			// Every option has a value, and exactly one of label/labelKey.
			for (const opt of d.options ?? []) {
				expect(typeof opt.value).toBe("string");
				expect(opt.value.length).toBeGreaterThan(0);
				const hasLabel = typeof opt.label === "string";
				const hasLabelKey = typeof opt.labelKey === "string";
				// Mutually exclusive but exactly one set.
				expect(hasLabel !== hasLabelKey).toBe(true);
			}
		}
	});

	it("every toggle descriptor has a boolean defaultValue", () => {
		for (const d of audioFilterRowDescriptors) {
			if (d.kind !== "toggle") continue;
			expect(typeof d.defaultValue).toBe("boolean");
			// Toggles don't carry slider-specific fields.
			expect(d.min).toBeUndefined();
			expect(d.max).toBeUndefined();
			expect(d.step).toBeUndefined();
			expect(d.suffix).toBeUndefined();
			// Toggles don't carry select-specific fields.
			expect(d.options).toBeUndefined();
		}
	});
});

describe("audioFilterRowDescriptors — section title contract", () => {
	it("every descriptor references the SAME sectionTitleKey (single source of truth)", () => {
		for (const d of audioFilterRowDescriptors) {
			expect(d.sectionTitleKey).toBe(AUDIO_SECTION_TITLE_KEY);
		}
	});

	it("AUDIO_SECTION_TITLE_KEY is the dotted i18n key for the section heading", () => {
		expect(AUDIO_SECTION_TITLE_KEY).toBe("settings.audioEnhancement.title");
	});
});

describe("audioFilterRowDescriptors — registry sanity", () => {
	it("registry is non-empty", () => {
		expect(audioFilterRowDescriptors.length).toBeGreaterThan(0);
	});

	it("every descriptor has all required base fields populated", () => {
		// Compile-time check via the type system, but assert at runtime
		// too so a `null`/`undefined` slip (e.g. an accidental `?` on a
		// required field) is caught at test time, not in production.
		for (const d of audioFilterRowDescriptors as AudioFilterRowDescriptor[]) {
			expect(typeof d.labelKey).toBe("string");
			expect(d.labelKey.length).toBeGreaterThan(0);
			expect(typeof d.infoSearchKey).toBe("string");
			expect(d.infoSearchKey.length).toBeGreaterThan(0);
			expect(typeof d.sectionTitleKey).toBe("string");
			expect(typeof d.configKey).toBe("string");
			expect(d.configKey.length).toBeGreaterThan(0);
			expect(
				d.kind === "toggle" || d.kind === "slider" || d.kind === "select",
			).toBe(true);
			expect(typeof d.infoKey).toBe("string");
			expect(typeof d.ariaKey).toBe("string");
			// defaultValue is set for every kind (boolean/number/string).
			expect(d.defaultValue).not.toBeUndefined();
		}
	});
});
