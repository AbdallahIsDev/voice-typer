/**
 * RW-0 vitest rewrite — behavioral tests for `App.tsx` accessibility.
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
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: vi.fn(),
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: vi.fn(),
		reloadThemeFromConfig: vi.fn(),
		textSize: 14,
		setTextSize: vi.fn(),
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Cancel01Icon: make("Cancel01Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		Moon02Icon: make("Moon02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Sun01Icon: make("Sun01Icon"),
	};
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

import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";

const completedConfig: Partial<VoiceTyperConfig> = {
	onboarding_completed: true,
};

describe("App skip-to-main-content link — RW-0 rewrite of test_app_has_skip_link", () => {
	beforeEach(() => {
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
});
