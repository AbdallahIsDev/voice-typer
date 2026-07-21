// @vitest-environment node
/**
 * R6-F8 unit tests for `electron-builder.yml`.
 *
 * Verifies that `python3` is NOT listed in `deb.depends` or
 * `rpm.depends` (the bundled PyInstaller backend embeds its own
 * interpreter, so the system python3 package is unnecessary bloat).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Minimal YAML parser — the file uses a constrained subset (lists of
// scalars, nested maps) so we don't need a full YAML dep. We just
// assert on the raw text for `python3` absence and structural shape.
const BUILDER_YML = readFileSync(
	resolve(__dirname, "../../../electron-builder.yml"),
	"utf-8",
);

describe("R6-F8: electron-builder.yml has no python3 dependency", () => {
	it("deb.depends block does not list python3", () => {
		// Find the `deb:` section.
		const debIdx = BUILDER_YML.indexOf("\ndeb:");
		expect(debIdx).toBeGreaterThan(-1);
		const rpmIdx = BUILDER_YML.indexOf("\nrpm:", debIdx);
		const debSection = BUILDER_YML.slice(
			debIdx,
			rpmIdx > debIdx ? rpmIdx : undefined,
		);
		// `python3` may appear in comments (we have a removal-rationale
		// comment). We assert it does NOT appear as a list item.
		// A list item under deb.depends would be `    - python3\n` (4-space
		// indent + dash + space + value).
		const listItem = /^\s+-\s+python3\s*$/m;
		expect(listItem.test(debSection)).toBe(false);
	});

	it("rpm.depends block does not list python3", () => {
		const rpmIdx = BUILDER_YML.indexOf("\nrpm:");
		expect(rpmIdx).toBeGreaterThan(-1);
		const rpmSection = BUILDER_YML.slice(rpmIdx);
		const listItem = /^\s+-\s+python3\s*$/m;
		expect(listItem.test(rpmSection)).toBe(false);
	});

	it("deb.depends still contains libnotify4 and libxtst6 (no over-pruning)", () => {
		const debIdx = BUILDER_YML.indexOf("\ndeb:");
		const rpmIdx = BUILDER_YML.indexOf("\nrpm:", debIdx);
		const debSection = BUILDER_YML.slice(debIdx, rpmIdx);
		expect(debSection).toMatch(/^\s+-\s+libnotify4\s*$/m);
		expect(debSection).toMatch(/^\s+-\s+libxtst6\s*$/m);
	});

	it("rpm.depends still contains libnotify and libXtst (no over-pruning)", () => {
		const rpmIdx = BUILDER_YML.indexOf("\nrpm:");
		const rpmSection = BUILDER_YML.slice(rpmIdx);
		expect(rpmSection).toMatch(/^\s+-\s+libnotify\s*$/m);
		expect(rpmSection).toMatch(/^\s+-\s+libXtst\s*$/m);
	});

	it("mentions the R6-F8 rationale (so the removal is intentional)", () => {
		expect(BUILDER_YML).toContain("R6-F8");
	});
});
