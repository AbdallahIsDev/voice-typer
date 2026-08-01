/**
 *  (session NH) test: ``<Button variant="warning">`` applies the
 * warning cva variant and exposes ``data-variant="warning"``.
 *
 * Before , ``ConfirmDialog`` accepted ``variant="warning"`` on its
 * props but the implementation silently fell through to ``"default"``
 * styling because ``button.tsx``'s cva had no ``warning`` entry. The
 * skip-onboarding confirmation (the only ``warning`` caller) rendered
 * with the default blue primary styling — no visual signal that skipping
 * is a warning-tier action.
 *
 *  added a ``warning`` variant to ``button.tsx``'s cva (amber-tinted,
 * using the ``--warning`` design token from ) and wired
 * ``ConfirmDialog`` to pass it through.
 *
 * This test pins the cva contract: rendering ``<Button variant="warning">``
 * produces a button whose ``data-variant`` attribute is ``"warning"`` and
 * whose ``className`` contains the ``bg-warning`` utility (proving the
 * amber token is applied, not the default primary blue).
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Button, buttonVariants } from "@/components/ui/button";

afterEach(() => {
	cleanup();
});

describe("NH-14: Button warning variant", () => {
	it("renders with data-variant='warning' when variant='warning'", () => {
		const { container } = render(<Button variant="warning">Skip</Button>);
		const btn = container.querySelector("button");
		expect(btn).toBeTruthy();
		expect(btn?.getAttribute("data-variant")).toBe("warning");
	});

	it("className includes bg-warning tint (the warning design token, not primary blue)", () => {
		const { container } = render(<Button variant="warning">Skip</Button>);
		const btn = container.querySelector("button");
		expect(btn?.className).toMatch(/bg-warning\/15/);
		expect(btn?.className).toMatch(/text-warning/);
	});

	it("buttonVariants() helper accepts 'warning' and returns a non-empty class string", () => {
		const cls = buttonVariants({ variant: "warning" });
		expect(typeof cls).toBe("string");
		expect(cls.length).toBeGreaterThan(0);
		expect(cls).toContain("bg-warning");
	});

	it("default variant is unchanged (no warning class leaks when variant is default)", () => {
		const { container } = render(<Button variant="default">OK</Button>);
		const btn = container.querySelector("button");
		expect(btn?.className).not.toMatch(/bg-warning/);
		expect(btn?.getAttribute("data-variant")).toBe("default");
	});
});
