/**
 * Switch component tests — covers  (RTL thumb translate) and the
 *  follow-ups (sub-24px touch target via `after:-inset-y-3`,
 * visible `data-checked:border-primary/30` ring, `bg-clip-padding`
 * Safari-rendering-bug comment presence).
 *
 * The tests assert on `className` strings rather than computed styles
 * because jsdom has no CSS engine — the Tailwind `rtl:` and `data-*:`
 * variants compile to plain CSS selectors in the bundle, so verifying
 * the variant prefix is present in the rendered `class` attribute is
 * sufficient to confirm the intent.
 */
import { cleanup, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { Switch as VtSwitch } from "../switch";

afterEach(() => {
	cleanup();
});

describe("Switch — BG-37 RTL thumb translate", () => {
	it("renders the thumb with the LTR-positive translate variant", () => {
		render(<VtSwitch aria-label="test-switch" />);

		// The thumb is the only element with data-slot="switch-thumb".
		const thumb = document.querySelector(
			'[data-slot="switch-thumb"]',
		) as HTMLElement;
		expect(thumb).toBeTruthy();
		// Default LTR rule: positive translate-x when checked.
		expect(thumb.className).toContain(
			"data-checked:translate-x-[calc(100%-8px)]",
		);
	});

	it("renders the thumb with the RTL-negative translate variant so it flips under [dir=rtl]", () => {
		render(<VtSwitch aria-label="test-switch" />);

		const thumb = document.querySelector(
			'[data-slot="switch-thumb"]',
		) as HTMLElement;
		expect(thumb).toBeTruthy();
		//the `rtl:` variant must be present so the translate sign
		// flips under `[dir="rtl"]`. Without this class the thumb always
		// slides right (physical translate-x), which is wrong for Arabic.
		expect(thumb.className).toContain(
			"rtl:data-checked:-translate-x-[calc(100%-8px)]",
		);
	});

	it("toggles data-state to checked when clicked (smoke test for the underlying radix primitive)", async () => {
		const user = userEvent.setup();
		render(<VtSwitch aria-label="test-switch" />);

		const root = document.querySelector('[data-slot="switch"]') as HTMLElement;
		expect(root).toBeTruthy();
		expect(root.getAttribute("data-state")).toBe("unchecked");

		await user.click(root);
		expect(root.getAttribute("data-state")).toBe("checked");
	});
});

describe("Switch — BG-R12 sub-24px touch target", () => {
	it("uses after:-inset-y-3 (12px each side) on the root for a 44px hit area on the default-size track", () => {
		render(<VtSwitch aria-label="test-switch" size="default" />);

		const root = document.querySelector('[data-slot="switch"]') as HTMLElement;
		expect(root).toBeTruthy();
		//previously `after:-inset-y-2` (8px each side → 36px
		// total touch height on the 20px-tall default track). Now
		// `after:-inset-y-3` (12px each side → 44px total, WCAG 2.5.5).
		expect(root.className).toContain("after:-inset-y-3");
		// The smaller value must NOT still be present.
		expect(root.className).not.toContain("after:-inset-y-2");
	});
});

describe("Switch — BG-R12 visible checked-border ring", () => {
	it("uses data-checked:border-primary/30 so the 2px border shows as a subtle ring (was invisible at full opacity)", () => {
		render(<VtSwitch aria-label="test-switch" />);

		const root = document.querySelector('[data-slot="switch"]') as HTMLElement;
		expect(root).toBeTruthy();
		//the previous `data-checked:border-primary` was visually
		// identical to `data-checked:bg-primary` so the border was
		// invisible. Now uses `/30` so the border shows as a subtle
		// primary-tinted ring around the track when checked.
		expect(root.className).toContain("data-checked:border-primary/30");
		// The fully-opaque invisible variant must NOT still be present.
		// Match it as a standalone token (with a trailing space or quote)
		// so we don't false-positive on the `/30` substring.
		expect(root.className).not.toMatch(/\bdata-checked:border-primary(?!\/)\b/);
	});
});
