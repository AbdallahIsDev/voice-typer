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
import userEvent from "@testing-library/user-event";
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

describe("NumberInputStepper — at-boundary aria-disabled (no native disabled)", () => {
	// When the value is at min or max, the stepper buttons must:
	//   - expose ``aria-disabled="true"`` so SRs announce the disabled
	//     state (the native ``disabled`` attribute causes SRs to skip
	//     disabled buttons entirely, hiding it from the user).
	//   - drop out of the tab order (``tabIndex={-1}``) so keyboard
	//     users aren't trapped on a no-op control.
	//   - NOT carry the native ``disabled`` attribute.
	//   - refuse to activate when clicked (onClick guard).
	it("marks the up-stepper aria-disabled + tabIndex=-1 when value is at max", () => {
		render(
			<NumberInputStepper
				value="10"
				min={0}
				max={10}
				step={1}
				onChange={() => {}}
				aria-label="at-max"
			/>,
		);
		const upBtn = document.querySelector(
			'button[aria-label="a11y.increase"]',
		) as HTMLButtonElement;
		expect(upBtn).toBeTruthy();
		expect(upBtn.hasAttribute("disabled")).toBe(false);
		expect(upBtn.getAttribute("aria-disabled")).toBe("true");
		expect(upBtn.tabIndex).toBe(-1);
	});

	it("marks the down-stepper aria-disabled + tabIndex=-1 when value is at min", () => {
		render(
			<NumberInputStepper
				value="0"
				min={0}
				max={10}
				step={1}
				onChange={() => {}}
				aria-label="at-min"
			/>,
		);
		const downBtn = document.querySelector(
			'button[aria-label="a11y.decrease"]',
		) as HTMLButtonElement;
		expect(downBtn).toBeTruthy();
		expect(downBtn.hasAttribute("disabled")).toBe(false);
		expect(downBtn.getAttribute("aria-disabled")).toBe("true");
		expect(downBtn.tabIndex).toBe(-1);
	});

	it("keeps both steppers in tab order (tabIndex=0) and without aria-disabled when value is mid-range", () => {
		render(
			<NumberInputStepper
				value="5"
				min={0}
				max={10}
				step={1}
				onChange={() => {}}
				aria-label="mid-range"
			/>,
		);
		const upBtn = document.querySelector(
			'button[aria-label="a11y.increase"]',
		) as HTMLButtonElement;
		const downBtn = document.querySelector(
			'button[aria-label="a11y.decrease"]',
		) as HTMLButtonElement;
		expect(upBtn.tabIndex).toBe(0);
		expect(upBtn.hasAttribute("aria-disabled")).toBe(false);
		expect(downBtn.tabIndex).toBe(0);
		expect(downBtn.hasAttribute("aria-disabled")).toBe(false);
	});

	it("does not call onChange when the up-stepper is clicked at max (onClick guard)", async () => {
		const onChange = vi.fn();
		const user = userEvent.setup();
		render(
			<NumberInputStepper
				value="10"
				min={0}
				max={10}
				step={1}
				onChange={onChange}
				aria-label="at-max"
			/>,
		);
		const upBtn = document.querySelector(
			'button[aria-label="a11y.increase"]',
		) as HTMLButtonElement;
		await user.click(upBtn);
		expect(onChange).not.toHaveBeenCalled();
	});

	it("does not call onChange when the down-stepper is clicked at min (onClick guard)", async () => {
		const onChange = vi.fn();
		const user = userEvent.setup();
		render(
			<NumberInputStepper
				value="0"
				min={0}
				max={10}
				step={1}
				onChange={onChange}
				aria-label="at-min"
			/>,
		);
		const downBtn = document.querySelector(
			'button[aria-label="a11y.decrease"]',
		) as HTMLButtonElement;
		await user.click(downBtn);
		expect(onChange).not.toHaveBeenCalled();
	});
});
