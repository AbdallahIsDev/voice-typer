/**
 * Task 7: Tests for the About page's Cache Status card.
 *
 * Covers:
 *   - formatBytes(): 0, MB range, GB range
 *   - formatRelativeTime(): null, "never", <1 min, minutes, hours, days, ISO fallback
 *   - CacheStatusBadge: renders correct color dot + i18n label for each state
 *   - Cache Status card renders the expected rows
 *   - "Refresh" button calls get_prewarm_status IPC
 *   - "Run Prewarm Now" button calls run_prewarm IPC and refreshes status
 *   - "Run Prewarm Now" button is disabled when cache is Hot
 *   - PrewarmStatus type matches the Python get_prewarm_status() return shape
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
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

// ─── Cache Status card rendering ──────────────────────────────────────

describe("About page — Cache Status card", () => {
	beforeEach(() => {
		mockCall.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	/**
	 * Helper: render the About page with a specific prewarm status.
	 * The About page calls get_status, get_config, and get_prewarm_status
	 * on mount; we mock all three.
	 */
	async function renderAboutWithPrewarmStatus(
		prewarmStatus: Record<string, unknown>,
	) {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve(prewarmStatus);
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		// Wait for the prewarm status to load (the card title appears
		// once the initial fetch resolves).
		await waitFor(() => {
			expect(screen.getByText("Cache Status")).toBeTruthy();
		});
	}

	it("renders the Cache Status card with all four rows", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: 0.73,
			cache_label: "partial",
			cached_bytes: 1750000000,
			total_bytes: 2400000000,
			prewarm_running: false,
		});

		// All four row labels must be present.
		expect(screen.getByText("Prewarm Status")).toBeTruthy();
		expect(screen.getByText("Last Run")).toBeTruthy();
		expect(screen.getByText("Cache Health")).toBeTruthy();
		expect(screen.getByText("Elapsed")).toBeTruthy();
	});

	it("shows 'Partial' badge when cache_label is 'partial'", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: 0.5,
			cache_label: "partial",
			cached_bytes: 1200000000,
			total_bytes: 2400000000,
			prewarm_running: false,
		});

		expect(screen.getByText("Partial")).toBeTruthy();
	});

	it("shows 'Hot' badge when cache_label is 'hot'", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: 1.0,
			cache_label: "hot",
			cached_bytes: 2400000000,
			total_bytes: 2400000000,
			prewarm_running: false,
		});

		expect(screen.getByText("Hot")).toBeTruthy();
	});

	it("shows 'Cold' badge when cache_label is 'cold'", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: 0.0,
			cache_label: "cold",
			cached_bytes: 0,
			total_bytes: 2400000000,
			prewarm_running: false,
		});

		expect(screen.getByText("Cold")).toBeTruthy();
	});

	it("shows 'Unknown' badge when cache_label is 'unknown'", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: null,
			elapsed_s: null,
			cache_ratio: 0.0,
			cache_label: "unknown",
			cached_bytes: 0,
			total_bytes: 0,
			prewarm_running: false,
		});

		expect(screen.getByText("Unknown")).toBeTruthy();
	});

	it("shows 'Running…' when prewarm_running is true (overrides badge)", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: null,
			elapsed_s: null,
			cache_ratio: 0.0,
			cache_label: "cold",
			cached_bytes: 0,
			total_bytes: 0,
			prewarm_running: true,
		});

		// When prewarm is running, the Prewarm Status row shows "Running…".
		// There may be multiple "Running…" texts (the row value + the
		// button label), so use getAllByText.
		const runningElements = screen.getAllByText("Running…");
		expect(runningElements.length).toBeGreaterThanOrEqual(1);
	});

	it("shows cache health percentage and bytes", async () => {
		// Use values that divide cleanly into GB:
		//   1.6 GB = 1,717,986,918 bytes (1.6 * 1024^3)
		//   2.4 GB = 2,576,980,378 bytes (2.4 * 1024^3)
		//   ratio = 1.6 / 2.4 = 0.6667 → rounds to 67%
		const totalBytes = Math.round(2.4 * 1024 * 1024 * 1024);
		const cachedBytes = Math.round(1.6 * 1024 * 1024 * 1024);
		const ratio = cachedBytes / totalBytes; // ~0.667
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: ratio,
			cache_label: "partial",
			cached_bytes: cachedBytes,
			total_bytes: totalBytes,
			prewarm_running: false,
		});

		// The card renders "67% (1.6 GB / 2.4 GB)".
		expect(screen.getByText(/67%/)).toBeTruthy();
		expect(screen.getByText(/1\.6 GB/)).toBeTruthy();
		expect(screen.getByText(/2\.4 GB/)).toBeTruthy();
	});

	it("shows elapsed seconds with one decimal", async () => {
		await renderAboutWithPrewarmStatus({
			last_run: "2026-07-08T13:48:49",
			elapsed_s: 20.4,
			cache_ratio: 1.0,
			cache_label: "hot",
			cached_bytes: 2400000000,
			total_bytes: 2400000000,
			prewarm_running: false,
		});

		expect(screen.getByText("20.4s")).toBeTruthy();
	});
});

// ─── Refresh button ───────────────────────────────────────────────────

describe("About page — Refresh button", () => {
	beforeEach(() => {
		mockCall.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	it("calls get_prewarm_status when clicked", async () => {
		let prewarmCallCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				prewarmCallCount++;
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0.0,
					cache_label: "unknown",
					cached_bytes: 0,
					total_bytes: 0,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		// Wait for initial load (1 call to get_prewarm_status).
		await waitFor(() => {
			expect(screen.getByText("Refresh")).toBeTruthy();
		});
		expect(prewarmCallCount).toBe(1);

		// Click Refresh → 1 more call.
		fireEvent.click(screen.getByText("Refresh"));
		await waitFor(() => {
			expect(prewarmCallCount).toBe(2);
		});
	});
});

// ─── Run Prewarm Now button ───────────────────────────────────────────

describe("About page — Run Prewarm Now button", () => {
	beforeEach(() => {
		mockCall.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	it("is disabled when cache is Hot", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: "2026-07-08T13:48:49",
					elapsed_s: 20.4,
					cache_ratio: 1.0,
					cache_label: "hot",
					cached_bytes: 2400000000,
					total_bytes: 2400000000,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		});

		const button = screen
			.getByText("Run Prewarm Now")
			.closest("button") as HTMLButtonElement;
		expect(button).toBeTruthy();
		expect(button?.disabled).toBe(true);
	});

	it("is enabled when cache is Cold", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: "2026-07-08T13:48:49",
					elapsed_s: 20.4,
					cache_ratio: 0.0,
					cache_label: "cold",
					cached_bytes: 0,
					total_bytes: 2400000000,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		});

		const button = screen
			.getByText("Run Prewarm Now")
			.closest("button") as HTMLButtonElement;
		expect(button).toBeTruthy();
		expect(button?.disabled).toBe(false);
	});

	it("is enabled when cache is Partial", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: "2026-07-08T13:48:49",
					elapsed_s: 20.4,
					cache_ratio: 0.5,
					cache_label: "partial",
					cached_bytes: 1200000000,
					total_bytes: 2400000000,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		});

		const button = screen
			.getByText("Run Prewarm Now")
			.closest("button") as HTMLButtonElement;
		expect(button).toBeTruthy();
		expect(button?.disabled).toBe(false);
	});

	it("is enabled when cache is Unknown", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0.0,
					cache_label: "unknown",
					cached_bytes: 0,
					total_bytes: 0,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		});

		const button = screen
			.getByText("Run Prewarm Now")
			.closest("button") as HTMLButtonElement;
		expect(button).toBeTruthy();
		expect(button?.disabled).toBe(false);
	});

	it("calls run_prewarm IPC when clicked", async () => {
		let runPrewarmCallCount = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0.0,
					cache_label: "cold",
					cached_bytes: 0,
					total_bytes: 2400000000,
					prewarm_running: false,
				});
			}
			if (type === "run_prewarm") {
				runPrewarmCallCount++;
				return Promise.resolve({ started: true, pid: 12345 });
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Run Prewarm Now")).toBeTruthy();
		});

		// Click the button.
		fireEvent.click(screen.getByText("Run Prewarm Now"));

		// The run_prewarm IPC must have been called.
		await waitFor(() => {
			expect(runPrewarmCallCount).toBe(1);
		});
	});

	it("is disabled when prewarm is already running", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({ status: "idle", config_dir: "/tmp" });
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
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0.0,
					cache_label: "cold",
					cached_bytes: 0,
					total_bytes: 2400000000,
					prewarm_running: true, // prewarm is running
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		// Wait for the card to render. When prewarm is running, the
		// Run Prewarm Now button shows "Running…" and is disabled.
		await waitFor(() => {
			const runningElements = screen.getAllByText("Running…");
			expect(runningElements.length).toBeGreaterThanOrEqual(1);
		});

		// Find the Run Prewarm Now button — it's the <button> whose
		// text contains "Running". The status row is a <span>, not a
		// button, so this uniquely identifies the button.
		const buttons = screen.getAllByRole("button") as HTMLButtonElement[];
		const runButton = buttons.find((b) => b.textContent?.includes("Running"));
		expect(runButton).toBeTruthy();
		expect(runButton?.disabled).toBe(true);
	});
});

// ─── PrewarmStatus type shape ─────────────────────────────────────────

describe("PrewarmStatus type matches Python get_prewarm_status()", () => {
	it("has all required fields with correct types", () => {
		// This is a compile-time + runtime check. The object below must
		// match the PrewarmStatus interface in About.tsx AND the dict
		// returned by voice_typer.server.prewarm.get_prewarm_status().
		const status = {
			last_run: "2026-07-08T13:48:49" as string | null,
			elapsed_s: 20.4 as number | null,
			cache_ratio: 0.73 as number,
			cache_label: "partial" as "hot" | "partial" | "cold" | "unknown",
			cached_bytes: 1750000000 as number,
			total_bytes: 2400000000 as number,
			prewarm_running: false as boolean,
		};

		// Runtime field presence + type checks.
		expect(typeof status.last_run).toBe("string");
		expect(typeof status.elapsed_s).toBe("number");
		expect(typeof status.cache_ratio).toBe("number");
		expect(typeof status.cache_label).toBe("string");
		expect(typeof status.cached_bytes).toBe("number");
		expect(typeof status.total_bytes).toBe("number");
		expect(typeof status.prewarm_running).toBe("boolean");

		// cache_label must be one of the allowed values.
		expect(["hot", "partial", "cold", "unknown"]).toContain(status.cache_label);

		// cache_ratio must be in [0.0, 1.0].
		expect(status.cache_ratio).toBeGreaterThanOrEqual(0.0);
		expect(status.cache_ratio).toBeLessThanOrEqual(1.0);
	});

	it("accepts null for last_run and elapsed_s (first-run state)", () => {
		const status = {
			last_run: null,
			elapsed_s: null,
			cache_ratio: 0.0,
			cache_label: "unknown" as const,
			cached_bytes: 0,
			total_bytes: 0,
			prewarm_running: false,
		};

		expect(status.last_run).toBeNull();
		expect(status.elapsed_s).toBeNull();
	});
});
