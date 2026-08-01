/**
 * Tests for the `flashCopied` timer cleanup in `ErrorBoundary.tsx`.
 *
 * Background: `flashCopied` schedules a 2-second `window.setTimeout` that
 * flips the "Copied!" button label back to "Copy error". Previously the
 * timer was not stored, so:
 *
 *   - Calling `flashCopied` twice in quick succession left the FIRST timer
 *     running. When it fired it would set `copied: false` even though the
 *     user had just copied again — the "Copied!" feedback vanished
 *     instantly instead of staying visible for 2s after the latest copy.
 *
 *   - If the boundary unmounted while the timer was pending (e.g. the
 *     error was recovered via "Try Again"), the timer would fire
 *     `setState` on an unmounted component — a React 19 warning + a latent
 *     leak if the boundary was re-mounted shortly after.
 *
 * The fix mirrors the `copyTimeoutRef` pattern in `ActivityList.tsx`
 * (): store the timer in an instance field (`copiedTimer`),
 * clear it before setting a new one in `flashCopied`, and clear it on
 * unmount via `componentWillUnmount`.
 *
 * These tests drive `flashCopied` directly via a ref to the class
 * instance (avoids the async clipboard path that `handleCopyError`
 * traverses — the clipboard mock is fragile under jsdom + fake timers,
 * and the unit under test here is the timer bookkeeping, not the
 * clipboard integration). `window.setTimeout` / `globalThis.clearTimeout`
 * are replaced with `vi.fn()` mocks so we can assert on the call args
 * without actually arming real timers.
 */
import { cleanup, render } from "@testing-library/react";
import { createRef, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

// `copiedTimer` is a `private` instance field on `ErrorBoundary`. TypeScript
// enforces the privacy at compile time, but the field is a regular property
// at runtime. This helper casts the instance to a shape that exposes the
// field so the tests can assert on its value (timer id bookkeeping is the
// unit under test — without reading the field we can only assert on the
// `clearTimeout` / `setTimeout` spy calls, not on the slot itself).
type ErrorBoundaryInternals = {
	copiedTimer: ReturnType<typeof setTimeout> | null;
};
function internals(b: ErrorBoundary): ErrorBoundaryInternals {
	return b as unknown as ErrorBoundaryInternals;
}

// A trivial child component — we render the boundary in the NON-error
// state. The boundary's `flashCopied` is callable regardless of whether
// it's currently showing the fallback UI (it just sets `copied: true`
// which only affects the fallback render, but the timer bookkeeping is
// what we're testing).
function Passthrough({ children }: { children: ReactNode }) {
	return <>{children}</>;
}

describe("ErrorBoundary: flashCopied timer cleanup", () => {
	// Mocks for `setTimeout` / `clearTimeout`. We replace the globals
	// (which in jsdom are the same as `window.setTimeout` /
	// `window.clearTimeout`) so the ErrorBoundary's `window.setTimeout`
	// and `clearTimeout` calls both route through our spies. The
	// `setTimeout` mock returns a monotonically-increasing fake id so
	// each call produces a distinct, comparable id.
	let originalSetTimeout: typeof globalThis.setTimeout;
	let originalClearTimeout: typeof globalThis.clearTimeout;
	let setTimeoutMock: ReturnType<typeof vi.fn>;
	let clearTimeoutMock: ReturnType<typeof vi.fn>;
	let nextId: number;

	beforeEach(() => {
		originalSetTimeout = globalThis.setTimeout;
		originalClearTimeout = globalThis.clearTimeout;
		nextId = 1;
		setTimeoutMock = vi.fn((() => {
			return nextId++;
		}) as unknown as typeof globalThis.setTimeout);
		clearTimeoutMock = vi.fn();
		globalThis.setTimeout =
			setTimeoutMock as unknown as typeof globalThis.setTimeout;
		globalThis.clearTimeout =
			clearTimeoutMock as unknown as typeof globalThis.clearTimeout;
	});

	afterEach(() => {
		globalThis.setTimeout = originalSetTimeout;
		globalThis.clearTimeout = originalClearTimeout;
		cleanup();
	});

	it("flashCopied stores the timer id in the copiedTimer instance field", () => {
		const ref = createRef<ErrorBoundary>();
		render(
			<ErrorBoundary ref={ref}>
				<Passthrough>child</Passthrough>
			</ErrorBoundary>,
		);
		expect(ref.current).not.toBeNull();

		// Before flashCopied, no timer is tracked.
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBeNull();

		(ref.current as ErrorBoundary).flashCopied();

		// After flashCopied, the tracked slot holds the id returned
		// by setTimeout.
		expect(setTimeoutMock).toHaveBeenCalledTimes(1);
		const expectedId = setTimeoutMock.mock.results[0]?.value;
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBe(
			expectedId,
		);
	});

	it("calling flashCopied twice clears the first timer before setting the second", () => {
		const ref = createRef<ErrorBoundary>();
		render(
			<ErrorBoundary ref={ref}>
				<Passthrough>child</Passthrough>
			</ErrorBoundary>,
		);
		expect(ref.current).not.toBeNull();

		// First call — arms timer #1.
		(ref.current as ErrorBoundary).flashCopied();
		expect(setTimeoutMock).toHaveBeenCalledTimes(1);
		const firstId = setTimeoutMock.mock.results[0]?.value;
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBe(firstId);
		// No clearTimeout yet (first call has nothing to clear).
		expect(clearTimeoutMock).not.toHaveBeenCalled();

		// Second call — must clear timer #1 before arming timer #2.
		(ref.current as ErrorBoundary).flashCopied();
		expect(clearTimeoutMock).toHaveBeenCalledTimes(1);
		// The clearTimeout call must use the FIRST timer's id (the
		// one that was previously tracked). This is the core
		// guarantee of the clear-before-set pattern.
		expect(clearTimeoutMock.mock.calls[0]?.[0]).toBe(firstId);

		// A new timer is armed (distinct id).
		expect(setTimeoutMock).toHaveBeenCalledTimes(2);
		const secondId = setTimeoutMock.mock.results[1]?.value;
		expect(secondId).not.toBe(firstId);
		// The tracked slot now holds the SECOND id.
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBe(secondId);
	});

	it("componentWillUnmount clears the tracked timer via clearTimeout", () => {
		const ref = createRef<ErrorBoundary>();
		render(
			<ErrorBoundary ref={ref}>
				<Passthrough>child</Passthrough>
			</ErrorBoundary>,
		);
		expect(ref.current).not.toBeNull();

		// Arm the timer.
		(ref.current as ErrorBoundary).flashCopied();
		const trackedId = internals(ref.current as ErrorBoundary).copiedTimer;
		expect(trackedId).not.toBeNull();

		// No clearTimeout yet.
		expect(clearTimeoutMock).not.toHaveBeenCalled();

		// Unmount — componentWillUnmount must call clearTimeout
		// with the tracked id.
		cleanup();

		expect(clearTimeoutMock).toHaveBeenCalledTimes(1);
		expect(clearTimeoutMock.mock.calls[0]?.[0]).toBe(trackedId);

		// NOTE: we can't assert on `ref.current.copiedTimer` after
		// unmount because React nulls the ref during cleanup(). The
		// clearTimeout assertion above is the load-bearing check —
		// it verifies the tracked id was passed to clearTimeout.
	});

	it("componentWillUnmount is a no-op when no timer is tracked (no spurious clearTimeout)", () => {
		const ref = createRef<ErrorBoundary>();
		render(
			<ErrorBoundary ref={ref}>
				<Passthrough>child</Passthrough>
			</ErrorBoundary>,
		);
		expect(ref.current).not.toBeNull();

		// Don't call flashCopied — no timer is tracked.
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBeNull();

		cleanup();

		// componentWillUnmount must NOT call clearTimeout when
		// there is nothing to clear (guards against a regression
		// where the `if (this.copiedTimer)` guard is removed).
		expect(clearTimeoutMock).not.toHaveBeenCalled();
	});

	it("the setTimeout callback clears the tracked slot after firing", () => {
		// This test verifies the callback's `this.copiedTimer = null`
		// line — without it, the slot would hold a stale id and the
		// NEXT flashCopied call would call clearTimeout with an
		// already-fired id (harmless but wasteful, and a sign the
		// bookkeeping is broken).
		const ref = createRef<ErrorBoundary>();
		render(
			<ErrorBoundary ref={ref}>
				<Passthrough>child</Passthrough>
			</ErrorBoundary>,
		);
		expect(ref.current).not.toBeNull();

		(ref.current as ErrorBoundary).flashCopied();
		expect(internals(ref.current as ErrorBoundary).copiedTimer).not.toBeNull();

		// Extract the callback that flashCopied registered with
		// setTimeout and invoke it (simulating the timer firing).
		expect(setTimeoutMock).toHaveBeenCalledTimes(1);
		const registeredCallback = setTimeoutMock.mock.calls[0]?.[0] as () => void;
		expect(typeof registeredCallback).toBe("function");

		registeredCallback();

		// After the callback fires, the tracked slot is cleared.
		expect(internals(ref.current as ErrorBoundary).copiedTimer).toBeNull();
	});
});
