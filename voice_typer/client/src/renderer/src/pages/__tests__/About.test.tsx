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

// ─── formatRelativeTime ───────────────────────────────────────────────

describe("formatRelativeTime", () => {
	it("returns 'Never' for null", () => {
		// Note: t("about.neverRun") returns "Never" in English.
		expect(formatRelativeTime(null)).toBe("Never");
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
});

// ─── About & Privacy page, product identity (merged) ────────────────

describe("About & Privacy page, product identity (merged)", () => {
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

		// Page heading, the About title (i18n key about.title).
		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// Identity row: product tagline under the app name.
		expect(screen.getByText("Desktop voice-to-text")).toBeTruthy();
		// Capability split, Local & Offline vs Cloud blocks.
		expect(screen.getByText("Local & Offline")).toBeTruthy();
		expect(screen.getByText("Cloud (optional)")).toBeTruthy();
	});

	it("renders the Version row with the inline Check for Updates button beside it", async () => {
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
		// The update-check button sits INLINE beside the version (same
		// row, 24px gap) in the compact xs size.
		const checkButton = screen.getByRole("button", {
			name: "Check for Updates",
		});
		expect(checkButton).toBeTruthy();
		expect(checkButton.getAttribute("data-size")).toBe("xs");
		expect(screen.queryByText("Platforms")).toBeNull();
		expect(screen.queryByText("Windows, macOS, and Linux")).toBeNull();
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
		// topic rows, no separate "Privacy" section heading exists.
		expect(screen.queryByRole("heading", { name: "Diagnostics" })).toBeNull();
		expect(screen.queryByRole("heading", { name: "Privacy" })).toBeNull();
		expect(
			screen.queryByRole("heading", { name: "Resources & Feedback" }),
		).toBeNull();
	});

	it("does NOT render the Help section (removed, duplicates `?` overlay)", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// row. After , that row is gone (the help overlay is the
		// canonical source for shortcut labels).
		expect(screen.queryByText("Start / Stop dictation")).toBeNull();
		// The Help section heading itself is also gone.
		expect(screen.queryByText("Help")).toBeNull();
	});

	it("does NOT render the Cache Status section (removed, belongs on a diagnostics surface)", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// button and a "Refresh" button, both removed.
		expect(screen.queryByText("Cache Status")).toBeNull();
		expect(screen.queryByText("Run Prewarm Now")).toBeNull();
		expect(screen.queryByText("View prewarm log")).toBeNull();
	});

	it("keeps ONLY the version meta row, no Platforms / Offline engine pack status rows", async () => {
		const { default: AboutPage } = await import("@/pages/AboutAndPrivacy");
		render(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "About & Privacy" }),
			).toBeTruthy();
		});

		// update check is triggered via the inline button beside the
		// version, and no pack status text is rendered anywhere.
		expect(screen.queryByText("Offline engine pack")).toBeNull();
		expect(screen.queryByText("Not checked yet")).toBeNull();
		expect(
			screen.getByRole("button", { name: "Check for Updates" }),
		).toBeTruthy();
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
					config_dir: "/tmp/lausu",
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
			// The configured model's weights ARE on disk, the
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
			expect(screen.getByText("/tmp/lausu")).toBeTruthy();
		});
		// The row resolves, no "Loading…" placeholder remains.
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
			expect(screen.getByText("/tmp/lausu")).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));

		await waitFor(() => {
			expect(writeText).toHaveBeenCalledTimes(1);
		});
		const text = writeText.mock.calls[0]?.[0] as string;
		// The copied block contains the labeled diagnostic fields.
		expect(text).toContain("App Version: v");
		expect(text).toContain("Backend: Connected");
		expect(text).toContain("Config Directory: /tmp/lausu");
		expect(text).toContain("Speech recognizer: whisper (tiny)");
		// Device renders the friendly display name ("cpu" → "CPU").
		expect(text).toContain("Device: CPU");
		expect(text).toContain("Loaded Via: cpu/int8/tiny.en");
		expect(text).toContain("Hotkey: F2");

		// Confirmation toast.
		expect(toastSuccess).toHaveBeenCalledWith("Copied!", expect.anything());
	});

	it("does NOT render the sticky section nav (removed, page is short enough to scroll)", async () => {
		renderDiag();

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		// navigation landmark remains.
		expect(
			screen.queryByRole("navigation", { name: "About page sections" }),
		).toBeNull();
	});
});

// ─── Diagnostics model-truth (point 10) ────────────────────────────────
// The Diagnostics table's Speech recognizer / Device rows must derive
// from the SAME source of truth as the Analytics page's Current Setup
// cards (lib/utils/models.ts resolveActiveModel), never a per-page
// duplicate check. With no model installed both pages show
// "Not selected"; with one installed both show the real values.
describe("Diagnostics section, model rows share one source of truth with Analytics", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/lausu",
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
		// get_model_status returns {}, the configured "tiny" is NOT on
		// disk, so the config defaults must NOT leak into the table.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/lausu",
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
		// Both model rows report the unselected state, the stale
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
					config_dir: "/tmp/lausu",
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
		// check inline, the two pages can drift again, the whole point
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
