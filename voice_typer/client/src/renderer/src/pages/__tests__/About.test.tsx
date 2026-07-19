/**
 * Tests for the About page.
 *
 * UX-20 / SET-5: the About page was slimmed down from a 726-line catch-all
 * to a focused ~300-line page with three sections (Diagnostics, Privacy,
 * Resources). The Help section was removed (it duplicates the `?` overlay),
 * and Cache Status + Updates were removed from About — they now live in
 * Settings → Troubleshooting via the PrewarmAndUpdates component (added
 * back after the slim-down incorrectly dropped them from the UI). These
 * tests cover:
 *
 *   - formatBytes(): 0, MB range, GB range
 *   - formatRelativeTime(): null, "never", <1 min, minutes, hours, days, ISO fallback
 *   - Smoke test: Diagnostics + Privacy + Resources render
 *   - Negative test: Help section is gone (no "Start / Stop dictation" row)
 *   - Negative test: Cache Status section is gone from About (no "Run Prewarm Now" button)
 *   - Negative test: Updates section is gone from About (no "Check for Updates" button)
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoist the mock call handler so it's available inside vi.mock factories.
const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

// Stub hugeicons (About.tsx doesn't use icons directly, but the import
// chain via SettingsSection may pull them in).
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ children }: { children?: React.ReactNode }) => (
		<span data-testid="hugeicon">{children}</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", () => ({
	RefreshIcon: { name: "RefreshIcon" },
}));

// sonner is imported by About.tsx for toast notifications.
vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

// next-themes is imported transitively.
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

import { formatBytes, formatRelativeTime } from "@/pages/About";

// ─── formatBytes ───────────────────────────────────────────────────────

describe("formatBytes", () => {
	it("returns '0 MB' for 0 bytes", () => {
		expect(formatBytes(0)).toBe("0 MB");
	});

	it("returns '0 MB' for negative bytes", () => {
		expect(formatBytes(-100)).toBe("0 MB");
	});

	it("returns MB for sub-GB values", () => {
		expect(formatBytes(1024 * 1024)).toBe("1 MB");
		expect(formatBytes(500 * 1024 * 1024)).toBe("500 MB");
	});

	it("returns GB for GB-range values (1 decimal)", () => {
		expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
		expect(formatBytes(2.4 * 1024 * 1024 * 1024)).toBe("2.4 GB");
		// 1,750,000,000 bytes = 1.628 GB → rounds to 1.6 GB
		expect(formatBytes(1750000000)).toBe("1.6 GB");
	});

	it("rounds MB values (no decimals)", () => {
		// 1.5 MB → rounds to 2 MB
		expect(formatBytes(1.5 * 1024 * 1024)).toBe("2 MB");
	});
});

// ─── formatRelativeTime ───────────────────────────────────────────────

describe("formatRelativeTime", () => {
	it("returns 'Never' for null", () => {
		// Note: t("about.neverRun") returns "Never" in English.
		expect(formatRelativeTime(null)).toBe("Never");
	});

	it("returns '<1 min ago' for a timestamp 30 seconds ago", () => {
		const thirtySecondsAgo = new Date(Date.now() - 30_000).toISOString();
		expect(formatRelativeTime(thirtySecondsAgo)).toBe("<1 min ago");
	});

	it("returns 'N min ago' for a timestamp minutes ago", () => {
		const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString();
		expect(formatRelativeTime(fiveMinAgo)).toBe("5 min ago");
	});

	it("returns 'N h ago' for a timestamp hours ago", () => {
		const threeHrsAgo = new Date(Date.now() - 3 * 60 * 60_000).toISOString();
		expect(formatRelativeTime(threeHrsAgo)).toBe("3 h ago");
	});

	it("returns 'N d ago' for a timestamp days ago (under 7)", () => {
		const threeDaysAgo = new Date(
			Date.now() - 3 * 24 * 60 * 60_000,
		).toISOString();
		expect(formatRelativeTime(threeDaysAgo)).toBe("3 d ago");
	});

	it("returns the raw ISO string for timestamps older than 7 days", () => {
		const tenDaysAgo = new Date(
			Date.now() - 10 * 24 * 60 * 60_000,
		).toISOString();
		const result = formatRelativeTime(tenDaysAgo);
		// Should fall back to the raw ISO string (not a relative format).
		expect(result).toBe(tenDaysAgo);
	});

	it("returns the raw string for unparseable input", () => {
		expect(formatRelativeTime("not-a-date")).toBe("not-a-date");
	});
});

// ─── Slimmed-down About page (UX-20 / SET-5) ──────────────────────────

describe("About page — slimmed-down sections (UX-20 / SET-5)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp",
					loaded_via: "cpu/int8/tiny.en",
				});
			}
			if (type === "get_config") {
				return Promise.resolve({
					asr_backend: "whisper",
					model_size: "tiny.en",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			return Promise.resolve({});
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("renders Diagnostics, Privacy, and Resources sections", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		// Diagnostics section heading (i18n key about.diagnosticsTitle).
		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});

		// Privacy section heading.
		expect(screen.getByText("Privacy")).toBeTruthy();

		// Resources section heading.
		expect(screen.getByText("Resources & Feedback")).toBeTruthy();
	});

	it("does NOT render the Help section (removed — duplicates `?` overlay)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});

		// The Help section previously rendered a "Start / Stop dictation"
		// row. After UX-20, that row is gone (the help overlay is the
		// canonical source for shortcut labels).
		expect(screen.queryByText("Start / Stop dictation")).toBeNull();
		// The Help section heading itself is also gone.
		expect(screen.queryByText("Help")).toBeNull();
	});

	it("does NOT render the Cache Status section (removed — belongs on a diagnostics surface)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});

		// The Cache Status card previously had a "Run Prewarm Now"
		// button and a "Refresh" button — both removed.
		expect(screen.queryByText("Cache Status")).toBeNull();
		expect(screen.queryByText("Run Prewarm Now")).toBeNull();
		expect(screen.queryByText("View prewarm log")).toBeNull();
	});

	it("does NOT render the Updates section (removed — belongs on a diagnostics surface)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});

		// The Updates section previously had a "Check for Updates"
		// button — removed.
		expect(screen.queryByText("Updates")).toBeNull();
		expect(screen.queryByText("Check for Updates")).toBeNull();
	});

	it("does not call get_prewarm_status on mount (Cache Status section is gone)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});

		// The slimmed-down About page no longer fetches prewarm status —
		// only get_status + get_config are called on mount.
		const prewarmCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_prewarm_status",
		);
		expect(prewarmCalls.length).toBe(0);
	});
});
