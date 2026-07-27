/**
 * Tests for PrewarmAndUpdates — restored Cache Status + Updates surfaces.
 *
 * UX-20 / SET-5 slimmed About.tsx and dropped the Cache Status and Updates
 * sections from the UI (the "relocated to Settings → Troubleshooting" claim
 * was inaccurate). This component restores them into Settings. These tests
 * verify the functionality is actually present and wired:
 *
 *   - Cache Status + Updates sections render (headings + action buttons)
 *   - prewarm cache status is fetched on mount and the badge renders
 *   - "Check for Updates" compares the installed vs latest version via
 *     compareSemver and surfaces the appropriate snackbar message
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
		Download01Icon: make("Download01Icon"),
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
	// Stub fetch so the mount-time / manual update checks don't hit network.
	vi.stubGlobal(
		"fetch",
		vi.fn().mockResolvedValue({
			ok: true,
			json: async () => ({ tag_name: "v1.0.0" }),
		}),
	);
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

	// CR-11 / S3-CR-11 regression: the previous implementation fired a
	// `fetch("https://api.github.com/...")` call inside a mount-time
	// `useEffect`, which leaked the user's public IP, request timestamp,
	// and Electron User-Agent to GitHub on EVERY Settings page open.
	// That broke the "offline guarantee" the project advertises.
	//
	// The fix removed the auto-firing useEffect entirely; the only
	// network call to api.github.com now happens inside the explicit
	// `handleManualCheck` handler attached to the "Check for Updates"
	// button. This test asserts that contract so a future regression
	// (e.g. someone re-adding a mount-time fetch) fails loudly.
	it("does NOT fire any fetch on mount (CR-11 / S3-CR-11 privacy regression)", async () => {
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

	it("renders the prewarm action buttons", () => {
		render(<PrewarmAndUpdates />);
		expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		expect(screen.getByText("View prewarm log")).toBeTruthy();
		expect(screen.getByText("Check for Updates")).toBeTruthy();
	});

	it("fetches prewarm status on mount and shows the cache badge", async () => {
		render(<PrewarmAndUpdates />);
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_prewarm_status");
		});
		// cache_label "hot" → the Hot badge text renders.
		expect(await screen.findByText("Hot")).toBeTruthy();
	});

	it("surfaces a newer version via the manual update check", async () => {
		// Latest release is newer than the installed (package.json) version.
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue({
				ok: true,
				json: async () => ({ tag_name: "v99.0.0" }),
			}),
		);
		render(<PrewarmAndUpdates />);
		screen.getByText("Check for Updates").click();
		await waitFor(() => {
			expect(mockShowSnack).toHaveBeenCalled();
		});
		const calledWithNewVersion = mockShowSnack.mock.calls.some(([msg]) =>
			String(msg).includes("New version available"),
		);
		expect(calledWithNewVersion).toBe(true);
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
