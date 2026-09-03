/**
 *  (session NH) test: physical Tailwind properties
 * (``ml-``, ``mr-``, ``pl-``, ``pr-``) don't auto-flip in RTL — only
 * logical-property classes (``ms-``, ``me-``, ``ps-``, ``pe-``) do.
 *
 * The renderer sets ``document.documentElement.dir = "rtl"`` for Arabic
 * (see ``i18n.ts:326-337``). Tailwind logical utilities respect this
 * attribute, but physical utilities (``ml-4``, ``pr-8``, etc.) don't —
 * they always render as left/right margin/padding regardless of the
 * document direction.
 *
 * This test asserts the production files in scope for  use ONLY
 * logical-property utilities for inline-axis margin/padding. We use the
 * same static-source-check strategy as ``Dashboard.test.tsx`` /
 * ``accessibility.test.tsx`` because the contracts are visible in the
 * source text (no React render needed).
 *
 * Scope: every file the finding explicitly listed as needing the fix
 * (excluding ``PermissionsStep.tsx`` which Fix-D owns).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", "..");

function readSrc(rel: string): string {
	return readFileSync(resolve(RENDERER_SRC, rel), "utf8");
}

const FILES_UNDER_TEST: { rel: string; label: string }[] = [
	{
		rel: "components/ui/dropdown-menu.tsx",
		label: "dropdown-menu.tsx",
	},
	{
		rel: "components/ui/button.tsx",
		label: "button.tsx",
	},
	{
		rel: "components/models/DownloadProgressBar.tsx",
		label: "DownloadProgressBar.tsx",
	},
	{
		rel: "components/hotkey/HotkeyPicker.tsx",
		label: "HotkeyPicker.tsx",
	},
	{
		rel: "components/settings/GeneralSettingsSection.tsx",
		label: "GeneralSettingsSection.tsx",
	},
	{
		rel: "components/settings/PrivacySettingsSection.tsx",
		label: "PrivacySettingsSection.tsx",
	},
];

describe("NH-11: physical Tailwind properties are replaced with logical (RTL flip)", () => {
	for (const { rel, label } of FILES_UNDER_TEST) {
		describe(`${label}`, () => {
			const src = readSrc(rel);

			it("does not use physical ml-* / mr-* / pl-* / pr-* utilities in inline-axis spacing", () => {
				// Match standalone utility class occurrences like `ml-2`, `mr-4`,
				// `pl-9.5`, `pr-8` (and any ``data-*:ml-X`` variant). The
				// regex deliberately matches the utility prefix so it also
				// catches compound selectors like ``data-inset:pl-9.5`` and
				// ``has-data-[icon=inline-end]:pr-2.5``.
				//
				// Edge cases that should NOT be flagged:
				//   - ``mt-*`` / ``mb-*`` (block-axis — physical is fine,
				//     vertical doesn't flip in RTL).
				//   - ``px-*`` / ``py-*`` (axis-pair utilities — these are
				//     already direction-agnostic).
				//   - The literal substrings inside comments / strings that
				//merely mention the legacy class name in an
				//     migration note (e.g. ``"ml-2 → ms-2"`` in a comment).
				//     We strip /* … */ block comments + // line comments
				//     before matching so only live JSX className values are
				//     checked.
				const stripped = src
					.replace(/\/\*[\s\S]*?\*\//g, "")
					.replace(/\/\/.*$/gm, "");
				// Word-boundary on both sides: matches the utility class
				// itself, not the prefix of a longer identifier.
				const physicalInline = /\b(?:ml|mr|pl|pr)-\d+(?:\.\d+)?\b/;
				// Also check the data-inset:pl-X and has-data-[icon=...]:pr-X variants.
				const physicalInlineWithVariant = /:(?:ml|mr|pl|pr)-\d+(?:\.\d+)?\b/;
				const matches =
					physicalInline.test(stripped) ||
					physicalInlineWithVariant.test(stripped);
				expect(
					matches,
					`${label} still uses a physical inline-axis utility (ml-/mr-/pl-/pr-). ` +
						`Replace with logical (ms-/me-/ps-/pe-) so the value flips in RTL.`,
				).toBe(false);
			});
		});
	}

	it("dropdown-menu.tsx uses data-inset:ps-9.5 (logical) — not data-inset:pl-9.5", () => {
		const src = readSrc("components/ui/dropdown-menu.tsx");
		expect(src).toContain("data-inset:ps-9.5");
		expect(src).not.toContain("data-inset:pl-9.5");
	});

	it("button.tsx uses no physical pr-/pl- and no leftover data-icon padding (normalized to uniform px-3 py-2)", () => {
		const src = readSrc("components/ui/button.tsx");
		// 2026-08-28 button-sizing normalization: every text size variant
		// now shares the same h-fit w-fit px-3 py-2 box, so the old
		// per-variant ``has-data-[icon=inline-end]:pe-*`` /
		// ``has-data-[icon=inline-start]:ps-*`` icon-padding rules were
		// removed (no call site ever set data-icon). Assert they are
		// gone AND that no physical pr-/pl- padding crept in.
		expect(src).not.toMatch(/has-data-\[icon=inline-end\]:pe-\d/);
		expect(src).not.toMatch(/has-data-\[icon=inline-start\]:ps-\d/);
		expect(src).not.toMatch(/has-data-\[icon=inline-end\]:pr-\d/);
		expect(src).not.toMatch(/has-data-\[icon=inline-start\]:pl-\d/);
	});

	it("DownloadProgressBar.tsx uses ms-2 for inline-axis gap (logical)", () => {
		const src = readSrc("components/models/DownloadProgressBar.tsx");
		expect(src).toContain("ms-2");
		expect(src).not.toMatch(/\bml-2\b/);
	});

	it("HotkeyPicker.tsx uses ms-2 for inline-axis gap (logical)", () => {
		const src = readSrc("components/hotkey/HotkeyPicker.tsx");
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).toContain("ms-2");
		expect(stripped).not.toMatch(/\bml-2\b/);
	});

	it("GeneralSettingsSection.tsx uses no physical ml-* / mr-* / pl-* / pr-* utilities (RTL guard)", () => {
		// The Settings Hub migration deleted ModelSettingsSection.tsx
		// (its `w-56 pe-8` API-key input moved with it) — this suite
		// keeps a settings-section file in scope via
		// GeneralSettingsSection.tsx instead, pinned to the same
		// logical-utilities-only contract the FILES_UNDER_TEST loop
		// asserts for it.
		const src = readSrc("components/settings/GeneralSettingsSection.tsx");
		// Word-boundary regex on the same stripped source the loop test
		// uses — no physical inline-axis utilities may appear.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toMatch(/\b(?:ml|mr|pl|pr)-\d+(?:\.\d+)?\b/);
	});

	it("PrivacySettingsSection.tsx uses ps-4 for the consent-banner list-disc indent (logical)", () => {
		const src = readSrc("components/settings/PrivacySettingsSection.tsx");
		expect(src).toContain("list-disc ps-4");
		expect(src).not.toContain("list-disc pl-4");
	});
});
