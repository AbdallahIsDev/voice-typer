/**
 * Guard: every `tf(key, fallback)` literal in BubbleModeContent.tsx
 * must match the en.json value for that key.
 *
 * The `tf()` fallbacks exist for dictionary-load failure only. Without
 * this guard, a copy edit to en.json silently leaves the hardcoded
 * fallback contradicting the live translation (two sources of truth).
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import en from "@/i18n/translations/en.json";

const componentSource = readFileSync(
	join(dirname(fileURLToPath(import.meta.url)), "../BubbleModeContent.tsx"),
	"utf8",
);

// Matches tf("some.key", "Fallback literal") — single-line form only,
// which is the pattern used in this component.
const TF_CALL_RE = /tf\(\s*"([a-zA-Z0-9_.]+)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)/g;

describe("BubbleModeContent tf() fallbacks match en.json", () => {
	it("every tf(key, fallback) equals the en translation", () => {
		const mismatches: string[] = [];
		let checked = 0;
		for (const match of componentSource.matchAll(TF_CALL_RE)) {
			const key = match[1];
			const fallback = match[2];
			if (key === undefined || fallback === undefined) continue;
			checked += 1;
			const expected = key
				.split(".")
				.reduce<unknown>(
					(acc, part) =>
						typeof acc === "object" && acc !== null
							? (acc as Record<string, unknown>)[part]
							: undefined,
					en,
				);
			if (expected !== fallback) {
				mismatches.push(
					`${key}: fallback="${fallback}" en.json="${String(expected)}"`,
				);
			}
		}
		expect(checked).toBeGreaterThan(0);
		expect(mismatches).toEqual([]);
	});
});
