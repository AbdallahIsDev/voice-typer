/**
 * Slider per-thumb valuetext contract tests.
 *
 * The shared `Slider` primitive applies `getThumbAriaValueText` PER
 * THUMB (aria-valuetext lives on each focusable thumb element, which
 * is the ARIA surface screen readers read), and passes both the
 * thumb's numeric value and its index to the callback so multi-thumb
 * sliders can label each thumb differently ("250 ms minimum",
 * "4800 Hz maximum").
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Slider } from "@/components/ui/slider";

describe("Slider — per-thumb aria-valuetext", () => {
	afterEach(() => {
		cleanup();
	});

	it("passes (value, index) to getThumbAriaValueText for every thumb", () => {
		const getText = vi.fn(
			(value: number, index: number) => `thumb ${index}: ${value} ms`,
		);
		render(
			<Slider
				defaultValue={[250, 4800]}
				thumbLabels={["Minimum", "Maximum"]}
				getThumbAriaValueText={getText}
			/>,
		);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(2);
		expect(getText).toHaveBeenCalledWith(250, 0);
		expect(getText).toHaveBeenCalledWith(4800, 1);
		expect(thumbs[0]).toHaveAttribute("aria-valuetext", "thumb 0: 250 ms");
		expect(thumbs[1]).toHaveAttribute("aria-valuetext", "thumb 1: 4800 ms");
	});

	it("single-thumb sliders receive index 0", () => {
		const getText = vi.fn(() => "50%");
		render(
			<Slider
				defaultValue={[50]}
				aria-label="Volume"
				getThumbAriaValueText={getText}
			/>,
		);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs).toHaveLength(1);
		expect(getText).toHaveBeenCalledWith(50, 0);
		expect(thumbs[0]).toHaveAttribute("aria-valuetext", "50%");
	});

	it("does not set aria-valuetext when the callback is absent", () => {
		render(<Slider defaultValue={[50]} aria-label="Volume" />);
		const thumbs = document.querySelectorAll('[data-slot="slider-thumb"]');
		expect(thumbs[0]).not.toHaveAttribute("aria-valuetext");
	});
});
