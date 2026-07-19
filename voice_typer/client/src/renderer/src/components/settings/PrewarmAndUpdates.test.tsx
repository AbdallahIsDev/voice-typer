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
