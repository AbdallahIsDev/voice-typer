/**
 * Tests that SegmentedControl's container ref callback is stable across
 * value-stable re-renders, so React does not thrash ResizeObserver
 * `.observe()` / `.disconnect()` on every parent re-render.
 *
 * Background: the previous implementation passed an inline arrow
 * function as the `ref` prop on the container <div>:
 *
 *   ref={(el) => {
 *     if (containerRef.current !== el) {
 *       resizeObserver.disconnect();
 *       containerRef.current = el;
 *       if (el) {
 *         resizeObserver.observe(el);
 *         requestAnimationFrame(() => updateIndicator());
 *       }
 *     }
 *   }}
 *
 * An inline arrow creates a NEW function identity on every render. React
 * detects the identity change and re-invokes the old ref with `null` and
 * the new ref with the element on EVERY parent re-render, causing
 * `resizeObserver.disconnect()` + `resizeObserver.observe(el)` +
 * `requestAnimationFrame(updateIndicator)` to fire repeatedly even when
 * the underlying DOM node hasn't changed. The fix hoists the callback
 * into a `useCallback` so it has a stable identity across value-stable
 * re-renders.
 *
 * These tests assert the stable-identity behaviour by spying on the
 * global `ResizeObserver` constructor's `.observe()` / `.disconnect()`
 * methods. The default jsdom polyfill installed in `test-setup.ts` is a
 * no-op stub, so this file replaces it with a spy-backed mock via
 * `vi.stubGlobal`.
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "../segmented-control";

// Spy-backed ResizeObserver mock. We track `.observe()` and `.disconnect()`
// calls so we can assert the ref callback is stable. The constructor also
// records each instance's callback so tests can fire it manually (simulating
// a container resize) and inspect what the observer re-measures.
const observeSpy = vi.fn();
const disconnectSpy = vi.fn();
const observerInstances: Array<{ callback: ResizeObserverCallback }> = [];

class SpyResizeObserver {
	constructor(callback: ResizeObserverCallback) {
		observerInstances.push({ callback });
	}
	observe = observeSpy;
	unobserve = vi.fn();
	disconnect = disconnectSpy;
}

// Replace the no-op stub from test-setup.ts with our spy-backed mock.
vi.stubGlobal("ResizeObserver", SpyResizeObserver);

afterEach(() => {
	cleanup();
	observeSpy.mockClear();
	disconnectSpy.mockClear();
	observerInstances.length = 0;
});

const OPTIONS = [
	{ value: "a", label: "A" },
	{ value: "b", label: "B" },
];

/**
 * jsdom's getBoundingClientRect returns all-zero rects, which collapses
 * every measured indicator position to left:0/width:0, position
 * assertions would be meaningless. This helper pins DISTINCT rects onto
 * the container and each option label (DOM order == option order for the
 * default variant's `<label>` elements) so the indicator's `left`
 * reveals WHICH option was measured.
 */
function mockMeasureRects(
	root: HTMLElement,
	lefts: Record<string, number>,
	width = 40,
): void {
	const rect = (left: number): DOMRect =>
		({
			left,
			right: left + width,
			width,
			top: 0,
			bottom: 0,
			height: 20,
			x: left,
			y: 0,
			toJSON: () => ({}),
		}) as DOMRect;
	const container = root.querySelector('[role="radiogroup"]');
	if (container) {
		vi.spyOn(container, "getBoundingClientRect").mockReturnValue(
			rect(0) as DOMRect,
		);
	}
	const labels = Array.from(root.querySelectorAll("label"));
	for (const [i, label] of labels.entries()) {
		const left = lefts[String(i)] ?? 0;
		vi.spyOn(label, "getBoundingClientRect").mockReturnValue(
			rect(left) as DOMRect,
		);
	}
}

describe("SegmentedControl container ref callback stability", () => {
	it("calls ResizeObserver.observe exactly once on mount", () => {
		render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="stable-ref-mount"
			/>,
		);

		// On mount the container ref is invoked once with the element,
		// which calls `resizeObserver.observe(el)`.
		expect(observeSpy).toHaveBeenCalledTimes(1);
		expect(disconnectSpy).not.toHaveBeenCalled();
	});

	it("does NOT re-invoke ResizeObserver.observe on value-stable re-render", () => {
		// An inline arrow `ref={(el) => {...}}` would have created
		// a new function identity on every render, causing React to call
		// ref(null) + ref(el) on this re-render, which would in turn
		// call `disconnect()` + `observe()`. The hoisted `useCallback`
		// keeps the identity stable across value-stable re-renders, so
		// neither `disconnect()` nor `observe()` should fire.
		const { rerender } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="stable-ref-rerender"
			/>,
		);

		expect(observeSpy).toHaveBeenCalledTimes(1);

		// Re-render with the same value and same props. Parent re-rendered
		// for some unrelated reason (e.g. its own state changed).
		rerender(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="stable-ref-rerender"
			/>,
		);

		// observe() must NOT have been called again, ref is stable.
		expect(observeSpy).toHaveBeenCalledTimes(1);
		expect(disconnectSpy).not.toHaveBeenCalled();

		// A second value-stable re-render should also not trigger observe().
		rerender(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="stable-ref-rerender"
			/>,
		);

		expect(observeSpy).toHaveBeenCalledTimes(1);
		expect(disconnectSpy).not.toHaveBeenCalled();
	});

	it("disconnects the ResizeObserver exactly once on unmount", () => {
		// The unmount cleanup effect (`useEffect(() => () =>
		// resizeObserver.disconnect(), [resizeObserver])`) is the sole
		// owner of `disconnect()` calls. The ref callback itself no
		// longer calls `disconnect()` (the old inline arrow did, which
		// is what caused the thrash). On unmount, React runs the effect
		// cleanup → `disconnect()` is called exactly once.
		const { unmount } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="unmount-disconnect"
			/>,
		);

		expect(disconnectSpy).not.toHaveBeenCalled();

		unmount();

		expect(disconnectSpy).toHaveBeenCalledTimes(1);
	});

	it("also keeps the ref stable in the tabs variant", () => {
		// Same behaviour must hold for variant="tabs", the container
		// <div> is the same element, just with role="tablist".
		const { rerender } = render(
			<SegmentedControl
				variant="tabs"
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="tabs-stable-ref"
			/>,
		);

		expect(observeSpy).toHaveBeenCalledTimes(1);

		rerender(
			<SegmentedControl
				variant="tabs"
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="tabs-stable-ref"
			/>,
		);

		expect(observeSpy).toHaveBeenCalledTimes(1);
		expect(disconnectSpy).not.toHaveBeenCalled();
	});
});

describe("SegmentedControl per-option label ref stability + value-change behaviour", () => {
	it("keeps per-option label ref callbacks stable across re-renders (no ref attach/detach churn)", () => {
		// A fresh inline closure per option per render (what an
		// un-memoized `getLabelRef(opt.value)` produces) makes React call
		// the old ref with `null` and the new one with the element on
		// EVERY re-render, 2N attach/detach round-trips plus label-Map
		// churn for an unchanged option list. With stable cached
		// callbacks, React does not re-invoke the refs at all, so the
		// label Map performs ZERO writes during a re-render.
		const { rerender } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="label-ref-stability"
			/>,
		);

		const setSpy = vi.spyOn(Map.prototype, "set");
		try {
			rerender(
				<SegmentedControl
					options={OPTIONS}
					value="a"
					onChange={() => {}}
					ariaLabel="label-ref-stability"
				/>,
			);
			// Only label-ref Map writes matter here, filter by our option
			// values so React-internal Map traffic (keyed by objects) can't
			// skew the count.
			const labelRefWrites = setSpy.mock.calls.filter(
				(call) => call[0] === "a" || call[0] === "b",
			);
			expect(labelRefWrites).toHaveLength(0);
		} finally {
			setSpy.mockRestore();
		}
	});

	it("does NOT re-invoke ResizeObserver.observe when the value changes (container ref stable across value changes)", () => {
		// `updateIndicator` used to depend on `value`, so `setContainerRef`
		// got a new identity on every value change, React detached the old
		// ref and re-attached the new one, re-firing `observe()` on an
		// element that never changed. Decoupling the measurement from the
		// `value` closure (reading it from a ref inside the observer) keeps
		// the container ref identity stable across value changes.
		const { rerender } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="value-change-stability"
			/>,
		);
		expect(observeSpy).toHaveBeenCalledTimes(1);

		rerender(
			<SegmentedControl
				options={OPTIONS}
				value="b"
				onChange={() => {}}
				ariaLabel="value-change-stability"
			/>,
		);

		expect(observeSpy).toHaveBeenCalledTimes(1);
		expect(disconnectSpy).not.toHaveBeenCalled();
	});

	it("repositions the indicator when the value prop changes externally (parent-driven or keyboard navigation)", async () => {
		// Regression guard for the ref-stability fix: the value-change
		// re-measure must not be lost when the container ref stops churning.
		// Distinct rects: option "a" at left 0, option "b" at left 48.
		const { rerender, container } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="external-value-change"
			/>,
		);
		mockMeasureRects(container, { 0: 0, 1: 48 });
		// The indicator element only mounts once the first measurement
		// lands (async rAF), wait for it before asserting positions.
		await waitFor(() =>
			expect(container.querySelector(".bg-primary")).toBeTruthy(),
		);
		const indicator = container.querySelector<HTMLElement>(".bg-primary");
		expect(indicator).toBeTruthy();
		await waitFor(() => expect(indicator?.style.left).toBe("0px"));

		rerender(
			<SegmentedControl
				options={OPTIONS}
				value="b"
				onChange={() => {}}
				ariaLabel="external-value-change"
			/>,
		);

		await waitFor(() => expect(indicator?.style.left).toBe("48px"));
		expect(indicator?.style.width).toBe("40px");
	});

	it("the ResizeObserver callback measures the CURRENT value's label, not a stale first-render closure", async () => {
		// The observer is constructed ONCE (useState initializer). If its
		// closure captures the `value` from the render that created it, a
		// container resize AFTER a value change re-positions the indicator
		// on the STALE (initial) option. Reading the latest value from a
		// ref keeps the observer correct for the component's whole life.
		const { rerender, container } = render(
			<SegmentedControl
				options={OPTIONS}
				value="a"
				onChange={() => {}}
				ariaLabel="observer-current-value"
			/>,
		);
		mockMeasureRects(container, { 0: 0, 1: 48 });
		await waitFor(() =>
			expect(container.querySelector(".bg-primary")).toBeTruthy(),
		);
		const indicator = container.querySelector<HTMLElement>(".bg-primary");
		expect(indicator).toBeTruthy();

		// Move to "b" (external value change), then fire the observer
		// callback as a real resize would.
		rerender(
			<SegmentedControl
				options={OPTIONS}
				value="b"
				onChange={() => {}}
				ariaLabel="observer-current-value"
			/>,
		);
		await waitFor(() => expect(indicator?.style.left).toBe("48px"));

		expect(observerInstances.length).toBeGreaterThan(0);
		const observer = observerInstances[0];
		if (!observer) throw new Error("SpyResizeObserver instance not recorded");
		observer.callback([], observer as unknown as ResizeObserver);

		// Let the rAF the observer scheduled actually land BEFORE asserting
		// (asserting via waitFor would race: waitFor's first poll runs
		// before the 16ms rAF, so a stale closure would pass the immediate
		// check and only corrupt the position afterwards). After the settle
		// window the indicator must STILL sit on the current value's label
		// ("b" at 48px), not have snapped back to the first-render value
		// ("a" at 0px).
		await new Promise((resolve) => setTimeout(resolve, 120));
		expect(indicator?.style.left).toBe("48px");
		expect(indicator?.style.left).not.toBe("0px");
	});

	it("keeps the label-ref cache consistent when an option is removed and re-added (dynamic option sets)", async () => {
		// The per-option callbacks live in a ref-held cache; pruning removed
		// options must never corrupt the refs for a re-added option, the
		// remounted element must be re-registered and remain measurable.
		const threeOptions = [...OPTIONS, { value: "c", label: "C" }];
		const props = {
			options: OPTIONS,
			value: "a",
			onChange: () => {},
			ariaLabel: "dynamic-options",
		};
		const { rerender, container } = render(<SegmentedControl {...props} />);
		await waitFor(() =>
			expect(container.querySelector(".bg-primary")).toBeTruthy(),
		);
		mockMeasureRects(container, { 0: 0, 1: 48 });

		// Add option "c" (left 96), select it, then remove it again.
		rerender(<SegmentedControl {...props} options={threeOptions} value="c" />);
		mockMeasureRects(container, { 0: 0, 1: 48, 2: 96 });
		const indicator = container.querySelector<HTMLElement>(".bg-primary");
		expect(indicator).toBeTruthy();
		await waitFor(() => expect(indicator?.style.left).toBe("96px"));

		rerender(<SegmentedControl {...props} options={OPTIONS} value="a" />);
		await waitFor(() => expect(indicator?.style.left).toBe("0px"));
	});
});
