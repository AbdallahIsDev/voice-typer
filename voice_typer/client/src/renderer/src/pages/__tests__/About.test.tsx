/**
 * Tests for the merged About & Privacy page (product identity +
 * data-handling disclosure) plus the moved-out surfaces it used to
 * host:
 *
 *   - formatBytes() / formatRelativeTime() — now exported from the
 *     DiagnosticsSettingsSection component (they moved there with the
 *     diagnostics table in the IA split).
 *   - The diagnostics table itself (config dir, Loaded Via, Copy
 *     diagnostics, model-truth rows) — now lives in Settings →
 *     Privacy (support area), covered by mounting
 *     DiagnosticsSettingsSection directly.
 *   - The privacy disclosure — lives on the SAME page now (the About
 *     and Privacy pages were merged into AboutAndPrivacy); covered by
 *     the a11y-rewrite/About-privacy.test.tsx suite.
 *   - Product-identity smoke tests + the negative tests that Help /
 *     Cache Status / Updates are gone from the identity card.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
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

import {
	DiagnosticsSettingsSection,
	formatBytes,
	formatRelativeTime,
} from "@/components/settings/DiagnosticsSettingsSection";

// Static sources for the shared model-truth assertions (same
// readFileSync pattern as Dashboard.test.tsx).
const fs = require("node:fs");
const nodePath = require("node:path");

const DIAG_SRC = fs.readFileSync(
	nodePath.resolve(
		__dirname,
		"..",
		"..",
		"components",
		"settings",
		"DiagnosticsSettingsSection.tsx",
	),
	"utf8",
);
const DASH_DATA_SRC = fs.readFileSync(
	nodePath.resolve(
		__dirname,
		"..",
		"dashboard",
		"hooks",
		"useDashboardData.ts",
	),
	"utf8",
);

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
		// >7-day fallback now uses Intl.DateTimeFormat with
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

// ─── About & Privacy page — product identity (merged) ────────────────

describe("About & Privacy page — product identity (merged)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation(() => Promise.resolve({}));
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the product identity: name, tagline, description, capabilities", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		// Page heading — the About title (i18n key about.title).
		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// Identity row: product tagline under the app name.
		expect(screen.getByText("Desktop voice-to-text")).toBeTruthy();
		// Capability split — Local & Offline vs Cloud blocks.
		expect(screen.getByText("Local & Offline")).toBeTruthy();
		expect(screen.getByText("Cloud (optional)")).toBeTruthy();
	});

	it("renders the Version and Platforms rows", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// Version row (value is v{version} from package.json).
		expect(screen.getByText("Version")).toBeTruthy();
		expect(screen.getByText(/^v\d+\.\d+\.\d+/)).toBeTruthy();
		// Platforms row — the cross-platform claim.
		expect(screen.getByText("Platforms")).toBeTruthy();
		expect(screen.getByText("Windows, macOS, and Linux")).toBeTruthy();
	});

	it("does NOT render Diagnostics or Resources sections (moved out in the IA split)", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// The diagnostics table moved to Settings → Privacy (support
		// area) and the resources grid to Settings → Privacy. The
		// privacy disclosure lives on this page (merged), but as plain
		// topic rows — no separate "Privacy" section heading exists.
		expect(screen.queryByRole("heading", { name: "Diagnostics" })).toBeNull();
		expect(screen.queryByRole("heading", { name: "Privacy" })).toBeNull();
		expect(
			screen.queryByRole("heading", { name: "Resources & Feedback" }),
		).toBeNull();
	});

	it("does NOT render the Help section (removed — duplicates `?` overlay)", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// The Help section previously rendered a "Start / Stop dictation"
		// row. After , that row is gone (the help overlay is the
		// canonical source for shortcut labels).
		expect(screen.queryByText("Start / Stop dictation")).toBeNull();
		// The Help section heading itself is also gone.
		expect(screen.queryByText("Help")).toBeNull();
	});

	it("does NOT render the Cache Status section (removed — belongs on a diagnostics surface)", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// The Cache Status card previously had a "Run Prewarm Now"
		// button and a "Refresh" button — both removed.
		expect(screen.queryByText("Cache Status")).toBeNull();
		expect(screen.queryByText("Run Prewarm Now")).toBeNull();
		expect(screen.queryByText("View prewarm log")).toBeNull();
	});

	it("renders the runtime-pack row with a manual Check for Updates control", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// The Updates section was removed in the IA split, then
		// intentionally RESTORED as a compact runtime-pack status row +
		// user-triggerable check: the pack auto-update path only checks
		// silently on network-online transitions, so users had no way
		// to see pack currency or force a check. This pin guards the
		// restored surface against future removal.
		expect(screen.getByText("Offline engine pack")).toBeTruthy();
		expect(screen.getByText("Check for Updates")).toBeTruthy();
	});
});

// ─── Diagnostics section (moved: About → Settings) ─────────────────────

describe("Diagnostics section (IA split: Settings → Privacy)", () => {
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
			// The configured model's weights ARE on disk — the
			// Diagnostics rows surface the real selection.
			if (type === "get_model_status") {
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
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

	const renderDiag = () =>
		render(<DiagnosticsSettingsSection isVisible={() => true} />);

	it("resolves the Config Directory row to the path from get_status (no permanent Loading…)", async () => {
		renderDiag();

		await waitFor(() => {
			expect(screen.getByText("/tmp/voice-typer")).toBeTruthy();
		});
		// The row resolves — no "Loading…" placeholder remains.
		expect(screen.queryByText("Loading…")).toBeNull();
	});

	it("renders the Loaded Via row with a live status dot when get_status reports it", async () => {
		renderDiag();

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

		renderDiag();

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
		expect(text).toContain("Backend: Connected");
		expect(text).toContain("Config Directory: /tmp/voice-typer");
		expect(text).toContain("Speech recognizer: whisper (tiny)");
		// Device renders the friendly display name ("cpu" → "CPU").
		expect(text).toContain("Device: CPU");
		expect(text).toContain("Loaded Via: cpu/int8/tiny.en");
		expect(text).toContain("Hotkey: F2");

		// Confirmation toast.
		expect(toastSuccess).toHaveBeenCalledWith("Copied!", expect.anything());
	});

	it("does NOT render the sticky section nav (removed — page is short enough to scroll)", async () => {
		renderDiag();

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		// The in-page section nav was removed entirely — no
		// navigation landmark remains.
		expect(
			screen.queryByRole("navigation", { name: "About page sections" }),
		).toBeNull();
	});
});

// ─── Diagnostics model-truth (point 10) ────────────────────────────────
// The Diagnostics table's Speech recognizer / Device rows must derive
// from the SAME source of truth as the Analytics page's Current Setup
// cards (lib/utils/models.ts resolveActiveModel) — never a per-page
// duplicate check. With no model installed both pages show
// "Not selected"; with one installed both show the real values.
describe("Diagnostics section — model rows share one source of truth with Analytics", () => {
	beforeEach(() => {
		mockCall.mockReset();
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
			// get_model_status is set per-test below.
			return Promise.resolve({});
		});
	});

	afterEach(() => {
		cleanup();
	});

	const renderDiag = () =>
		render(<DiagnosticsSettingsSection isVisible={() => true} />);

	it("shows 'Not selected' for Speech recognizer and Device when no model is installed", async () => {
		// get_model_status returns {} — the configured "tiny" is NOT on
		// disk, so the config defaults must NOT leak into the table.
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
					device: "cuda",
					hotkey: "F2",
					microphone: null,
				});
			}
			return Promise.resolve({});
		});

		renderDiag();

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});
		// Both model rows report the unselected state — the stale
		// "whisper (tiny)" / "GPU" values from the config defaults
		// never render (the pre-fix bug this round was reported for).
		expect(screen.getAllByText("Not selected")).toHaveLength(2);
		expect(screen.queryByText(/whisper \(tiny\)/)).toBeNull();
		expect(screen.queryByText("GPU")).toBeNull();
	});

	it("shows the real model + device when get_model_status confirms the weights are on disk", async () => {
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
			if (type === "get_model_status") {
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
				});
			}
			return Promise.resolve({});
		});

		renderDiag();

		await waitFor(() => {
			expect(screen.getByText("whisper (tiny)")).toBeTruthy();
		});
		expect(screen.getByText("CPU")).toBeTruthy();
	});

	it("Diagnostics and the Analytics data hook both import resolveActiveModel from lib/utils/models (one shared check)", () => {
		// If either page ever re-implements its own "is it installed"
		// check inline, the two pages can drift again — the whole point
		// of this round's fix. Both must route through the shared
		// helper in lib/utils/models.ts.
		expect(DIAG_SRC).toMatch(
			/import \{ resolveActiveModel \} from "@\/lib\/utils\/models"/,
		);
		expect(DASH_DATA_SRC).toMatch(
			/import \{ resolveActiveModel \} from "@\/lib\/utils\/models"/,
		);
	});
});
