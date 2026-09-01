/**
 * Visible range endpoint labels on RangeSlider.
 *
 * Sighted users see only the current value next to the thumb, never
 * the range endpoints. Both ends of the track now render the min/max
 * numbers (numeric only, no unit suffix — the unit is already shown
 * next to the current value) in the shared muted small-text tokens.
 * They are decorative (the slider root exposes aria-valuemin/aria-
 * valuemax) so they carry aria-hidden.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RangeSlider } from "@/components/common/RangeSlider";

describe("RangeSlider — visible min/max endpoint labels", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the min and max endpoint numbers at both ends of the track", () => {
		render(
			<RangeSlider
				value={50}
				min={20}
				max={200}
				step={1}
				suffix="ms"
				ariaLabel="Delay"
				onChange={vi.fn()}
			/>,
		);
		expect(screen.getByText("20")).toBeInTheDocument();
		expect(screen.getByText("200")).toBeInTheDocument();
	});

	it("endpoint labels are decorative (aria-hidden) — the slider root exposes the range", () => {
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				onChange={vi.fn()}
			/>,
		);
		const minLabel = screen.getByText("0");
		const maxLabel = screen.getByText("100");
		expect(minLabel).toHaveAttribute("aria-hidden", "true");
		expect(maxLabel).toHaveAttribute("aria-hidden", "true");
	});

	it("uses the muted small-text tokens on both endpoint labels", () => {
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				onChange={vi.fn()}
			/>,
		);
		const minLabel = screen.getByText("0");
		const maxLabel = screen.getByText("100");
		expect(minLabel.className).toContain("text-(--text-muted)");
		expect(maxLabel.className).toContain("text-(--text-muted)");
		expect(minLabel.className).toContain("text-xs");
	});

	it("endpoint labels are numeric only — no unit suffix", () => {
		render(
			<RangeSlider
				value={500}
				min={100}
				max={1000}
				step={1}
				suffix="Hz"
				ariaLabel="Frequency"
				onChange={vi.fn()}
			/>,
		);
		// The unit appears next to the current value, never on the
		// endpoint numbers.
		expect(screen.getByText("100")).not.toHaveTextContent("Hz");
		expect(screen.getByText("1000")).not.toHaveTextContent("Hz");
	});
});
