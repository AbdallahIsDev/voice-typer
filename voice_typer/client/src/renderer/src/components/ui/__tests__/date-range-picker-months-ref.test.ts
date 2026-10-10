/**
 * Source-shape guard for the picker's month transition.
 *
 * `AnimatePresence mode="popLayout"` takes the LEAVING month out of flow by
 * measuring it in `PopChild.getSnapshotBeforeUpdate` and injecting a
 * `position: absolute !important` rule keyed on `data-motion-pop-id`. It can
 * only reach that element through the ref it clones onto its child:
 *
 *   React.cloneElement(children, { ref: composedRef })
 *   const childRef = pop !== false ? (children.props?.ref ?? children?.ref) : undefined;
 *   ... if (isPresent || pop === false || !ref.current || !width || !height) return;
 *
 * `Months` is that child. When it swallowed the ref, `ref.current` stayed
 * null, nothing was popped, and the leaving month kept its place in normal
 * flow — the panel grew to hold two months (347px -> 623px) and the old
 * month flashed above the new one. Measured in Chromium: 2 months in flow
 * without the forwarding, 1 with it.
 *
 * This cannot be asserted in jsdom: PopChild bails while `width`/`height`
 * are 0, and jsdom has no layout, so the attribute is never applied and the
 * injected rule never materialises. Hence a source-shape assertion, matching
 * the AST/source guards this repo already uses for exactly this kind of
 * "silently degrades in a way the test environment cannot see" contract.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Same source-path convention as src/renderer/src/a11y/accessibility.test.tsx.
const PICKER = path.resolve(__dirname, "..", "date-range-picker.tsx");

/**
 * Whole `function Months(...) { ... }` block, params included, brace-matched
 * from the body's opening `{`.
 */
function monthsBlock(source: string): string {
	const start = source.indexOf("function Months(");
	expect(start, "Months component not found").toBeGreaterThan(-1);
	const open = source.indexOf("{", source.indexOf(")", start));
	let depth = 0;
	for (let i = open; i < source.length; i++) {
		if (source[i] === "{") depth++;
		else if (source[i] === "}") {
			depth--;
			if (depth === 0) return source.slice(start, i + 1);
		}
	}
	throw new Error("unbalanced braces in Months");
}

describe("DateRangePicker month transition contract", () => {
	const source = readFileSync(PICKER, "utf8");
	const block = monthsBlock(source);

	it("Months declares a ref in its props", () => {
		expect(block).toMatch(/ref\?:\s*React\.Ref<HTMLDivElement>/);
		expect(block).toMatch(/^\s*ref,$/m);
	});

	it("Months forwards that ref to the element AnimatePresence measures", () => {
		// Without this the pop never happens and the leaving month stacks
		// under the arriving one instead of being lifted out of flow.
		expect(block).toMatch(/ref=\{ref\}/);
	});

	it("the month set still animates under AnimatePresence mode='popLayout'", () => {
		// The ref forwarding is only load-bearing because of popLayout; if
		// the mode changes, revisit the guard above rather than deleting it.
		expect(source).toMatch(/<AnimatePresence[^>]*mode="popLayout"/);
	});
});
