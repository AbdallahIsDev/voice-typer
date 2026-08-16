/**
 * Tests for the About page.
 *
 *  / SET-5: the About page was slimmed down from a 726-line catch-all
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
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, toastSuccess, toastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

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

	it("returns a localized medium-format date for timestamps older than 7 days", () => {
		const tenDaysAgo = new Date(
			Date.now() - 10 * 24 * 60 * 60_000,
		).toISOString();
		const result = formatRelativeTime(tenDaysAgo);
		//>7-day fallback now uses Intl.DateTimeFormat with
		// dateStyle:"medium" (e.g. "Jul 14, 2026" in en) instead of the
		// raw ISO 8601 string. Assert it's a non-empty localized date,
		// NOT the raw ISO and NOT a relative format.
		expect(result).not.toBe(tenDaysAgo);
		expect(result.length).toBeGreaterThan(0);
		// The medium-format date contains the year (4 digits) so the
		// fallback is distinguishable from a relative "N d ago" string.
		expect(result).toMatch(/\d{4}/);
	});

	it("returns the raw string for unparseable input", () => {
		expect(formatRelativeTime("not-a-date")).toBe("not-a-date");
	});
});

//Slimmed-down About page ( / SET-5) ──────────────────────────

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
					model_size: "large-v3-turbo",
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
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		// Privacy section heading.
		expect(screen.getByRole("heading", { name: "Privacy" })).toBeTruthy();

		// Resources section heading.
		expect(
			screen.getByRole("heading", { name: "Resources & Feedback" }),
		).toBeTruthy();
	});

	it("does NOT render the Help section (removed — duplicates `?` overlay)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		// The Help section previously rendered a "Start / Stop dictation"
		//row. After , that row is gone (the help overlay is the
		// canonical source for shortcut labels).
		expect(screen.queryByText("Start / Stop dictation")).toBeNull();
		// The Help section heading itself is also gone.
		expect(screen.queryByText("Help")).toBeNull();
	});

	it("does NOT render the Cache Status section (removed — belongs on a diagnostics surface)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
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
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
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
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		// The slimmed-down About page no longer fetches prewarm status —
		// only get_status + get_config are called on mount.
		const prewarmCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_prewarm_status",
		);
		expect(prewarmCalls.length).toBe(0);
	});
});

// ─── Config directory + copy diagnostics + layout ──────────────────────

describe("About page — config dir, copy diagnostics, privacy cards, section nav", () => {
	beforeEach(() => {
		mockCall.mockReset();
		toastSuccess.mockClear();
		toastError.mockClear();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/voice-typer",
					loaded_via: "cpu/int8/tiny.en",
				});
			}
			if (type === "get_config") {
				return Promise.resolve({
					asr_backend: "whisper",
					model_size: "tiny",
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
		// Restore a real clipboard for other suites.
		// @ts-expect-error delete restores the jsdom default
		delete navigator.clipboard;
	});

	it("resolves the Config Directory row to the path from get_status (no permanent Loading…)", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("/tmp/voice-typer")).toBeTruthy();
		});
		// The row resolves — no "Loading…" placeholder remains.
		expect(screen.queryByText("Loading…")).toBeNull();
	});

	it("renders the Loaded Via row with a live status dot when get_status reports it", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("cpu/int8/tiny.en")).toBeTruthy();
		});
		expect(screen.getByText("Loaded Via")).toBeTruthy();
		// The hint explains what the field means (point 2 of the
		// simplification request).
		expect(screen.getByText(/How the speech model was loaded/)).toBeTruthy();
	});

	it("copies formatted diagnostics to the clipboard and confirms with a toast", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("/tmp/voice-typer")).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));

		await waitFor(() => {
			expect(writeText).toHaveBeenCalledTimes(1);
		});
		const text = writeText.mock.calls[0]?.[0] as string;
		// The copied block contains the labeled diagnostic fields.
		expect(text).toContain("App Version: v");
		expect(text).toContain("Python Backend: Connected");
		expect(text).toContain("Config Directory: /tmp/voice-typer");
		expect(text).toContain("Speech recognizer: whisper (tiny)");
		expect(text).toContain("Device: cpu");
		expect(text).toContain("Loaded Via: cpu/int8/tiny.en");
		expect(text).toContain("Hotkey: F2");

		// Confirmation toast.
		expect(toastSuccess).toHaveBeenCalledWith("Copied!", expect.anything());
	});

	it("renders the five privacy topic cards with their existing copy", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Privacy" })).toBeTruthy();
		});

		// Five topic blocks — titles + unchanged descriptions.
		expect(screen.getByText("Audio processing.")).toBeTruthy();
		expect(screen.getByText("Model weights.")).toBeTruthy();
		expect(screen.getByText("Cloud speech recognition.")).toBeTruthy();
		expect(screen.getByText("Voice biometrics.")).toBeTruthy();
		expect(screen.getByText("Local data.")).toBeTruthy();
		expect(screen.getByText(/processes all audio locally/)).toBeTruthy();
		expect(screen.getByText(/downloaded from HuggingFace/)).toBeTruthy();
	});

	it("renders the sticky in-page section nav with anchors for all five sections", async () => {
		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		const nav = screen.getByRole("navigation", {
			name: "About page sections",
		});
		for (const label of [
			"About",
			"Diagnostics",
			"Privacy",
			"Resources & Feedback",
			"Credits & Licenses",
		]) {
			expect(within(nav).getByText(label)).toBeTruthy();
		}
	});
});
