// @vitest-environment node
/**
 * HU-30 regression test: a corrupted locale JSON file must never kill
 * app launch.
 *
 * Pre-fix: `_loadLocaleJson` read `i18n/locales/<locale>.json` via
 * `readFileSync` with no try/catch — a single corrupted locale file
 * threw an uncaught exception at module load and killed the app before
 * the UI could mount.
 *
 * Post-fix: the locale tables are statically imported (bundled by
 * Vite/Rollup into `out/main/index.js`) and `_loadLocaleJson` is a pure
 * in-memory lookup (`LOCALE_TABLES[locale] ?? {}`) that never touches
 * the filesystem. There is no runtime read that a corrupted on-disk
 * file could break.
 *
 * This test mocks `fs.readFileSync` to THROW (simulating a corrupted
 * locale file on disk) and asserts the i18n module still imports and
 * resolves dialog keys — pinning the "no runtime fs read" contract so
 * a future revert to `readFileSync` is caught immediately.
 */
import { describe, expect, it, vi } from "vitest";

// Simulate a corrupted locale file on disk. If i18n.ts ever regresses
// to reading locale JSON via readFileSync at module load, this mock
// throws and the imports below fail the test.
vi.mock("node:fs", async (importOriginal) => {
	const actual = await importOriginal<typeof import("node:fs")>();
	return {
		...actual,
		readFileSync: () => {
			throw new Error("HU-30: simulated corrupted locale file");
		},
	};
});

import { mainT, setMainLocale } from "../i18n";

const LOCALES = ["en", "es", "ar", "de", "fr", "hi", "ru", "zh"] as const;

describe("HU-30: corrupted locale file cannot kill app launch", () => {
	it("the i18n module imports cleanly even when fs reads throw", async () => {
		// Dynamic import forces module evaluation; both the static
		// imports above and this re-import must succeed despite the
		// broken fs mock.
		vi.resetModules();
		const mod = await import("../i18n");
		expect(typeof mod.mainT).toBe("function");
		expect(typeof mod.setMainLocale).toBe("function");
	});

	it("mainT resolves dialog keys for every locale despite the corrupt fs", () => {
		try {
			for (const locale of LOCALES) {
				setMainLocale(locale);
				const title = mainT("dialog.criticalError.title");
				// Resolves to a real string — never the raw key, never a
				// throw.
				expect(title.length).toBeGreaterThan(0);
				expect(title).not.toBe("dialog.criticalError.title");
			}
		} finally {
			// Restore the default locale even on assertion failure so
			// module state can't leak into later tests.
			setMainLocale("en");
		}
	});
});
