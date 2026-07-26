/**
 * Focus-ring WCAG 1.4.11 regression test.
 *
 * The interactive primitives (``Button``, ``Input``, ``SelectTrigger``)
 * previously declared ``focus-visible:ring-ring/30`` — a 30% alpha ring
 * composited over the surface behind it. Programmatic WCAG audit found
 * the composite contrast sat at 1.15:1–2.45:1 across all 12 themes —
 * far below the WCAG 1.4.11 "Non-text Contrast" 3:1 minimum, so the
 * focus indicator was effectively invisible in every theme.
 *
 * The fix is to drop the ``/30`` alpha modifier so the ring paints at
 * the full ``--ring`` token opacity (which the theme files tune for
 * 3:1+ contrast against the surface). This test pins the contract:
 *
 * 1. The focus-ring utility class on each primitive is ``ring-ring``
 *    (full opacity), not ``ring-ring/30`` (or any other alpha-prefixed
 *    variant that would re-introduce the same composite-contrast
 *    failure).
 * 2. The focus ring thickness (``ring-3``) and the ``focus-visible:``
 *    qualifier are preserved — we are only tightening the alpha, not
 *    re-architecting the focus indicator.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectTrigger } from "@/components/ui/select";

afterEach(() => {
	cleanup();
});

/** Extract the ``focus-visible:ring-ring`` token from a class string. */
function extractFocusRingToken(cls: string | undefined | null): string {
	if (!cls) return "";
	// Match `focus-visible:ring-ring` optionally followed by `/<n>`
	// alpha modifier, terminated by whitespace or end-of-string.
	const m = cls.match(/focus-visible:ring-ring(?:\/\d+)?(?=\s|$)/);
	return m ? m[0] : "";
}

describe("focus ring WCAG 1.4.11 (3:1) — full-opacity ring-ring", () => {
	describe("Button", () => {
		it("rendered className uses ring-ring at full opacity (no /30 alpha)", () => {
			const { container } = render(<Button variant="default">OK</Button>);
			const btn = container.querySelector("button");
			expect(btn).toBeTruthy();
			const cls = btn?.className ?? "";
			// Positive contract: focus ring is the full-opacity token.
			expect(cls).toMatch(/focus-visible:ring-ring(\s|$)/);
			// Negative contract: the 30% alpha modifier that caused the
			// 1.15:1–2.45:1 composite-contrast failure is gone.
			expect(cls).not.toMatch(/focus-visible:ring-ring\/30/);
			// Any alpha modifier on the focus ring is suspect — flag it
			// so a future edit that re-introduces /N alpha is caught.
			expect(cls).not.toMatch(/focus-visible:ring-ring\/\d+/);
		});

		it("preserves focus ring thickness (ring-3) and focus-visible qualifier", () => {
			const { container } = render(<Button variant="default">OK</Button>);
			const cls = container.querySelector("button")?.className ?? "";
			// Thickness was never the bug — the bug was the alpha. We
			// pin the thickness so a future "fix" that drops ring-3
			// to ring-2 (to "compensate" for full opacity) doesn't
			// silently regress the focus indicator visibility.
			expect(cls).toMatch(/focus-visible:ring-3/);
			expect(cls).toMatch(/focus-visible:border-ring/);
			expect(cls).toMatch(/focus-visible:ring-ring/);
		});

		it("every variant inherits the full-opacity focus ring (cva base layer)", () => {
			// The focus ring is declared in the cva BASE layer, so
			// every variant inherits it. Verify by spot-checking a
			// representative slice of variants via the cva helper
			// (which returns the exact class string a render would).
			for (const variant of [
				"default",
				"outline",
				"secondary",
				"ghost",
				"destructive",
				"warning",
				"link",
			] as const) {
				const cls = buttonVariants({ variant });
				const token = extractFocusRingToken(cls);
				expect(
					token,
					`variant=${variant} should have focus-visible:ring-ring token`,
				).toBe("focus-visible:ring-ring");
				expect(
					cls,
					`variant=${variant} must not carry ring-ring/30`,
				).not.toMatch(/focus-visible:ring-ring\/30/);
			}
		});
	});

	describe("Input", () => {
		it("rendered className uses ring-ring at full opacity (no /30 alpha)", () => {
			const { container } = render(<Input type="text" />);
			const input = container.querySelector("input");
			expect(input).toBeTruthy();
			const cls = input?.className ?? "";
			expect(cls).toMatch(/focus-visible:ring-ring(\s|$)/);
			expect(cls).not.toMatch(/focus-visible:ring-ring\/30/);
			expect(cls).not.toMatch(/focus-visible:ring-ring\/\d+/);
		});

		it("preserves focus ring thickness (ring-3)", () => {
			const { container } = render(<Input type="text" />);
			const cls = container.querySelector("input")?.className ?? "";
			expect(cls).toMatch(/focus-visible:ring-3/);
		});
	});

	describe("SelectTrigger", () => {
		it("rendered className uses ring-ring at full opacity (no /30 alpha)", () => {
			const { container } = render(
				<Select>
					<SelectTrigger>
						<span>placeholder</span>
					</SelectTrigger>
				</Select>,
			);
			const trigger = container.querySelector("[data-slot='select-trigger']");
			expect(trigger).toBeTruthy();
			const cls = trigger?.className ?? "";
			expect(cls).toMatch(/focus-visible:ring-ring(\s|$)/);
			expect(cls).not.toMatch(/focus-visible:ring-ring\/30/);
			expect(cls).not.toMatch(/focus-visible:ring-ring\/\d+/);
		});

		it("preserves focus ring thickness (ring-3)", () => {
			const { container } = render(
				<Select>
					<SelectTrigger>
						<span>placeholder</span>
					</SelectTrigger>
				</Select>,
			);
			const cls =
				container.querySelector("[data-slot='select-trigger']")?.className ??
				"";
			expect(cls).toMatch(/focus-visible:ring-3/);
		});
	});
});
