/**
 *  vitest rewrite — behavioral tests for `App.tsx` help overlay.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_ux_components.py`:
 *   - TestAppHasHelpOverlayForShortcuts::test_app_has_question_mark_keydown_handler
 *   - TestAppHasHelpOverlayForShortcuts::test_help_overlay_closes_on_escape
 *
 * The Python tests asserted on substring presence inside `App.tsx`
 * (e.g. `'"?" === e.key' in app`, `'"Escape" in app'`,
 * `"setShowHelpOverlay(false)" in app`).  These pass even when the
 * handler is wired to the wrong element, and they fail on innocent
 * refactors (switching from `e.key === "?"` to `e.code === "Slash"
 * && e.shiftKey`).  The vitest versions below mount the real App
 * component, dispatch a realistic `?` keydown to `document`, and
 * assert the help overlay Modal opens; then dispatch Escape and
 * assert it closes.
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state hoisted before vi.mock factories run ─────────────────
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
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
		recordingState: "idle" as const,
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

// Real Modal is used so the open/close state is observable via the
// "Keyboard Shortcuts" heading text.  Modal renders children only when
// `open` is true (and uses Radix Dialog under the hood); jsdom supports
// it well enough for this open/close assertion.
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

// Mock all child pages as trivial stubs so App's renderPage() switch
// doesn't pull in the full dependency tree.
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

function dispatchKey(
	key: string,
	opts: { ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {},
) {
	// App.tsx attaches the "?" / Escape handler to `document`.
	fireEvent.keyDown(document, {
		key,
		ctrlKey: opts.ctrlKey ?? false,
		metaKey: opts.metaKey ?? false,
		altKey: opts.altKey ?? false,
	});
}

describe("App help overlay — RW-0 rewrite of test_app_has_question_mark_keydown_handler", () => {
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

	it("opens the help overlay when '?' is pressed", async () => {
		const { default: App } = await import("@/App");
		renderWithProviders(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Help overlay is closed initially.
		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		dispatchKey("?");

		// The help overlay Modal opens and renders the
		// "Keyboard Shortcuts" heading (i18n key help.title).
		await waitFor(() => {
			expect(
				screen.getAllByText("Keyboard Shortcuts").length,
			).toBeGreaterThanOrEqual(1);
		});
	});

	it("does NOT open the help overlay when '?' is pressed with Ctrl held", async () => {
		// App.tsx explicitly ignores '?' when Ctrl/Cmd/Alt is held so
		// keyboard shortcuts like Ctrl+Shift+? (DevTools) don't pop
		// the help overlay.
		const { default: App } = await import("@/App");
		renderWithProviders(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		dispatchKey("?", { ctrlKey: true });

		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();
	});

	it("does NOT open the help overlay when focus is in an input", async () => {
		const { default: App } = await import("@/App");
		renderWithProviders(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Simulate focus moving into an <input> (e.g. SearchField).
		const input = document.createElement("input");
		document.body.appendChild(input);
		input.focus();
		dispatchKey("?");
		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();
		document.body.removeChild(input);
	});
});

describe("App help overlay — RW-0 rewrite of test_help_overlay_closes_on_escape", () => {
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

	it("closes the help overlay when Escape is pressed", async () => {
		const { default: App } = await import("@/App");
		renderWithProviders(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Open the overlay first.
		dispatchKey("?");
		await waitFor(() => {
			expect(
				screen.getAllByText("Keyboard Shortcuts").length,
			).toBeGreaterThanOrEqual(1);
		});

		// Press Escape — App.tsx's handler closes the overlay
		// (separate from Radix Modal's own Escape handler).
		dispatchKey("Escape");

		await waitFor(() => {
			expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();
		});
	});
});
