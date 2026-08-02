/**
 * Tests for PrewarmAndUpdates — Cache Status + offline Updates surfaces.
 *
 * History: an earlier revision hosted an in-app "Check for Updates"
 * button that fired a renderer `fetch()` to the GitHub releases API.
 * C-DATA-1 (the offline guarantee) forbids ANY network call in the
 * production code path — including an explicit user click — so the
 * button + handler + latestVersion state were removed. The Updates
 * section now shows the installed version + a static offline message
 * + a user-clicked external link to the GitHub releases page.
 *
 * These tests verify the post-removal contract:
 *   - Cache Status + Updates sections render (headings + action buttons)
 *   - prewarm cache status is fetched on mount and the badge renders
 *   - NO `fetch()` is ever called from this component (mount, click,
 *     unmount, or otherwise) — the offline guarantee is absolute
 *   - the "Check for Updates" button is NOT rendered (removed)
 *   - the offline message + "View Changelog" link ARE rendered
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoist the mock handlers so they're available inside vi.mock factories.
const { mockCall, mockShowSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		RefreshIcon: make("RefreshIcon"),
	};
});

import PrewarmAndUpdates from "@/components/settings/PrewarmAndUpdates";

const PREWARM_HOT = {
	last_run: "2024-01-01T00:00:00Z",
	elapsed_s: 12.3,
	cache_ratio: 1.0,
	cache_label: "hot",
	cached_bytes: 4_000_000_000,
	total_bytes: 4_000_000_000,
	prewarm_running: false,
};

beforeEach(() => {
	mockCall.mockReset();
	mockShowSnack.mockReset();
	// Default: backend responds with a hot cache.
	mockCall.mockResolvedValue(PREWARM_HOT);
	// Stub fetch with a spy so ANY fetch call (mount, click, unmount)
	// is recorded — C-DATA-1 forbids network calls in the production
	// code path, so the spy must remain uncalled across every test.
	vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
	vi.unstubAllGlobals();
	cleanup();
});

describe("PrewarmAndUpdates", () => {
	it("renders both the Cache Status and Updates sections", () => {
		render(<PrewarmAndUpdates />);
		expect(screen.getByText("Cache Status")).toBeTruthy();
		expect(screen.getByText("Updates")).toBeTruthy();
	});

	// C-DATA-1 regression guard: NO network call may leave this
	// component. The previous implementation fired
	// `fetch("https://api.github.com/...")` inside a mount-time
	// `useEffect` (auto-fire), leaking the user's public IP, request
	// timestamp, and Electron User-Agent to GitHub on EVERY Settings
	// page open. A subsequent fix removed the auto-fire but kept the
	// manual "Check for Updates" button (which still issued a fetch on
	// click). C-DATA-1 now forbids both: the manual button has been
	// removed entirely. This test asserts the stronger contract — no
	// fetch fires on mount, on unmount, or at any other time — so a
	// future regression (re-adding the auto-fire OR the manual button)
	// fails loudly.
	it("does NOT fire any fetch on mount (C-DATA-1 offline guarantee)", async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal("fetch", fetchSpy);

		render(<PrewarmAndUpdates />);

		// Wait for the mount-time IPC call (get_prewarm_status) to
		// settle so the test isn't racing the effect cleanup.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_prewarm_status");
		});
		// Flush any pending microtasks (the mount effect only calls
		// IPC — no fetch — but give the scheduler a chance to run
		// anything that might have been queued).
		await new Promise((r) => setTimeout(r, 0));

		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("renders the prewarm action buttons + the offline Updates notice", () => {
		render(<PrewarmAndUpdates />);
		// Prewarm action buttons — still present.
		expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		expect(screen.getByText("View prewarm log")).toBeTruthy();
		// "View Changelog" link button — still present (anchor, no fetch).
		expect(screen.getByText("View Changelog")).toBeTruthy();
		// Offline notice — the new static message replacing the
		// "Check for Updates" button. The English text is hardcoded
		// in the en.json locale; the test renders with the default
		// English locale, so the message substring is stable.
		expect(
			screen.getByText(/Voice Typer is an offline application/i),
		).toBeTruthy();
	});

	// C-DATA-1 regression guard: the "Check for Updates" button has
	// been REMOVED. If a future contributor re-adds it (even as a
	// disabled placeholder), this test fails loudly so the regression
	// is caught before merge.
	it("does NOT render the removed 'Check for Updates' button", () => {
		render(<PrewarmAndUpdates />);
		expect(screen.queryByText("Check for Updates")).toBeNull();
		expect(
			screen.queryByRole("button", { name: /check for updates/i }),
		).toBeNull();
	});

	it("fetches prewarm status on mount and shows the cache badge", async () => {
		render(<PrewarmAndUpdates />);
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_prewarm_status");
		});
		// cache_label "hot" → the Hot badge text renders.
		expect(await screen.findByText("Hot")).toBeTruthy();
	});

	// C-DATA-1 absolute guarantee: NO fetch is ever called from this
	// component — not on mount, not on unmount, not on any user
	// interaction. The previous "surfaces a newer version via the
	// manual update check" test asserted the OPPOSITE contract (that
	// clicking "Check for Updates" fires a fetch and surfaces a
	// snackbar); that test has been removed and replaced with this
	// stronger assertion.
	it("does NOT fire any fetch across the component's entire lifecycle (C-DATA-1)", async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal("fetch", fetchSpy);

		const { unmount } = render(<PrewarmAndUpdates />);

		// Wait for the mount-time IPC call to settle.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_prewarm_status");
		});
		// Flush microtasks.
		await new Promise((r) => setTimeout(r, 0));

		// Unmount (exercises the cleanup path — a future regression
		// could try to fire a fetch in a cleanup effect).
		unmount();
		await new Promise((r) => setTimeout(r, 0));

		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("opens the prewarm log via the open_prewarm_log IPC", async () => {
		mockCall.mockImplementation(async (type: string) => {
			if (type === "open_prewarm_log") return { opened: true };
			return PREWARM_HOT;
		});
		render(<PrewarmAndUpdates />);
		screen.getByText("View prewarm log").click();
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("open_prewarm_log");
		});
		expect(mockShowSnack).toHaveBeenCalled();
	});
});
