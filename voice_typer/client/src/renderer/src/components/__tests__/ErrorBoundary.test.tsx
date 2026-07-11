/**
 * Tests for the ErrorBoundary component.
 *
 * ErrorBoundary catches render-time exceptions in its children and
 * shows a fallback UI with "Try Again" / "Reload App" actions. The
 * fallback has role="alert" so screen readers announce it.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Component } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

// Module-level toggle for the throwing child. React 19's StrictMode
// (and dev-mode double-invocation of render) breaks counter-based
// approaches because the render runs twice per commit. A simple
// boolean flag set before render is robust to double-invocation.
let shouldThrow = false;

interface ThrowerProps {
	message: string;
}

class Thrower extends Component<ThrowerProps> {
	render() {
		if (shouldThrow) {
			throw new Error(this.props.message);
		}
		return <div data-testid="child-content">child-ok</div>;
	}
}

describe("ErrorBoundary", () => {
	afterEach(() => {
		cleanup();
		shouldThrow = false;
	});

	it("renders children normally when no error is thrown", () => {
		render(
			<ErrorBoundary>
				<Thrower message="boom" />
			</ErrorBoundary>,
		);
		expect(screen.getByTestId("child-content")).toBeTruthy();
		expect(screen.queryByText("Something went wrong")).toBeNull();
	});

	it("shows the fallback UI when a child throws during render", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="explosive-failure" />
			</ErrorBoundary>,
		);
		expect(screen.getByText("Something went wrong")).toBeTruthy();
		// The thrown error message is rendered in a <pre> for diagnostics.
		expect(screen.getByText("explosive-failure")).toBeTruthy();
		// Original children are gone.
		expect(screen.queryByTestId("child-content")).toBeNull();
	});

	it('the default fallback has role="alert" for screen readers', () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="a11y-fail" />
			</ErrorBoundary>,
		);
		const alert = screen.getByRole("alert");
		expect(alert).toBeTruthy();
		// Sanity: the alert region contains the heading copy.
		expect(alert.textContent).toContain("Something went wrong");
	});

	it('"Try Again" resets the error state so children re-render', () => {
		// Start in the throwing state — the boundary shows the fallback.
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="recover-me" />
			</ErrorBoundary>,
		);
		expect(screen.getByText("Something went wrong")).toBeTruthy();

		// Flip the toggle so the child will render cleanly after reset.
		shouldThrow = false;

		// Click "Try Again" — the boundary clears its error state and
		// re-renders the children.
		fireEvent.click(screen.getByText("Try Again"));

		expect(screen.getByTestId("child-content")).toBeTruthy();
		expect(screen.queryByText("Something went wrong")).toBeNull();
	});

	it('"Reload App" calls window.location.reload', () => {
		shouldThrow = true;
		const reloadSpy = vi.fn();
		// jsdom's window.location.reload is not implemented by default —
		// replace it with a spy for the duration of this test.
		const original = window.location.reload;
		Object.defineProperty(window, "location", {
			value: { ...window.location, reload: reloadSpy },
			writable: true,
			configurable: true,
		});

		render(
			<ErrorBoundary>
				<Thrower message="reload-test" />
			</ErrorBoundary>,
		);
		fireEvent.click(screen.getByText("Reload App"));
		expect(reloadSpy).toHaveBeenCalledTimes(1);

		// Restore the original location object.
		Object.defineProperty(window, "location", {
			value: original,
			writable: true,
			configurable: true,
		});
	});

	it("renders a custom fallback prop instead of the default UI when provided", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary fallback={<div data-testid="custom-fallback">Custom</div>}>
				<Thrower message="custom-fallback-case" />
			</ErrorBoundary>,
		);
		expect(screen.getByTestId("custom-fallback")).toBeTruthy();
		// The default UI copy must NOT appear when a custom fallback is given.
		expect(screen.queryByText("Something went wrong")).toBeNull();
	});
});
