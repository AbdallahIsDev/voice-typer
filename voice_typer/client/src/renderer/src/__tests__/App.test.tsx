/**
 * Integration tests for the App shell — D1-FIX (b-review Finding 1).
 *
 * Scenario under test: the "Re-run setup wizard" button in Settings calls
 * `updateConfig({ onboarding_completed: false })` then
 * `onNavigate("onboarding")`.  Before the D1 fix, `updateConfig` only
 * updated Settings.tsx's LOCAL config state and queued a backend `set_config`
 * IPC — it did NOT touch the Zustand `appStore.config` snapshot that
 * App.tsx's route guard reads:
 *
 *   // App.tsx:42-46
 *   useEffect(() => {
 *     if (currentPage === "onboarding" && config?.onboarding_completed === true) {
 *       navigate("home");  // ← bounces the user back to home
 *     }
 *   }, [currentPage, config, navigate]);
 *
 * Because the appStore only learned about the change later (via the async
 * `config_changed` push event handled in useTheme.ts), the route guard fired
 * on the very next render, saw the stale `true` value, and bounced the user
 * back to home — the onboarding wizard was never shown.
 *
 * The D1 fix calls `useAppStore.getState().mergeConfig(updates)` synchronously
 * inside `updateConfig` so the route guard sees `onboarding_completed: false`
 * immediately.  The unit test in pages/__tests__/Settings.test.tsx verifies
 * Settings.tsx actually calls `mergeConfig`; this integration test verifies
 * that App.tsx's route guard cooperates: when the wizard button is clicked,
 * the Onboarding page is shown (not bounced back to Home).
 *
 * To keep the integration test focused on the App-level routing behaviour,
 * all child pages are mocked as trivial stubs.  The Settings stub simulates
 * the real SettingsPage's post-fix wizard-button behaviour: it calls
 * `useAppStore.getState().mergeConfig({ onboarding_completed: false })` then
 * `props.onNavigate?.("onboarding")` — exactly what the real page does after
 * the D1 fix.
 */
import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state hoisted before vi.mock factories run ─────────────────────
// `store` holds a late-bound reference to the real `useAppStore` so the
// mocked Settings page (whose vi.mock factory is hoisted ABOVE the test
// file's top-level imports) can call `mergeConfig` without referencing a
// not-yet-initialised top-level import.
const { mockCall, mockPythonEvent, store } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	store: {} as {
		useAppStore?: typeof import("@/stores/appStore")["useAppStore"];
	},
}));

// ── Mock the Python bridge + event hook ─────────────────────────────────
const stable = vi.hoisted(() => ({
	handleRetryConnection: vi.fn(),
	handleThemeChange: vi.fn(),
	reloadThemeFromConfig: vi.fn(),
	setTextSize: vi.fn(),
	toastError: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

// ── Mock App-level hooks that talk to the backend ──────────────────────
// useConnection: return "connected" so App renders the active page (rather
// than the connecting/restarting/disconnected spinner).
vi.mock("@/hooks/useConnection", () => ({
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: stable.handleRetryConnection,
	}),
}));

// useTheme: stub all returned values so App doesn't make real IPC calls.
vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: stable.handleThemeChange,
		reloadThemeFromConfig: stable.reloadThemeFromConfig,
		textSize: 14,
		setTextSize: stable.setTextSize,
	}),
}));

// useSoundFeedback: no-op (App mounts it at the root for cue playback).
vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

// ── Mock window-chrome components so App renders without the bridge ────
vi.mock("@/components/layout/Sidebar", () => ({
	Sidebar: ({
		onNavigate,
	}: {
		onNavigate: (page: string) => void;
		currentPage: string;
	}) => (
		<nav data-testid="sidebar">
			<button type="button" onClick={() => onNavigate("settings")}>
				Go to Settings
			</button>
			<button type="button" onClick={() => onNavigate("home")}>
				Go to Home
			</button>
			{/* Test-only escape hatch: forces navigation to the
                            onboarding page WITHOUT first flipping
                            onboarding_completed to false.  Used by the
                            regression test to verify the route guard still
                            bounces when the appStore holds the stale true
                            value (i.e. the D1 fix is a SYNC fix, not a guard
                            removal). */}
			<button type="button" onClick={() => onNavigate("onboarding")}>
				Force Nav To Onboarding
			</button>
		</nav>
	),
}));

vi.mock("@/components/layout/TitleBar", () => ({
	TitleBar: () => <div data-testid="titlebar" />,
}));

// ErrorBoundary: passthrough wrapper so a render error in a child doesn't
// trigger the recovery UI (which would obscure the page text we assert on).
vi.mock("@/components/feedback/ErrorBoundary", () => ({
	ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
		<>{children}</>
	),
}));

// Modal: minimal open/closed renderer (App renders a help overlay via Modal).
vi.mock("@/components/common/Modal", () => ({
	Modal: ({ children, open }: { children: React.ReactNode; open: boolean }) =>
		open ? <div data-testid="modal">{children}</div> : null,
	ModalFooter: ({ children }: { children: React.ReactNode }) => (
		<div>{children}</div>
	),
}));

// Toaster: sonner's portal — stubbed to null so it doesn't render to jsdom.
vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

// ── Mock icon libraries (used transitively by Button, Modal, etc.) ─────
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// sonner + next-themes: pulled in transitively via useSnackbar / ui/sonner.
vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: stable.toastError,
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// ── Mock page components as trivial stubs ──────────────────────────────
// Each stub renders a unique text label so the test can assert which page
// is currently mounted by App's router.  The Onboarding stub also exposes
// an "Complete" button wired to the onComplete prop so we can verify the
// post-wizard navigation path too.
vi.mock("@/pages/Home", () => ({
	default: () => <div data-testid="home-page">Home page</div>,
}));
vi.mock("@/pages/History", () => ({
	default: () => <div data-testid="history-page">History page</div>,
}));
vi.mock("@/pages/Templates", () => ({
	default: () => <div data-testid="templates-page">Templates page</div>,
}));
vi.mock("@/pages/Vocabulary", () => ({
	default: () => <div data-testid="vocabulary-page">Vocabulary page</div>,
}));
vi.mock("@/pages/Models", () => ({
	default: () => <div data-testid="models-page">Models page</div>,
}));
vi.mock("@/pages/Microphone", () => ({
	default: () => <div data-testid="microphone-page">Microphone page</div>,
}));
vi.mock("@/pages/About", () => ({
	default: () => <div data-testid="about-page">About page</div>,
}));
vi.mock("@/pages/Privacy", () => ({
	default: () => <div data-testid="privacy-page">Privacy page</div>,
}));
vi.mock("@/pages/Dashboard", () => ({
	default: () => <div data-testid="dashboard-page">Analytics page</div>,
}));
vi.mock("@/pages/Onboarding", () => ({
	default: ({ onComplete }: { onComplete?: () => void }) => (
		<div data-testid="onboarding-page">
			Onboarding wizard page
			<button type="button" onClick={onComplete}>
				Complete
			</button>
		</div>
	),
}));

// The Settings stub simulates the real SettingsPage's post-D1-fix wizard
// button behaviour: it synchronously calls `mergeConfig({ onboarding_completed:
// false })` on the appStore, then fires `onNavigate("onboarding")`.  The
// unit test in pages/__tests__/Settings.test.tsx verifies the REAL
// SettingsPage does this; this stub lets the App integration test focus on
// the route guard's behaviour without re-mocking the entire Settings render
// graph (icons, theme pickers, search field, etc.).
vi.mock("@/pages/Settings", () => ({
	default: function MockSettingsPage() {
		// The real SettingsPage navigates via the useNavigation hook
		// internally (App no longer passes an onNavigate prop), so the
		// stub mirrors that: read `navigate` from the real hook.
		const { navigate } = useNavigation();
		return (
			<div data-testid="settings-page">
				Settings page
				<button
					type="button"
					onClick={() => {
						// Mirror the real SettingsPage's post-fix behaviour:
						// synchronously merge into the appStore, THEN navigate.
						store.useAppStore
							?.getState()
							.mergeConfig({ onboarding_completed: false });
						navigate("onboarding");
					}}
				>
					Re-run setup wizard
				</button>
			</div>
		);
	},
}));

import { _resetNavigationForTest, useNavigation } from "@/hooks/useNavigation";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";

// Bind the real useAppStore into the hoisted ref so the mocked Settings
// page's click handler can call `mergeConfig` on the SAME store instance
// the App route guard subscribes to.  Runs AFTER the vi.mock factories
// (which are hoisted above all top-level statements) but BEFORE any test
// executes.
store.useAppStore = useAppStore;

/** A minimal valid config with onboarding_completed=true (the bug scenario). */
const completedConfig: Partial<VoiceTyperConfig> = {
	onboarding_completed: true,
};

describe("App route guard — D1-FIX wizard re-run bounce", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Shared store: reset the module-level nav state so a previous
		// test's navigation can't leak into this one (App is imported
		// dynamically without resetModules in this file).
		_resetNavigationForTest();
		// Reset the appStore to a known state: completed onboarding.
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

	it("navigates to the onboarding page (not home) after clicking Re-run setup wizard", async () => {
		// Seed the appStore with completed onboarding so the route
		// guard WOULD bounce if the wizard button failed to flip it
		// to false synchronously.
		expect(useAppStore.getState().config?.onboarding_completed).toBe(true);

		const { default: App } = await import("@/App");
		render(<App />);

		// App starts on the home page (useNavigation defaults to "home").
		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Navigate to Settings via the mocked Sidebar button.
		fireEvent.click(screen.getByText("Go to Settings"));
		await waitFor(() => {
			expect(screen.getByTestId("settings-page")).toBeTruthy();
		});

		// Click the wizard button — the mocked Settings page calls
		// `mergeConfig({ onboarding_completed: false })` then
		// `onNavigate("onboarding")`, mirroring the real SettingsPage
		// post-fix.
		fireEvent.click(screen.getByText("Re-run setup wizard"));

		// D1-FIX assertion: the onboarding page must be shown, NOT
		// bounced back to home.  Before the fix, the appStore still
		// held `onboarding_completed: true` when onNavigate fired, so
		// App's route guard (App.tsx:42-46) immediately called
		// `navigate("home")` and the onboarding page was never
		// visible.
		await waitFor(() => {
			expect(screen.getByTestId("onboarding-page")).toBeTruthy();
		});
		expect(screen.queryByTestId("home-page")).toBeNull();

		// And the appStore must reflect the new onboarding_completed
		// value (this is the synchronisation the D1 fix added).
		expect(useAppStore.getState().config?.onboarding_completed).toBe(false);
	});

	it("route guard still bounces to home when onboarding_completed remains true", async () => {
		// Regression guard: confirms the route guard logic itself
		// hasn't been disabled.  If we navigate to "onboarding"
		// WITHOUT first flipping onboarding_completed to false, the
		// guard must still bounce the user back to home.  This
		// ensures the D1 fix is the SYNC fix (not a guard removal).
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Click the test-only "Force Nav To Onboarding" button in
		// the mocked Sidebar.  This calls onNavigate("onboarding")
		// WITHOUT first calling mergeConfig — so the appStore still
		// holds onboarding_completed=true and the route guard must
		// bounce back to home.
		fireEvent.click(screen.getByText("Force Nav To Onboarding"));

		// The route guard fires on the next render and bounces the
		// user back to home — the onboarding page is never visible.
		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});
		expect(screen.queryByTestId("onboarding-page")).toBeNull();

		// The appStore must still hold the original true value
		// (nothing flipped it).
		expect(useAppStore.getState().config?.onboarding_completed).toBe(true);
	});
});

describe("App-wide shortcuts — zoom via the mounted App (keydown + wheel)", () => {
	// Same harness as the route-guard suite: mockCall resolves, store is
	// seeded to the connected/idle state, nav state is reset so a
	// previous test's navigation can't leak.
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
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
	});

	async function mountApp() {
		const { default: App } = await import("@/App");
		render(<App />);
		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});
	}

	function dispatchKey(key: string, opts: KeyboardEventInit = {}) {
		window.dispatchEvent(
			new KeyboardEvent("keydown", { bubbles: true, ...opts, key }),
		);
	}

	function dispatchWheel(deltaY: number, opts: WheelEventInit = {}) {
		window.dispatchEvent(
			new WheelEvent("wheel", {
				bubbles: true,
				cancelable: true,
				...opts,
				deltaY,
			}),
		);
	}

	it("Ctrl+= keydown bumps text size and persists via set_config through the mounted App", async () => {
		mockCall.mockResolvedValue({});
		await mountApp();

		dispatchKey("=", { ctrlKey: true });

		await waitFor(() => {
			expect(stable.setTextSize).toHaveBeenCalledWith(15);
		});
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 15 });
	});

	it("Ctrl+wheel (deltaY<0) zooms in and Ctrl+wheel (deltaY>0) zooms out", async () => {
		mockCall.mockResolvedValue({});
		await mountApp();

		dispatchWheel(-100, { ctrlKey: true });
		await waitFor(() => {
			expect(stable.setTextSize).toHaveBeenCalledWith(15);
		});
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 15 });

		// Zoom out: the mocked useTheme keeps textSize at 14, so a
		// deltaY>0 wheel computes 14 → 13 (independent of the earlier
		// zoom-in event).
		dispatchWheel(100, { ctrlKey: true });
		await waitFor(() => {
			expect(stable.setTextSize).toHaveBeenCalledWith(13);
		});
		expect(mockCall).toHaveBeenCalledWith("set_config", { text_size: 13 });
	});

	it("plain wheel without Ctrl does NOT zoom (modifier guard applies)", async () => {
		mockCall.mockResolvedValue({});
		await mountApp();

		dispatchWheel(-100);
		await act(async () => {
			await new Promise((resolve) => setTimeout(resolve, 0));
		});

		expect(stable.setTextSize).not.toHaveBeenCalled();
		expect(mockCall).not.toHaveBeenCalled();
	});

	it("Ctrl+wheel set_config failure stays silent (no toast) through the mounted App", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "set_config")
				return Promise.reject(new Error("backend down"));
			return Promise.resolve({});
		});
		await mountApp();

		dispatchWheel(-100, { ctrlKey: true });
		await waitFor(() => {
			expect(stable.setTextSize).toHaveBeenCalledWith(15);
		});
		// The rejected set_config must be swallowed by the wheel path's
		// silentOnError contract — the loud toast path is key-shortcut-only.
		await act(async () => {
			await new Promise((resolve) => setTimeout(resolve, 0));
		});
		expect(stable.toastError).not.toHaveBeenCalled();
	});
});
