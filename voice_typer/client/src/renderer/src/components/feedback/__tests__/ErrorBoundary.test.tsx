import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Component } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

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

describe("ErrorBoundary fallback behavior", () => {
	afterEach(() => {
		cleanup();
		shouldThrow = false;
	});

	it("shows the fallback UI when a child throws during render", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="explosive-failure" />
			</ErrorBoundary>,
		);
		expect(screen.getByText("Something went wrong")).toBeTruthy();
		expect(screen.getByText("explosive-failure")).toBeTruthy();
		expect(screen.queryByTestId("child-content")).toBeNull();
	});

	it("Try Again recovers the children after the error is fixed", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="recover-me" />
			</ErrorBoundary>,
		);
		expect(screen.getByText("Something went wrong")).toBeTruthy();

		shouldThrow = false;
		fireEvent.click(screen.getByText("Try Again"));

		expect(screen.getByTestId("child-content")).toBeTruthy();
		expect(screen.queryByText("Something went wrong")).toBeNull();
	});

	it("Reload App calls window.location.reload", () => {
		shouldThrow = true;
		const reloadSpy = vi.fn();
		const original = window.location;
		Object.defineProperty(window, "location", {
			value: { ...window.location, reload: reloadSpy },
			writable: true,
			configurable: true,
		});

		try {
			render(
				<ErrorBoundary>
					<Thrower message="reload-test" />
				</ErrorBoundary>,
			);
			fireEvent.click(screen.getByText("Reload App"));
			expect(reloadSpy).toHaveBeenCalledTimes(1);
		} finally {
			Object.defineProperty(window, "location", {
				value: original,
				writable: true,
				configurable: true,
			});
		}
	});
});
