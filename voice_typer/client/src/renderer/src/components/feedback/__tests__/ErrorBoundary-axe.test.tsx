/**
 * F-17: axe-core automated WCAG scan for the ErrorBoundary fallback UI.
 *
 * The existing `a11y/axe-core.test.tsx` mocks `ErrorBoundary` as a
 * pass-through wrapper (`<>{children}</>`) so it can mount pages without
 * their own boundary. That mock hides the real fallback's a11y surface
 * from CI. This file mounts the real `ErrorBoundary`, forces a render
 * crash in a child, and runs axe-core against the resulting fallback UI
 * (heading + alert region + recovery button row).
 *
 * The color-contrast rule is disabled because the test environment
 * doesn't load the full Tailwind stylesheet (same approach as
 * `a11y/axe-core.test.tsx`).
 */
import { cleanup, render } from "@testing-library/react";
import axe from "axe-core";
import { Component } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

// Module-level toggle for the throwing child. React 19's dev-mode
// double-invocation of render breaks counter-based approaches; a simple
// boolean flag set before render is robust to double-invocation (same
// approach as the existing ErrorBoundary.test.tsx).
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

/** Axe helper — filters out the disabled color-contrast rule. */
async function expectNoAxeViolations(container: HTMLElement): Promise<void> {
	const results = await axe.run(container, AXE_OPTIONS);
	const violations = results.violations.filter(
		(v) => v.id !== "color-contrast",
	);
	expect(violations).toEqual([]);
}

describe("F-17: axe-core WCAG scan — ErrorBoundary fallback UI", () => {
	afterEach(() => {
		cleanup();
		shouldThrow = false;
	});

	it("default fallback (after a child throws): no axe violations", async () => {
		shouldThrow = true;
		const { container } = render(
			<ErrorBoundary>
				<Thrower message="axe-scan-fallback" />
			</ErrorBoundary>,
		);
		// The fallback renders a role="alert" region with a heading,
		// description, the raw error message in a <pre>, and a row of
		// recovery buttons (Reset settings / Try again / Reload App /
		// Copy error / Open logs). axe scans the whole region.
		await expectNoAxeViolations(container);
	});
});
