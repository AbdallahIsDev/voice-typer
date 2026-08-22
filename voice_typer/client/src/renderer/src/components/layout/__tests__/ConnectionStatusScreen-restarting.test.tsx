/**
 * Tests for the  fix: ConnectionStatusScreen `restarting` state
 * + `role="alert"` switch + secondary "Force retry" action.
 *
 * Background: the  finding documented that the root element used
 * `role="alertdialog" aria-modal="false"` (contradictory — alertdialog
 * is implicitly modal) and that the `restarting` state had NO spinner,
 * NO progress, NO action button. Users perceived the app as frozen
 * during transient backend restarts and had no in-app escape.
 *
 * The fix:
 *  - Replaces `role="alertdialog"` with a ROLELESS wrapper (the
 *    assertive announcement contract later moved onto the description
 *    node's polite `role="status"`, so the wrapper carries no role at
 *    all) and drops `aria-modal` entirely.
 *  - Renders the `<Spinner />` for BOTH `isConnecting` AND
 *    `isRestarting` (previously `isConnecting`-only).
 *  - Adds a secondary "Force retry" button when `isRestarting` that
 *    short-circuits `useConnection`'s 60s safety timer.
 *
 * This file is the regression guard for those three changes. The
 * existing `pages/__tests__/ConnectionStatusScreen.test.tsx` and
 * `components/layout/__tests__/ConnectionStatusScreen-axe.test.tsx`
 * files pin the broader per-state behavioral + a11y contracts; this
 * file narrows in on the  surface so a future regression
 * to the alertdialog role or the missing restarting-state spinner
 * fails loudly here.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
// don't need the @hugeicons/react runtime in the test.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ children }: { children?: React.ReactNode }) => (
		<span data-testid="hugeicon" aria-hidden>
			{children}
		</span>
	),
}));

describe("ConnectionStatusScreen — roleless wrapper + restarting spinner + force-retry action", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	afterEach(() => {
		cleanup();
	});

	it('root element is roleless (NOT role="alertdialog", NOT role="alert")', () => {
		render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		// The outer wrapper is the element with data-testid="connection-status".
		const root = document.querySelector(
			'[data-testid="connection-status"]',
		) as HTMLElement;
		expect(root).toBeTruthy();
		// The wrapper is ROLELESS: an assertive alert/alertdialog region
		// around the whole card re-announced every progressbar tick. The
		// polite announcement contract now lives on the description node
		// (role="status") inside EmptyState's error card.
		expect(root.getAttribute("role")).toBeNull();
		//`aria-modal` is dropped entirely (no modal → no modal
		// attribute). The previous `aria-modal="false"` was contradictory
		// on a `role="alertdialog"` (alertdialog is implicitly modal).
		expect(root.hasAttribute("aria-modal")).toBe(false);
	});

	it("the description node is a polite status region (not assertive)", () => {
		render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		const status = document.querySelector(
			'[data-testid="connection-status"] p[role="status"]',
		);
		expect(status?.textContent).toBe("app.lostConnectionHint");
	});

	it("status='restarting' renders the Spinner (parity with isConnecting)", () => {
		render(
			<ConnectionStatusScreen
				status="restarting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		// The Spinner is wrapped in an <output aria-live="polite">
		// (so SR users hear the loading state). The output element
		// has implicit role="status" — assert it renders. The
		// description node is also a status region now, so query by
		// accessible name.
		expect(screen.getByRole("status", { name: "a11y.loading" })).toBeTruthy();
		// The restarting-state title is the localized
		// `app.restartingBackend` key (mocked to return the key).
		expect(
			screen.getByRole("heading", { name: "app.restartingBackend" }),
		).toBeTruthy();
	});

	it("status='restarting' renders the secondary \"Force retry\" action that calls onRetry on click", () => {
		const onRetry = vi.fn();
		render(
			<ConnectionStatusScreen
				status="restarting"
				lastError={null}
				onRetry={onRetry}
				connectingProgress={null}
			/>,
		);
		// The force-retry button is exposed via a stable testid so
		// integration tests can find it without relying on label
		// text (the label is localized).
		const forceRetry = screen.getByTestId("connection-status-force-retry");
		expect(forceRetry.tagName).toBe("BUTTON");
		forceRetry.click();
		expect(onRetry).toHaveBeenCalledTimes(1);
	});

	it("status='connecting' does NOT render the force-retry button (restarting-only affordance)", () => {
		render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		expect(screen.queryByTestId("connection-status-force-retry")).toBeNull();
	});

	it("status='disconnected' does NOT render the force-retry button (uses the primary Retry action instead)", () => {
		render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError="boom"
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		expect(screen.queryByTestId("connection-status-force-retry")).toBeNull();
	});
});
