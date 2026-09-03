/**
 * Live-region inventory guards for the data pages (Models / History /
 * Dashboard) — the same contract Home's guard enforces
 * (`pages/__tests__/Home-recording-flow-fixes.test.tsx`: exactly ONE
 * live region, the designed announcer) applied to the data pages'
 * status surfaces.
 *
 * The count selector counts BOTH explicit `aria-live` attributes and
 * implicit live-region roles (`role="status"` / `role="alert"` — the
 * EmptyState announcer and the Models no-model banner use role, not
 * an aria-live attribute). Non-live surfaces are deliberately
 * excluded: `role="img"` spinners (S5-CR-100), `role="timer"`,
 * `role="progressbar"`, `role="tabpanel"`.
 *
 * Per page the guard pins:
 *   - Models  — a SETTLED page (model selected, no download) carries
 *               ZERO live regions: the former active-model summary
 *               banner (the designed announcer) was removed per user
 *               decision — the selected card's button state already
 *               indicates the active model, so a page-level announcer
 *               was redundant duplication. The per-card status /
 *               disk-space badges remain visual spans (they were
 *               `<output aria-live="polite">` — the Home-pill class
 *               of accidental live region; with N cards that was up
 *               to 2N live regions). The only sanctioned live region
 *               on this page is the no-model banner (`role="status"`,
 *               no-model state only) and the download state's
 *               DownloadProgressBar status line.
 *   - History — the ONLY sanctioned page-level live region is the
 *               LastUpdatedIndicator's polite timestamp region
 *               (XA-8-L5); beyond it, a loaded list adds ZERO, and the
 *               empty / load-error states add EXACTLY ONE — the
 *               EmptyState (`role="status"` / `role="alert"`).
 *   - Dashboard — the skeleton is ZERO live regions; the loaded
 *               analytics view (with keyboard permission granted)
 *               carries ONLY the LastUpdatedIndicator's polite region.
 *
 * If future code adds a stray `aria-live` / `role="status"` /
 * `role="alert"` to a badge, spinner wrapper, card, or stat, the
 * count assertion fails at the page where it was introduced.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. This import MUST
// sit above the DownloadProgressBar import — that component pulls in the
// mocked @hugeicons modules, so the factories' bindings must be
// initialized before it evaluates.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	modelsConfigMock,
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";
import { TooltipProvider } from "@/components/ui/tooltip";

/** Every live region in the rendered tree (explicit + implicit roles). */
function liveRegions(): Element[] {
	return Array.from(
		document.querySelectorAll('[aria-live], [role="status"], [role="alert"]'),
	);
}

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock({ pythonPort: 9999 }));
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

// Config shape serving both Dashboard (model/device/language/hotkey)
// and Models (asr_backend, api keys, consents).
const MOCK_CONFIG = modelsConfigMock();

function renderWithProviders(ui: React.ReactElement) {
	return render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);
}

describe("Models page — live-region guard", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("settled page (model selected, no download) has ZERO live regions (banner removed per user decision)", async () => {
		const { default: ModelsPage } = await import("@/pages/Models");
		renderWithProviders(<ModelsPage />);

		await waitFor(
			() => {
				expect(screen.getByRole("heading", { name: /Models/i })).toBeTruthy();
			},
			{ timeout: 3000 },
		);
		// Let the model cards / accordion settle so any per-card badge
		// would be in the DOM.
		await new Promise((resolve) => setTimeout(resolve, 50));

		// The active-model summary banner was REMOVED (the selected card's
		// button state already marks the active model), and a settled page
		// renders no other sanctioned live region: no download in flight,
		// no-model banner only appears when model_size is empty. Any live
		// region here would be the Home-pill class of accidental announcer.
		expect(liveRegions()).toHaveLength(0);
	});

	it("DownloadProgressBar is the ONE live region for the download state", () => {
		renderWithProviders(
			<DownloadProgressBar
				progress={50}
				status="Downloading… 50%"
				isPaused={false}
				downloadedBytes={null}
				totalBytes={null}
				speedBps={null}
				etaSeconds={null}
				onTogglePause={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		const live = liveRegions();
		expect(live).toHaveLength(1);
		expect(live[0]?.getAttribute("aria-live")).toBe("polite");
		expect(live[0]?.tagName).toBe("P");
	});
});

describe("History page — live-region guard", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	const RECORDS = [
		{
			id: 1,
			text: "first transcription",
			timestamp: "2026-08-16T12:00:00Z",
			duration: 2,
			model: "tiny",
			device: "cpu",
			word_count: 3,
			char_count: 20,
			favorite: 0,
			language: "en",
		},
		{
			id: 2,
			text: "second transcription",
			timestamp: "2026-08-16T11:00:00Z",
			duration: 1.5,
			model: "tiny",
			device: "cpu",
			word_count: 2,
			char_count: 21,
			favorite: 0,
			language: "en",
		},
	];

	it("loaded list has ZERO live regions", async () => {
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_history") return Promise.resolve(RECORDS);
			if (cmd === "get_today_stats") {
				return Promise.resolve({
					count: 2,
					chars: 41,
					word_count: 5,
					duration: 3.5,
				});
			}
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		renderWithProviders(<HistoryPage />);

		await waitFor(
			() => {
				expect(screen.getByText("first transcription")).toBeTruthy();
			},
			{ timeout: 3000 },
		);
		// The ONLY live region is the LastUpdatedIndicator's polite
		// timestamp region (XA-8-L5) — the list itself adds none.
		const live = liveRegions();
		expect(live).toHaveLength(1);
		expect(live[0]?.getAttribute("aria-live")).toBe("polite");
	});

	it("empty state has EXACTLY ONE live region — the EmptyState (role=status)", async () => {
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_history") return Promise.resolve([]);
			if (cmd === "get_today_stats") {
				return Promise.resolve({
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				});
			}
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		renderWithProviders(<HistoryPage />);

		await waitFor(
			() => {
				expect(screen.getByText("No dictations yet")).toBeTruthy();
			},
			{ timeout: 3000 },
		);
		const live = liveRegions();
		expect(live).toHaveLength(2);
		// The EmptyState announcer (role=status) + the LastUpdated
		// indicator's polite timestamp region (XA-8-L5) — nothing else.
		expect(live.some((el) => el.getAttribute("role") === "status")).toBe(true);
		expect(live.some((el) => el.getAttribute("aria-live") === "polite")).toBe(
			true,
		);
	});

	it("load-error state has EXACTLY ONE live region — the EmptyState (role=alert)", async () => {
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_history")
				return Promise.reject(new Error("backend unreachable"));
			if (cmd === "get_today_stats") {
				return Promise.resolve({
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				});
			}
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		renderWithProviders(<HistoryPage />);

		await waitFor(
			() => {
				expect(screen.getByText("Failed to load history")).toBeTruthy();
			},
			{ timeout: 3000 },
		);
		const live = liveRegions();
		expect(live).toHaveLength(2);
		// The EmptyState announcer (role=alert) + the LastUpdated
		// indicator's polite timestamp region (XA-8-L5) — nothing else.
		expect(live.some((el) => el.getAttribute("role") === "alert")).toBe(true);
		expect(live.some((el) => el.getAttribute("aria-live") === "polite")).toBe(
			true,
		);
	});
});

describe("Dashboard page — live-region guard", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(MOCK_CONFIG);
			if (cmd === "get_history") {
				return Promise.resolve([
					{
						id: 1,
						text: "a transcription",
						timestamp: "2026-08-16T12:00:00Z",
						duration: 2,
						model: "tiny",
						device: "cpu",
						word_count: 3,
						char_count: 20,
						favorite: 0,
						language: "en",
					},
				]);
			}
			if (cmd === "get_history_count") return Promise.resolve({ count: 1 });
			if (cmd === "get_status") return Promise.resolve({ config_dir: "" });
			if (cmd === "get_correction_usage") {
				return Promise.resolve({ version: 1, entries: {} });
			}
			// KeyboardPermissionBanner probe — granted, so the banner
			// (role=alert) stays out of the tree.
			if (cmd === "onboarding_check_permissions") {
				return Promise.resolve({
					platform: "windows",
					state: "granted",
					needed: false,
					instructions: null,
				});
			}
			return Promise.resolve({});
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("skeleton (first paint, before data) has ZERO live regions", async () => {
		const { default: DashboardPage } = await import("@/pages/Dashboard");
		renderWithProviders(<DashboardPage />);

		// First paint is the skeleton (data is null until the refresh
		// promise resolves) — assert synchronously.
		expect(liveRegions()).toHaveLength(0);

		// Then let the data land and re-assert the settled view below.
		await waitFor(
			() => {
				expect(screen.getByText(/^Total Dictations/)).toBeTruthy();
			},
			{ timeout: 3000 },
		);
	});

	it("loaded analytics view (permission granted) has ZERO live regions", async () => {
		const { default: DashboardPage } = await import("@/pages/Dashboard");
		renderWithProviders(<DashboardPage />);

		await waitFor(
			() => {
				expect(screen.getByText(/^Total Dictations/)).toBeTruthy();
			},
			{ timeout: 3000 },
		);
		// Let the keyboard-permission probe resolve (granted → banner null).
		await new Promise((resolve) => setTimeout(resolve, 50));
		// The ONLY live region is the LastUpdatedIndicator's polite
		// timestamp region (XA-8-L5).
		const live = liveRegions();
		expect(live).toHaveLength(1);
		expect(live[0]?.getAttribute("aria-live")).toBe("polite");
	});
});
