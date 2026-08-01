/**
 *  (RangeSlider deferApply commit test).
 *
 * RangeSlider supports a `deferApply` prop that defers the real
 * `onChange` callback to a "commit" event. The implementation attaches
 * exactly two commit handlers (see JSDoc):
 *
 *   - `onPointerUp` — commits after a mouse/touch/pen drag.
 *   - `onBlur`      — commits after the user Tabs away from the slider
 *                     (covers keyboard-only arrow-key steps which never
 *                     produce a pointerup).
 *
 * Rather than drive Radix Slider's internal pointer-capture state
 * machine (which jsdom does not fully support — `hasPointerCapture` is
 * not implemented), we mock `@/components/ui/slider` with a thin pass-
 * through that exposes the props RangeSlider passes to it. The mock
 * renders a single `<input type="range">` and forwards onValueChange
 * (mapped from the native `onChange`) so tests can drive it without
 * pulling in Radix's pointer-capture code path.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture the props RangeSlider passes to Slider. Individual tests can
// inspect these to verify the wiring (e.g. that deferApply=true still
// passes the deferred display value, not the committed value).
let sliderProps: Record<string, unknown> | null = null;

vi.mock("@/components/ui/slider", () => ({
	Slider: (props: Record<string, unknown>) => {
		sliderProps = props;
		const value = (props.value as number[] | undefined)?.[0] ?? 0;
		return (
			<input
				type="range"
				data-slot="slider"
				data-testid="mock-slider"
				value={value}
				min={props.min as number}
				max={props.max as number}
				step={props.step as number}
				aria-label={props["aria-label"] as string}
				aria-valuetext={props["aria-valuetext"] as string}
				onChange={(e) => {
					const next = Number(e.target.value);
					(props.onValueChange as (v: number[]) => void)?.([next]);
				}}
				onPointerUp={props.onPointerUp as React.PointerEventHandler}
				onBlur={props.onBlur as React.FocusEventHandler}
			/>
		);
	},
}));

import { RangeSlider } from "@/components/common/RangeSlider";

function getSlider(): HTMLInputElement {
	return screen.getByTestId("mock-slider") as HTMLInputElement;
}

describe("RangeSlider — BG-R11 (deferApply commit contract)", () => {
	afterEach(() => {
		sliderProps = null;
		cleanup();
	});

	beforeEach(() => {
		sliderProps = null;
	});

	it("deferApply=false: onChange fires immediately on every value change", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				onChange={onChange}
			/>,
		);
		const slider = getSlider();
		fireEvent.change(slider, { target: { value: "55" } });
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith(55);
	});

	it("deferApply=true: value change updates display but does NOT fire onChange", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		// Two successive changes (simulating a drag).
		fireEvent.change(getSlider(), { target: { value: "51" } });
		fireEvent.change(getSlider(), { target: { value: "52" } });
		expect(onChange).not.toHaveBeenCalled();
		// The display value next to the thumb mirrors the local state.
		expect(screen.getByText("52%")).toBeInTheDocument();
	});

	it("deferApply=true: pointerup commits the pending value via onChange exactly once", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		fireEvent.change(getSlider(), { target: { value: "52" } });
		expect(onChange).not.toHaveBeenCalled();
		// pointerup on the slider root commits.
		fireEvent.pointerUp(getSlider());
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith(52);
	});

	it("deferApply=true: blur commits the pending value via onChange exactly once", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		fireEvent.change(getSlider(), { target: { value: "52" } });
		expect(onChange).not.toHaveBeenCalled();
		fireEvent.blur(getSlider());
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith(52);
	});

	it("deferApply=true: external value prop change mirrors to display without firing onChange", () => {
		const onChange = vi.fn();
		const { rerender } = render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		expect(screen.getByText("50%")).toBeInTheDocument();
		rerender(
			<RangeSlider
				value={75}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		expect(screen.getByText("75%")).toBeInTheDocument();
		expect(onChange).not.toHaveBeenCalled();
	});

	it("deferApply=true: no commit when nothing was changed (dirtyRef guard)", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		fireEvent.pointerUp(getSlider());
		expect(onChange).not.toHaveBeenCalled();
		fireEvent.blur(getSlider());
		expect(onChange).not.toHaveBeenCalled();
	});

	it("passes aria-valuetext (value + unit) to the underlying slider for SR users", () => {
		render(
			<RangeSlider
				value={42}
				min={0}
				max={100}
				step={1}
				suffix="ms"
				ariaLabel="Delay"
				onChange={vi.fn()}
			/>,
		);
		const slider = getSlider();
		expect(slider).toHaveAttribute("aria-valuetext", "42ms");
	});

	it("deferApply=true: passes the local display value (not the committed value) to the underlying Slider", () => {
		const onChange = vi.fn();
		render(
			<RangeSlider
				value={50}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Volume"
				deferApply={true}
				onChange={onChange}
			/>,
		);
		// Initially the slider's value prop matches the committed value.
		expect((sliderProps?.value as number[])?.[0]).toBe(50);
		// After a drag, the slider's value prop follows the local
		// display state — not the committed value (which is still 50).
		fireEvent.change(getSlider(), { target: { value: "70" } });
		expect((sliderProps?.value as number[])?.[0]).toBe(70);
		// onChange still hasn't fired (deferred to pointerup / blur).
		expect(onChange).not.toHaveBeenCalled();
	});

	it("forwards aria-label to the underlying slider", () => {
		render(
			<RangeSlider
				value={0}
				min={0}
				max={100}
				step={1}
				suffix="%"
				ariaLabel="Brightness"
				onChange={vi.fn()}
			/>,
		);
		expect(getSlider()).toHaveAttribute("aria-label", "Brightness");
	});
});
