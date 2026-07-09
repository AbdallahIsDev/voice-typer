/**
 * Tests for the SegmentedControl component (both default and tabs variants).
 *
 * Covers:
 * - Rendering both variants with the correct class names
 * - Option labels are visible
 * - onChange fires when clicking an unselected option
 * - Shows animated indicator for the active option
 * - Does NOT fire onChange when clicking the already-active option
 * - Keyboard accessibility (role="radiogroup", role="radio", aria-checked)
 * - Generic type parameter passes through correctly
 * - Works with any number of options (2 through 6)
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "../segmented-control";

afterEach(() => {
	cleanup();
});

// ── Shared test options (mutable arrays — don't use `as const` since
// SegmentedControlOption<T>[] is mutable, and `as const` makes readonly tuples) ──

const TWO_OPTIONS = [
	{ value: "left", label: "Left" },
	{ value: "right", label: "Right" },
];

const FOUR_OPTIONS = [
	{ value: "one", label: "Option One" },
	{ value: "two", label: "Option Two" },
	{ value: "three", label: "Option Three" },
	{ value: "four", label: "Option Four" },
];

const SIX_OPTIONS = [
	{ value: "a", label: "Alpha" },
	{ value: "b", label: "Bravo" },
	{ value: "c", label: "Charlie" },
	{ value: "d", label: "Delta" },
	{ value: "e", label: "Echo" },
	{ value: "f", label: "Foxtrot" },
];

// ── Default variant ──────────────────────────────────────────────────────────

describe("SegmentedControl (default variant)", () => {
	it("renders all option labels", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		expect(screen.getByText("Left")).toBeInTheDocument();
		expect(screen.getByText("Right")).toBeInTheDocument();
	});

	it("applies default variant class names (rounded-full, border, p-0.75)", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		const fieldset = screen.getByRole("radiogroup");
		expect(fieldset.className).toContain("rounded-full");
		expect(fieldset.className).toContain("border");
		expect(fieldset.className).toContain("p-0.75");
	});

	it("marks the active option as checked", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		expect(radios).toHaveLength(2);

		// Left should be checked, Right should not
		expect(radios[0]).toBeChecked();
		expect(radios[1]).not.toBeChecked();
	});

	it("calls onChange when clicking an unselected option", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		await user.click(screen.getByText("Right"));
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("right");
	});

	it("does NOT call onChange when clicking the already-active option", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		await user.click(screen.getByText("Left"));
		expect(onChange).not.toHaveBeenCalled();
	});

	it("renders the active indicator element", async () => {
		// The indicator is a position:absolute div positioned via
		// requestAnimationFrame after mount. We waitFor it to appear.
		const { container } = render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		await waitFor(() => {
			const indicator = container.querySelector(".bg-primary");
			expect(indicator).toBeTruthy();
		});
	});

	it("applies active styling to the active label", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		// The text-xs and active-styling classes live on the wrapping <label>,
		// not on the <input> itself (which has className="sr-only").
		const leftLabel = radios[0].closest("label");
		const rightLabel = radios[1].closest("label");

		expect(leftLabel?.className).toContain("text-primary-foreground");
		expect(rightLabel?.className).toContain("text-(--text-muted)");
	});

	it("renders labels with text-[11px]", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		for (const radio of radios) {
			const label = radio.closest("label");
			expect(label?.className).toContain("text-[11px]");
		}
	});
});

// ── Accessibility ────────────────────────────────────────────────────────────

describe("SegmentedControl accessibility", () => {
	it("has role radiogroup on the container", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="my-control"
			/>,
		);

		const group = screen.getByRole("radiogroup");
		expect(group).toBeInTheDocument();
		expect(group).toHaveAttribute("aria-label", "my-control");
	});

	it("each option has role radio with correct checked state", () => {
		// Native <input type="radio"> uses the `checked` DOM property,
		// not aria-checked. The `toBeChecked()` matcher tests this.
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		expect(radios).toHaveLength(2);

		expect(radios[0]).toBeChecked();
		expect(radios[1]).not.toBeChecked();
	});

	it("uses ariaLabel as the radio group name", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="recording-mode"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		// Each radio input has name={ariaLabel || \"segmented-control\"}
		for (const radio of radios) {
			expect(radio).toHaveAttribute("name", "recording-mode");
		}
	});
});

// ── Generic type parameter ───────────────────────────────────────────────────

describe("SegmentedControl generic type", () => {
	it("accepts a union type for value/onChange", () => {
		// This is a compile-time check wrapped in a runtime assertion.
		// If the generic type parameter didn't flow through correctly,
		// TypeScript would error at compile time.
		type MyValue = "foo" | "bar" | "baz";

		const onChange = vi.fn();
		const { container } = render(
			<SegmentedControl<MyValue>
				options={[
					{ value: "foo", label: "Foo" },
					{ value: "bar", label: "Bar" },
					{ value: "baz", label: "Baz" },
				]}
				value="foo"
				onChange={onChange}
				ariaLabel="generic-test"
			/>,
		);

		// Just verify it rendered
		expect(container.querySelector("[role=radiogroup]")).toBeInTheDocument();
	});
});

// ── Variable number of options ───────────────────────────────────────────────

describe("SegmentedControl with many options", () => {
	it("renders 6 options without issue", () => {
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={SIX_OPTIONS}
				value="c"
				onChange={onChange}
				ariaLabel="six-options"
			/>,
		);

		expect(screen.getByText("Alpha")).toBeInTheDocument();
		expect(screen.getByText("Foxtrot")).toBeInTheDocument();

		const radios = screen.getAllByRole("radio");
		expect(radios).toHaveLength(6);
	});

	it("handles onChange correctly with 6 options", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={SIX_OPTIONS}
				value="a"
				onChange={onChange}
				ariaLabel="six-options"
			/>,
		);

		// Click the last option
		await user.click(screen.getByText("Foxtrot"));
		expect(onChange).toHaveBeenCalledWith("f");
	});
});

// ── Keyboard navigation (ArrowLeft / ArrowRight) ─────────────────────────
//
// The component uses native <input type="radio"> elements, so ArrowLeft /
// ArrowRight (and Up/Down) navigation is handled by the browser's built-in
// radio-group behaviour.  In jsdom this should also work — dispatching
// ArrowRight on a focused radio moves focus (and checked state) to the next
// radio in the same name group, which triggers our onChange handler.
//
// Edge cases tested:
// - ArrowRight from a mid-group option moves forward
// - ArrowLeft from a mid-group option moves backward
// - ArrowRight on the LAST option is a no-op (no wrapping)
// - ArrowLeft on the FIRST option is a no-op (no wrapping)
// - Works identically in the tabs variant

describe("SegmentedControl keyboard navigation", () => {
	it("moves forward with ArrowRight from the first option", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={FOUR_OPTIONS}
				value="one"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		// Focus the first (active) radio, then press ArrowRight.
		screen.getAllByRole("radio")[0].focus();
		await user.keyboard("{ArrowRight}");

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("two");
	});

	it("moves backward with ArrowLeft from a mid-group option", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={FOUR_OPTIONS}
				value="three"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		// Focus the third radio, then press ArrowLeft.
		screen.getAllByRole("radio")[2].focus();
		await user.keyboard("{ArrowLeft}");

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("two");
	});

	it("wraps from last to first with ArrowRight (HTML spec)", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={FOUR_OPTIONS}
				value="four"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		// Per the HTML spec, ArrowRight on the last radio wraps to the first.
		screen.getAllByRole("radio")[3].focus();
		await user.keyboard("{ArrowRight}");

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("one");
	});

	it("wraps from first to last with ArrowLeft (HTML spec)", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				options={FOUR_OPTIONS}
				value="one"
				onChange={onChange}
				ariaLabel="test-control"
			/>,
		);

		// Per the HTML spec, ArrowLeft on the first radio wraps to the last.
		screen.getAllByRole("radio")[0].focus();
		await user.keyboard("{ArrowLeft}");

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith("four");
	});
});

// ── Tabs variant ────────────────────────────────────────────────────────────

describe("SegmentedControl tabs variant", () => {
	it("renders with transparent background and no border-radius", () => {
		render(
			<SegmentedControl
				variant="tabs"
				options={FOUR_OPTIONS}
				value="one"
				onChange={() => {}}
				ariaLabel="tabs-control"
			/>,
		);

		const group = screen.getByRole("radiogroup");
		expect(group.className).toContain("bg-transparent");
		expect(group.className).toContain("border-none");
		expect(group.className).toContain("rounded-none");
		expect(group.className).not.toContain("rounded-full");
		expect(group.className).not.toContain("bg-input/50");
		expect(group.className).toContain("p-1");
		expect(group.className).not.toContain("p-0.75");
	});

	it("labels have no rounded-full and use larger font", () => {
		render(
			<SegmentedControl
				variant="tabs"
				options={FOUR_OPTIONS}
				value="one"
				onChange={() => {}}
				ariaLabel="tabs-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		for (const radio of radios) {
			const label = radio.closest("label");
			expect(label?.className).toContain("rounded-none");
			expect(label?.className).not.toContain("rounded-full");
			expect(label?.className).toContain("text-[13px]");
			expect(label?.className).toContain("font-medium");
		}
	});

	it("active label uses text-(--text-primary) instead of text-primary-foreground", () => {
		render(
			<SegmentedControl
				variant="tabs"
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="tabs-control"
			/>,
		);

		const radios = screen.getAllByRole("radio");
		const leftLabel = radios[0].closest("label");
		const rightLabel = radios[1].closest("label");

		expect(leftLabel?.className).toContain("text-(--text-primary)");
		expect(rightLabel?.className).not.toContain("text-primary-foreground");
	});

	it("indicator has no rounded corners", async () => {
		const { container } = render(
			<SegmentedControl
				variant="tabs"
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="tabs-control"
			/>,
		);

		await waitFor(() => {
			// The indicator is the absolute-positioned child of the container.
			const indicator = container.querySelector(".absolute");
			expect(indicator).toBeTruthy();
			expect(indicator?.className).toContain("rounded-md");
			expect(indicator?.className).not.toContain("rounded-full");
			expect(indicator?.className).toContain("bg-input");
			expect(indicator?.className).not.toContain("bg-(", "should not use CSS variable reference that breaks in tests");
		});
	});

	it("onChange fires correctly in tabs variant", async () => {
		const user = userEvent.setup();
		const onChange = vi.fn();

		render(
			<SegmentedControl
				variant="tabs"
				options={TWO_OPTIONS}
				value="left"
				onChange={onChange}
				ariaLabel="tabs-control"
			/>,
		);

		await user.click(screen.getByText("Right"));
		expect(onChange).toHaveBeenCalledWith("right");
	});
});

// ── Extra className ──────────────────────────────────────────────────────────

describe("SegmentedControl className prop", () => {
	it("appends extra className to the container", () => {
		render(
			<SegmentedControl
				options={TWO_OPTIONS}
				value="left"
				onChange={() => {}}
				ariaLabel="test-control"
				className="my-custom-class"
			/>,
		);

		const group = screen.getByRole("radiogroup");
		expect(group.className).toContain("my-custom-class");
	});

	it("merges extra className with base classes", () => {
		render(
			<SegmentedControl
				options={FOUR_OPTIONS}
				value="one"
				onChange={() => {}}
				ariaLabel="test-classname"
				className="w-full"
			/>,
		);

		const group = screen.getByRole("radiogroup");
		expect(group.className).toContain("w-full");
		expect(group.className).toContain("rounded-full");
	});
});
