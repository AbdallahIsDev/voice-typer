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
 * the new ref with the element on EVERY parent re-render — causing
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
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "../segmented-control";

// Spy-backed ResizeObserver mock. We track `.observe()` and
// `.disconnect()` calls so we can assert the ref callback is stable.
const observeSpy = vi.fn();
const disconnectSpy = vi.fn();

class SpyResizeObserver {
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
});

const OPTIONS = [
	{ value: "a", label: "A" },
	{ value: "b", label: "B" },
];

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
		// ref(null) + ref(el) on this re-render — which would in turn
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

		// observe() must NOT have been called again — ref is stable.
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
		// Same behaviour must hold for variant="tabs" — the container
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
