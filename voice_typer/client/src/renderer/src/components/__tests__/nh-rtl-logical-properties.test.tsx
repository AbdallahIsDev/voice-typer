/**
 * NH-11 (session NH) test: physical Tailwind properties
 * (``ml-``, ``mr-``, ``pl-``, ``pr-``) don't auto-flip in RTL — only
 * logical-property classes (``ms-``, ``me-``, ``ps-``, ``pe-``) do.
 *
 * The renderer sets ``document.documentElement.dir = "rtl"`` for Arabic
 * (see ``i18n.ts:326-337``). Tailwind logical utilities respect this
 * attribute, but physical utilities (``ml-4``, ``pr-8``, etc.) don't —
 * they always render as left/right margin/padding regardless of the
 * document direction.
 *
 * This test asserts the production files in scope for NH-11 use ONLY
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
		rel: "components/settings/ModelSettingsSection.tsx",
		label: "ModelSettingsSection.tsx",
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
				//     merely mention the legacy class name in an NH-11
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

	it("button.tsx uses has-data-[icon=inline-end]:pe-* and has-data-[icon=inline-start]:ps-* (logical)", () => {
		const src = readSrc("components/ui/button.tsx");
		expect(src).toMatch(/has-data-\[icon=inline-end\]:pe-\d/);
		expect(src).toMatch(/has-data-\[icon=inline-start\]:ps-\d/);
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

	it("ModelSettingsSection.tsx uses pe-8 (logical) for the API-key input padding", () => {
		const src = readSrc("components/settings/ModelSettingsSection.tsx");
		// Only assert that the specific pr-8 instance on the API-key input
		// is gone — the file may legitimately use other physical utilities
		// elsewhere (we don't run a full codemod on the whole file).
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// The finding cited ``className="w-56 pr-8"`` on the Input for the
		// API key. Assert that exact pair is gone (replaced with pe-8).
		expect(stripped).not.toMatch(/w-56\s+pr-8/);
		expect(stripped).toMatch(/w-56\s+pe-8/);
	});

	it("PrivacySettingsSection.tsx uses ps-4 for the consent-banner list-disc indent (logical)", () => {
		const src = readSrc("components/settings/PrivacySettingsSection.tsx");
		expect(src).toContain("list-disc ps-4");
		expect(src).not.toContain("list-disc pl-4");
	});
});
