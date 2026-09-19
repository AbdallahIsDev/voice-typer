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
