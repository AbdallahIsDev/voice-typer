/**
 * NumberInputStepper tests — covers  (RTL: physical `right-1` /
 * `pr-8` were replaced with logical `inset-e-1` / `pe-8` so the steppers
 * sit at the inline-end edge in both LTR and RTL locales).
 *
 * The tests assert on `className` strings because jsdom has no CSS
 * engine; verifying the logical Tailwind utilities are present (and
 * the physical ones are NOT) is sufficient to confirm the intent.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NumberInputStepper } from "../number-input-stepper";

// Stub the i18n `t()` function so the stepper's aria-labels render as
// stable strings without depending on the full i18n catalog. The
// component calls `t("a11y.increase")` and `t("a11y.decrease")` for
// the up/down stepper buttons.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

afterEach(() => {
	cleanup();
});

describe("NumberInputStepper — BG-39 RTL logical positioning", () => {
	it("uses logical `inset-e-1` (not physical `right-1`) for the stepper container", () => {
		render(
			<NumberInputStepper
				value="1"
				onChange={() => {}}
				aria-label="test-stepper"
			/>,
		);

		// The stepper container is the only absolutely-positioned child
		// of the wrapper `<div class="group relative ...">`.
		const wrapper = document.querySelector(".group") as HTMLElement;
		expect(wrapper).toBeTruthy();
		const stepperContainer = wrapper.querySelector(".absolute") as HTMLElement;
		expect(stepperContainer).toBeTruthy();
		//physical `right-1` is gone; logical `inset-e-1` is present
		// so the steppers appear on the inline-end edge (right in LTR,
		// left in RTL) without per-locale overrides.
		expect(stepperContainer.className).toContain("inset-e-1");
		expect(stepperContainer.className).not.toMatch(/\bright-1\b/);
	});

	it("uses logical `pe-8` (not physical `pr-8`) for the input's end-side padding", () => {
		render(
			<NumberInputStepper
				value="1"
				onChange={() => {}}
				aria-label="test-stepper"
			/>,
		);

		const input = document.querySelector(
			'input[type="number"]',
		) as HTMLInputElement;
		expect(input).toBeTruthy();
		//the input reserves gutter space for the steppers via
		// logical `pe-8` (padding-inline-end) instead of physical
		// `pr-8` (padding-right), so the gutter sits on the same side
		// as the steppers in both LTR and RTL.
		expect(input.className).toContain("pe-8");
		expect(input.className).not.toMatch(/\bpr-8\b/);
	});
});
