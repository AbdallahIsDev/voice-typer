/**
 * F-17: axe-core automated WCAG scan for the ConnectionStatusScreen
 * component.
 *
 * ConnectionStatusScreen is rendered by App.tsx whenever the renderer
 * is not connected to the Python backend (startup, reconnect,
 * restart). The existing `pages/__tests__/ConnectionStatusScreen.test.tsx`
 * pins the per-state behavioural contract (titles, progress bar, retry
 * button click handler) but does not run an a11y scan. The existing
 * `a11y/accessibility.test.tsx` mocks this component as a bare
 * `<div data-testid="connection-status" role="alertdialog">` stub —
 * which hides the real component's a11y surface from CI (the comment
 * block on the component itself calls this out). This file fills that
 * gap by mounting the real component in each of its three states and
 * running axe-core against the rendered container.
 *
 * States covered:
 *   - `connecting`   — centered spinner + optional progress bar.
 *   - `disconnected` — last-error text + Retry button.
 *   - `restarting`   — restarting hint (no retry affordance).
 *
 * KNOWN VIOLATION (documented via `it.fails` below):
 *   - `aria-dialog-name` (impact: serious, cat.aria + best-practice):
 *     The outer wrapper `<div role="alertdialog" aria-labelledby="connection-status-title"
 *     aria-describedby="connection-status-desc">` references two IDs that
 *     are never attached to a DOM element. The `<EmptyState>` child
 *     renders the title in an `<h3>` and the description in a `<p>`,
 *     but does not receive / set `id` props, so the alertdialog has
 *     NO accessible name and NO accessible description. Screen-reader
 *     users hear only "alertdialog" with no context for what the
 *     dialog is about. Fix: pass through `id` props on EmptyState's
 *     `<h3>` / `<p>` (or replace the wrapper's `aria-labelledby` /
 *     `aria-describedby` with an `aria-label` / `aria-describedby`
 *     that points at real elements). Tracked separately — the
 *     ConnectionStatusScreen component file is owned by another
 *     slice and is out of scope for this test-only change.
 *
 * The per-state `it` tests below filter out the documented
 * `aria-dialog-name` violation so they can assert there are NO
 * OTHER a11y regressions in any state. The single `it.fails` test
 * runs the unfiltered axe scan so the violation stays visible in
 * CI output; when the bug is fixed, that test will start failing,
 * prompting the dev to drop the filter and flip `it.fails` → `it`.
 *
 * The color-contrast rule is disabled because the test environment
 * doesn't load the full Tailwind stylesheet (same approach as
 * `a11y/axe-core.test.tsx`).
 */
import { cleanup, render } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConnectionStatusScreen } from "@/components/layout/ConnectionStatusScreen";

// Spinner renders an <output aria-label="Loading"> that calls `t()` —
// mock the i18n module so we don't pull in the real translator (which
// would try to load locale chunks). We return the key as the label so
// the test can assert on a stable string. Same approach as the
// existing ConnectionStatusScreen.test.tsx.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	useT: () => (key: string) => key,
}));

// EmptyState uses HugeiconsIcon — mock to render a plain span so we
// don't need the @hugeicons/react runtime in the test (same approach
// as the existing ConnectionStatusScreen.test.tsx).
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ children }: { children?: React.ReactNode }) => (
		<span data-testid="hugeicon" aria-hidden>
			{children}
		</span>
	),
}));

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

// The `aria-dialog-name` violation documented in the file-level
// docstring has been RESOLVED — the ZU-36 fix replaced the wrapper's
// `role="alertdialog"` with `role="alert"`, so axe's
// aria-dialog-name rule (which only applies to dialog/alertdialog
// roles) no longer fires. No known violations remain, so the
// per-state `it` tests assert a completely clean scan and the
// formerly-`it.fails` test is now a plain `it`.
const KNOWN_VIOLATIONS = new Set<string>();

/** Axe helper — full (unfiltered) results for the `it.fails` test. */
async function runAxe(
	container: HTMLElement,
): Promise<axe.AxeResults["violations"]> {
	const results = await axe.run(container, AXE_OPTIONS);
	return results.violations.filter((v) => v.id !== "color-contrast");
}

/** Axe helper — filters out the disabled color-contrast rule AND the
 *  known/documented violations so the per-state tests can assert there
 *  are no NEW violations. */
async function expectNoNewAxeViolations(container: HTMLElement): Promise<void> {
	const violations = await runAxe(container);
	const newViolations = violations.filter((v) => !KNOWN_VIOLATIONS.has(v.id));
	expect(newViolations).toEqual([]);
}

describe("F-17: axe-core WCAG scan — ConnectionStatusScreen (all three states)", () => {
	afterEach(() => {
		cleanup();
	});

	// ── Per-state regression specs (filter out the documented
	//    `aria-dialog-name` violation; assert no OTHER violations). ──

	it("status='connecting' (no progress): no axe violations other than the documented aria-dialog-name", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		await expectNoNewAxeViolations(container);
	});

	it("status='connecting' (with progress bar): no axe violations other than the documented aria-dialog-name", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={42}
			/>,
		);
		await expectNoNewAxeViolations(container);
	});

	it("status='disconnected' (with last error): no axe violations other than the documented aria-dialog-name", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError="Python process exited with code 137"
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		await expectNoNewAxeViolations(container);
	});

	it("status='disconnected' (lastError=null, falls back to hint): no axe violations other than the documented aria-dialog-name", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		await expectNoNewAxeViolations(container);
	});

	it("status='restarting': no axe violations other than the documented aria-dialog-name", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="restarting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		await expectNoNewAxeViolations(container);
	});

	// ── Documented-violation regression spec ──────────────────────────
	// This test runs the UNFILTERED axe scan and asserts the violations
	// list is non-empty. It is marked `it.fails` because the underlying
	// ConnectionStatusScreen component has a known `aria-dialog-name`
	// violation (see file-level docstring) that is out of scope for
	// this test-only change. When the component is fixed, this test
	// will START FAILING (axe will return no violations), prompting
	// the dev to:
	//   1. Remove the `KNOWN_VIOLATIONS` filter above.
	//   2. Flip this test from `it.fails` to `it`.
	//   3. Delete the per-state tests' filter (they become plain
	//      `expectNoAxeViolations` calls).
	it("aria-dialog-name is resolved: role='alert' wrapper yields a clean axe scan (ZU-36)", async () => {
		const { container } = render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		const violations = await runAxe(container);
		// The ZU-36 fix replaced role="alertdialog" with role="alert",
		// so the formerly-documented aria-dialog-name violation is gone.
		expect(violations).toEqual([]);
	});
});
