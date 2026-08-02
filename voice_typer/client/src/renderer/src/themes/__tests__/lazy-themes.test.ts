/**
 * regression test: ``themes/index.ts`` MUST statically import ONLY
 * the ``default`` and ``custom`` presets. The 10 non-default/non-custom
 * presets MUST be loaded ON DEMAND via the ``lazyThemeLoaders``
 * registry (dynamic ``import()``).
 *
 * Background: the previous code had 12 static top-level imports in
 * ``themes/index.ts``, one per preset. Each preset module is small
 * (~60 CSS variable strings), but 10 × 60 = ~600 strings shipped
 * eagerly in the initial renderer bundle even though only the active
 * preset's vars are applied at any time.
 *
 * The refactor replaced the 10 preset imports with a
 * ``Record<string, () => Promise<...>>`` registry of dynamic
 * ``import()`` loaders. Vite emits each preset as a SEPARATE async
 * chunk; ``loadThemePreset(id)`` populates the ``THEMES`` entry in
 * place. ``default`` and ``custom`` remain statically imported (they're
 * the fallback pair — both are no-ops with empty light/dark maps).
 *
 * This test does a STATIC source analysis (reads the file as text and
 * regex-matches) to verify the import structure. It also does a
 * FUNCTIONAL test that ``loadThemePreset`` actually populates a lazy
 * preset's light/dark maps.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const THEMES_INDEX_SRC = readFileSync(
	resolve(__dirname, "../index.ts"),
	"utf-8",
);

describe("themes/index.ts lazy-load registry", () => {
	describe("static imports (source analysis)", () => {
		it("statically imports `default`", () => {
			// `default` is one of the two eagerly-loaded presets (the
			// fallback pair). It MUST be statically imported so the
			// renderer always has a valid preset without an async fetch.
			expect(THEMES_INDEX_SRC).toMatch(
				/import\s+\{\s*defaultTheme\s*\}\s+from\s+["']\.\/default["']/,
			);
		});

		it("statically imports `custom`", () => {
			// `custom` is the other eagerly-loaded preset. Its light/dark
			// maps are empty (the actual vars are derived at runtime via
			// `deriveCustomVars`), but the entry must exist statically so
			// `applyThemeVars("custom", ...)` works without a dynamic
			// import.
			expect(THEMES_INDEX_SRC).toMatch(
				/import\s+\{\s*customTheme\s*\}\s+from\s+["']\.\/custom["']/,
			);
		});

		const LAZY_PRESETS = [
			"amoled",
			"nord",
			"dracula",
			"sepia",
			"monokai",
			"ayu",
			"github",
			"catppuccin",
			"tokyo-night",
			"solarized",
		];

		for (const preset of LAZY_PRESETS) {
			it(`does NOT statically import \`${preset}\``, () => {
				// A static import looks like:
				//   import { amoledTheme } from "./amoled";
				// The regex matches any static import (named or default)
				// from `./<preset>`. The dynamic `import("./amoled")`
				// form (inside the LAZY_PRESETS loader array) uses
				// `import(` (parenthesis, no whitespace) so it does NOT
				// match this regex.
				//
				// We assert each lazy preset is NOT statically imported
				// — it should only appear as a dynamic `import("./<id>")`
				// inside the loader registry.
				const staticImportRe = new RegExp(
					`^\\s*import\\s+.*from\\s+["']\\./${preset.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}["']`,
					"m",
				);
				expect(THEMES_INDEX_SRC).not.toMatch(staticImportRe);
			});
		}
	});

	describe("lazy registry (source analysis)", () => {
		it("exports a `lazyThemeLoaders` registry", () => {
			expect(THEMES_INDEX_SRC).toMatch(/export\s+const\s+lazyThemeLoaders/);
		});

		it("exports a `loadThemePreset` function", () => {
			expect(THEMES_INDEX_SRC).toMatch(
				/export\s+async\s+function\s+loadThemePreset/,
			);
		});

		it("registry uses dynamic `import()` for each lazy preset", () => {
			// Each lazy preset's loader is `() => import("./<id>")`.
			// Assert at least one dynamic import call exists in the
			// registry (the per-preset assertions above already verify
			// no STATIC imports; this verifies the dynamic form is
			// present).
			expect(THEMES_INDEX_SRC).toMatch(/loader:\s*\(\)\s*=>\s*import\(/);
		});
	});

	describe("functional: loadThemePreset populates lazy preset vars", () => {
		it("loadThemePreset('amoled') populates the amoled entry's light/dark maps", async () => {
			// Import dynamically so the test doesn't load themes/index.ts
			// before the source-analysis tests above run.
			const { THEMES, loadThemePreset } = await import("@/themes/index");
			const amoled = THEMES.find((t) => t.id === "amoled");
			expect(amoled).toBeDefined();
			// Before load: light/dark are empty (metadata-only entry).
			expect(amoled?.light).toEqual({});
			expect(amoled?.dark).toEqual({});

			await loadThemePreset("amoled");

			// After load: light/dark are populated with the preset's
			// CSS variable map (non-empty).
			expect(Object.keys(amoled?.light ?? {}).length).toBeGreaterThan(0);
			expect(Object.keys(amoled?.dark ?? {}).length).toBeGreaterThan(0);
			// Spot-check a known amoled var (true-black background).
			expect(amoled?.dark["--background"]).toBeTruthy();
		});

		it("loadThemePreset is idempotent (second call is a no-op)", async () => {
			const { THEMES, loadThemePreset } = await import("@/themes/index");
			const nord = THEMES.find((t) => t.id === "nord");
			expect(nord).toBeDefined();

			await loadThemePreset("nord");
			const firstLight = { ...nord?.light };
			const firstDark = { ...nord?.dark };

			// Second call — should NOT re-import or mutate (the
			// `loadedLazyPresets` set short-circuits).
			await loadThemePreset("nord");
			expect(nord?.light).toEqual(firstLight);
			expect(nord?.dark).toEqual(firstDark);
		});

		it("loadThemePreset('default') and loadThemePreset('custom') are instant no-ops", async () => {
			const { loadThemePreset } = await import("@/themes/index");
			// These should resolve immediately without throwing —
			// `default` and `custom` are statically imported, so
			// `loadThemePreset` short-circuits for them.
			await expect(loadThemePreset("default")).resolves.toBeUndefined();
			await expect(loadThemePreset("custom")).resolves.toBeUndefined();
		});

		it("loadThemePreset(unknownId) is a no-op (logs warning, does not throw)", async () => {
			const { loadThemePreset } = await import("@/themes/index");
			// Unknown id — should resolve without throwing (defensive).
			await expect(
				loadThemePreset("nonexistent-preset"),
			).resolves.toBeUndefined();
		});
	});
});
