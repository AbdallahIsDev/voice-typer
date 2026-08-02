/**
 * ErrorBoundary focus-management tests.
 *
 * When the boundary triggers (a child throws), the fallback UI is a long
 * ``role="alert"`` region with a description, a <pre> stack trace, and a
 * row of recovery buttons. Without programmatic focus management,
 * keyboard / SR users land at the top of the alert region and must Tab
 * through the whole description before reaching any actionable control.
 *
 * The boundary's ``componentDidUpdate`` moves focus to the primary
 * recovery button ("Reset settings") when ``hasError`` transitions from
 * false → true, so keyboard / SR users land directly on the recommended
 * recovery affordance (mirrors ARIA Authoring Practices guidance for
 * error dialogs).
 *
 * These tests mount the real ``ErrorBoundary``, trigger a render crash
 * via a Thrower child, and assert the Reset button receives focus.
 */
import { cleanup, render } from "@testing-library/react";
import { Component } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

// Module-level toggle for the throwing child. React 19's dev-mode
// double-invocation of render breaks counter-based approaches; a simple
// boolean flag set before render is robust to double-invocation (same
// approach as the existing ErrorBoundary-axe.test.tsx).
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

// Stub the i18n ``t()`` function so the Reset button's label resolves
// to the stable catalog key (no dependency on the full i18n catalog).
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

beforeEach(() => {
	shouldThrow = false;
});

afterEach(() => {
	cleanup();
	shouldThrow = false;
});

describe("ErrorBoundary — focus management on trigger", () => {
	it("moves focus to the Reset settings button when a child throws", () => {
		shouldThrow = true;
		render(
			<ErrorBoundary>
				<Thrower message="focus-test" />
			</ErrorBoundary>,
		);
		// The Reset button is the primary recovery affordance and the
		// first button in the recovery row. After the boundary commits
		// the fallback UI, ``componentDidUpdate`` focuses it.
		const resetBtn = document.querySelector(
			'button[aria-describedby="error-boundary-reset-hint"]',
		) as HTMLButtonElement | null;
		expect(resetBtn).toBeTruthy();
		expect(document.activeElement).toBe(resetBtn);
	});

	it("does NOT render a reset button when the boundary has not triggered", () => {
		// Render a non-throwing child — the boundary never enters the
		// error state, so no fallback UI is rendered.
		shouldThrow = false;
		render(
			<ErrorBoundary>
				<Thrower message="never-thrown" />
			</ErrorBoundary>,
		);
		const resetBtn = document.querySelector(
			'button[aria-describedby="error-boundary-reset-hint"]',
		);
		expect(resetBtn).toBeNull();
	});
});
