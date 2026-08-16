/**
 *  vitest rewrite — behavioral tests for `App.tsx` accessibility.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_ux_components.py`:
 *   - TestAppHasSkipToMainContentLink::test_app_has_skip_link
 *   - TestAppAnnouncesRecordingStartStopWithAriaLive::test_app_has_aria_live
 *
 * The Python tests asserted on substring presence inside `App.tsx`
 * (e.g. `"a11y.skipToMain" in src`, `"#main-content" in src`,
 * `"aria-live" in src`).  These pass even when the link is rendered
 * with the wrong href or when the aria-live region never announces
 * useful text.  The vitest versions below mount the real App and
 * assert the actual DOM: the skip-link `<a>` exists, points at
 * `#main-content`, and the aria-live region is present.
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state hoisted before vi.mock factories run ─────────────────
const { mockCall, mockPythonEvent, mockRecordingState } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	// PVT-fix #6 (Sub-agent 16): per-test override of `recordingState`
	// so we can drive the App-level aria-live region through every
	// value in the RecordingState union and assert the announced
	// text matches the expected `t(...)` string.
	mockRecordingState: {
		current: "idle" as
			| "idle"
			| "recording"
			| "transcribing"
			| "loading"
			| "cancelling"
			| "error",
	},
}));

const stable = vi.hoisted(() => ({
	handleRetryConnection: vi.fn(),
	handleThemeChange: vi.fn(),
	reloadThemeFromConfig: vi.fn(),
	setTextSize: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: mockRecordingState.current,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: stable.handleRetryConnection,
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: stable.handleThemeChange,
		reloadThemeFromConfig: stable.reloadThemeFromConfig,
		textSize: 14,
		setTextSize: stable.setTextSize,
	}),
}));

vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

vi.mock("@/components/layout/Sidebar", () => ({
	Sidebar: () => <nav data-testid="sidebar" />,
}));

vi.mock("@/components/layout/TitleBar", () => ({
	TitleBar: () => <div data-testid="titlebar" />,
}));

vi.mock("@/components/feedback/ErrorBoundary", () => ({
	ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
		<>{children}</>
	),
}));

vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

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
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

vi.mock("@/pages/Home", () => ({
	default: () => <div data-testid="home-page">Home</div>,
}));
vi.mock("@/pages/History", () => ({
	default: () => <div data-testid="history-page">History</div>,
}));
vi.mock("@/pages/Templates", () => ({
	default: () => <div data-testid="templates-page">Templates</div>,
}));
vi.mock("@/pages/Vocabulary", () => ({
	default: () => <div data-testid="vocabulary-page">Vocabulary</div>,
}));
vi.mock("@/pages/Models", () => ({
	default: () => <div data-testid="models-page">Models</div>,
}));
vi.mock("@/pages/Microphone", () => ({
	default: () => <div data-testid="microphone-page">Microphone</div>,
}));
vi.mock("@/pages/About", () => ({
	default: () => <div data-testid="about-page">About</div>,
}));
vi.mock("@/pages/Dashboard", () => ({
	default: () => <div data-testid="dashboard-page">Analytics</div>,
}));
vi.mock("@/pages/Onboarding", () => ({
	default: () => <div data-testid="onboarding-page">Onboarding</div>,
}));
vi.mock("@/pages/Settings", () => ({
	default: () => <div data-testid="settings-page">Settings</div>,
}));

import { _resetNavigationForTest } from "@/hooks/useNavigation";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";

/** Seed the shared nav store to a known page and re-read it into the store. */
function seedNavPage(page: string): void {
	localStorage.setItem(
		"vt_nav_state",
		JSON.stringify({ page, history: [page], index: 0 }),
	);
	_resetNavigationForTest();
}

const completedConfig: Partial<VoiceTyperConfig> = {
	onboarding_completed: true,
};

describe("App skip-to-main-content link — RW-0 rewrite of test_app_has_skip_link", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: completedConfig,
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a skip link pointing at #main-content", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// The Python invariant was `"a11y.skipToMain" in src
		// and "#main-content" in src`.  Behavioral: the
		// rendered DOM has an <a href="#main-content"> that
		// a keyboard user can tab to.
		const skipLink = screen.getByRole("link", {
			name: /skip to main content/i,
		});
		expect(skipLink.getAttribute("href")).toBe("#main-content");
	});

	it('renders a <main id="main-content"> landmark the skip link targets', async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// The skip-link target must exist in the DOM as a
		// real landmark element, not just as a string in
		// the source.
		const main = document.getElementById("main-content");
		expect(main).toBeTruthy();
		expect(main?.tagName.toLowerCase()).toBe("main");
	});
});

describe("App aria-live region — RW-0 rewrite of test_app_has_aria_live", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		// PVT-fix #6 (Sub-agent 16): reset the per-test
		// recordingState override to "idle" before each test
		// so the previous test's value doesn't leak in.
		mockRecordingState.current = "idle";
		localStorage.clear();
		// Re-read the (now empty) nav storage so a previous test's
		// seeded page can't leak into this one — tests that need a
		// non-Home page seed `vt_nav_state` BEFORE this call.
		_resetNavigationForTest();
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: completedConfig,
		});
	});

	afterEach(() => {
		cleanup();
		// Defensive: reset back to idle so a future describe
		// block that doesn't set the value sees idle.
		mockRecordingState.current = "idle";
	});

	it("renders an aria-live region for recording state announcements", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// The Python invariant was `"aria-live" in src
		// and "a11y.recordingStarted" in src`.  Behavioral:
		// the rendered DOM has a polite aria-live region.
		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
	});

	//PVT-fix #6 (Sub-agent 16): the  test above only asserts
	// the live region EXISTS — it never checks that the announced
	// text actually changes when `recordingState` changes.  The
	// App.tsx live region (see App.tsx:608-636) renders one of six
	// i18n strings depending on the current `recordingState`:
	//
	//   recording     → t("a11y.recordingStarted")  = "Recording started."
	//   transcribing  → t("a11y.transcribingAudio") = "Transcribing audio…"
	//   idle          → t("a11y.ready")             = "Ready."
	//   error         → t("a11y.errorOccurred")     = "Error occurred."
	//   loading       → t("a11y.loadingModel")      = "Loading model…"
	//   cancelling    → t("a11y.cancelling")        = "Cancelling…"
	//
	// Double-announce trim: on the HOME page the coarse
	// transcribing/loading strings are SUPPRESSED — Home's dynamic
	// status line (its single specific live region) already
	// announces "Transcribing… please wait" / "Downloading model…".
	// The suppression is gated on `currentPage === "home"`, so the
	// coarse announcements still fire on every other page (where the
	// dynamic line isn't mounted). The transcribing/loading tests
	// below cover BOTH branches: non-Home keeps the coarse text,
	// Home omits it.
	//
	// Each test below mocks one `recordingState` value, renders
	// App, and asserts the FIRST polite live region's textContent
	// includes the expected translated string.  This catches
	// regressions where the live region exists but renders the
	// wrong string (or no string at all) for a given state — the
	// most common silent failure mode for aria-live regions.
	//
	// The App.tsx live region is the FIRST `[aria-live="polite"]`
	// in document order (Home's `<output aria-live="polite">` is
	// rendered inside the mocked Home stub and so doesn't exist
	// in this test).  We read `liveRegions[0]` accordingly.

	it("announces 'Recording started.' when recordingState is 'recording'", async () => {
		mockRecordingState.current = "recording";
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Recording started.");
	});

	it("announces 'Transcribing audio…' on NON-Home pages (coarse coverage kept)", async () => {
		mockRecordingState.current = "transcribing";
		seedNavPage("settings");
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("settings-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Transcribing audio…");
	});

	it("does NOT double-announce 'Transcribing audio…' on the Home page (dynamic line covers it)", async () => {
		mockRecordingState.current = "transcribing";
		seedNavPage("home");
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").not.toContain(
			"Transcribing audio…",
		);
	});

	it("announces 'Ready.' when recordingState is 'idle'", async () => {
		mockRecordingState.current = "idle";
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Ready.");
	});

	it("announces 'Error occurred.' when recordingState is 'error'", async () => {
		mockRecordingState.current = "error";
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Error occurred.");
	});

	it("announces 'Loading model…' on NON-Home pages (coarse coverage kept)", async () => {
		mockRecordingState.current = "loading";
		seedNavPage("models");
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("models-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Loading model…");
	});

	it("does NOT double-announce 'Loading model…' on the Home page (dynamic line covers it)", async () => {
		mockRecordingState.current = "loading";
		seedNavPage("home");
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").not.toContain("Loading model…");
	});

	it("announces 'Cancelling…' when recordingState is 'cancelling'", async () => {
		mockRecordingState.current = "cancelling";
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		expect(liveRegions[0]?.textContent ?? "").toContain("Cancelling…");
	});
});
