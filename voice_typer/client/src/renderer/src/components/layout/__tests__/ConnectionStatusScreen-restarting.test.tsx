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

// EmptyState uses HugeiconsIcon, mock to render a plain span so we
// don't need the @hugeicons/react runtime in the test.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ children }: { children?: React.ReactNode }) => (
		<span data-testid="hugeicon" aria-hidden>
			{children}
		</span>
	),
}));

describe("ConnectionStatusScreen, roleless wrapper + restarting spinner + force-retry action", () => {
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
		// (role="status") inside the local calm card.
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
		// has implicit role="status", assert it renders. The
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

	it("status='reconnecting' renders the shared recovering UI (GAP-A parity with restarting)", () => {
		const onRetry = vi.fn();
		render(
			<ConnectionStatusScreen
				status="reconnecting"
				lastError={null}
				onRetry={onRetry}
				connectingProgress={null}
			/>,
		);
		// Same spinner + restartingBackend title + force-retry as
		// "restarting" (isRecoveringStatus), never the disconnected
		// lost-connection copy.
		expect(screen.getByRole("status", { name: "a11y.loading" })).toBeTruthy();
		expect(
			screen.getByRole("heading", { name: "app.restartingBackend" }),
		).toBeTruthy();
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

	it("connecting/restarting render the calm card with no error icon (Spinner output only)", () => {
		render(
			<ConnectionStatusScreen
				status="connecting"
				lastError={null}
				onRetry={vi.fn()}
				connectingProgress={null}
			/>,
		);
		// Same local calm card as disconnected: bg-card surface, no
		// destructive wash.
		const card = document.querySelector(
			'[data-testid="connection-status"] > div',
		) as HTMLElement;
		expect(card).toBeTruthy();
		expect(card.className).toContain("bg-card");
		expect(card.className).not.toContain("bg-destructive/5");
		// No error-icon disc in the connecting state; the Spinner output
		// is the loading affordance.
		expect(card.className).not.toContain("border-destructive");
		expect(
			screen.getByRole("heading", { name: "app.startingBackend" }).tagName,
		).toBe("H2");
	});
});
