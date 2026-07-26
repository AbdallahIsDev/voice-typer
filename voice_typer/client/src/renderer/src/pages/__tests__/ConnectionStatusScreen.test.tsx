/**
 * NH-1 (session NH): ConnectionStatusScreen render tests.
 *
 * The component was previously a `return null` stub. App.tsx rendered it
 * whenever the renderer wasn't connected to the Python backend, but
 * because the body was empty the user saw a blank main pane during
 * startup / reconnect / restart. The accessibility test mocked it as
 * `<div data-testid="connection-status" />`, which hid the regression
 * from CI.
 *
 * These tests exercise the real component directly so a future regression
 * to a stub would fail loudly. The App-level test
 * (`a11y/accessibility.test.tsx`) keeps the mock removed so the real
 * component is also exercised through the App render path.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionStatusScreen } from "@/components/layout/ConnectionStatusScreen";

// Spinner renders an <output aria-label="Loading"> that calls `t()` —
// mock the i18n module so we don't pull in the real translator (which
// would try to load locale chunks). We return the key as the label so
// the test can assert on a stable string.
vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	useT: () => (key: string) => key,
}));

// EmptyState uses HugeiconsIcon — mock to render a plain span so we
// don't need the @hugeicons/react runtime in the test.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ children }: { children?: React.ReactNode }) => (
		<span data-testid="hugeicon">{children}</span>
	),
}));

describe("NH-1: ConnectionStatusScreen", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});
	afterEach(() => {
		cleanup();
	});

	it("renders a centered spinner card with the starting-backend title for status='connecting'", () => {
		render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);

		// The connecting branch renders a heading with the
		// starting-backend i18n key (mocked to return the key).
		expect(
			screen.getByRole("heading", { name: "app.startingBackend" }),
		).toBeTruthy();
		// The spinner is an <output> with aria-label="a11y.loading"
		// (also mocked to return the key).
		expect(screen.getByRole("status")).toBeTruthy();
	});

	it("renders a restarting-backend title for status='restarting'", () => {
		render(
			<ConnectionStatusScreen
				status="restarting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: "app.restartingBackend" }),
		).toBeTruthy();
	});

	it("shows the download progress percentage when connectingProgress is provided", () => {
		render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={42}
			/>,
		);

		// The percentage label is rendered as plain text.
		expect(screen.getByText("42%")).toBeTruthy();
	});

	it("renders the lost-connection error card with a Retry button for status='disconnected'", () => {
		const onRetry = vi.fn();
		render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError="boom"
				onRetry={onRetry}
				connectingProgress={null}
			/>,
		);

		// The disconnected branch renders an EmptyState error
		// variant — the title is the lost-connection i18n key
		// (mocked to return the key).
		expect(
			screen.getByRole("heading", { name: "app.lostConnection" }),
		).toBeTruthy();
		// The error message is rendered verbatim.
		expect(screen.getByText("boom")).toBeTruthy();
		// Two retry affordances: the EmptyState action button and
		// the secondary ghost button below.
		const retryButtons = screen.getAllByRole("button", {
			name: "app.retryConnection",
		});
		expect(retryButtons.length).toBeGreaterThanOrEqual(1);
		retryButtons[0].click();
		expect(onRetry).toHaveBeenCalled();
	});

	it("falls back to the lost-connection hint when lastError is null", () => {
		render(
			<ConnectionStatusScreen
				status="disconnected"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);

		// The EmptyState description falls back to
		// app.lostConnectionHint when lastError is null.
		expect(screen.getByText("app.lostConnectionHint")).toBeTruthy();
	});

	it("does not render the progress bar when connectingProgress is null", () => {
		render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);

		// No percentage text node should be present.
		expect(screen.queryByText(/\d+%/)).toBeNull();
	});
});
