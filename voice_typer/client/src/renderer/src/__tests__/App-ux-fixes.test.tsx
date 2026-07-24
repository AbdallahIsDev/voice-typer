/**
 * FIX-14 — UX-19, UX-24, UX-25 regression tests for App.tsx.
 *
 * UX-19: "Page not found" fallback uses i18n keys + renders a "Go to Home"
 *        recovery button that navigates home on click. The previous version
 *        hardcoded English strings and offered no recovery action.
 *
 * UX-24: The `?` help overlay shows the user's ACTUAL configured hotkey
 *        (via formatHotkeyLabel) rather than a hardcoded "Caps Lock" label
 *        that lied whenever the user had rebound the key.
 *
 * UX-25: The `?` keydown guard now skips when focus is in a contentEditable
 *        element (rich-text editors, Slate/ProseMirror, etc.). Previously
 *        only <input>/<textarea>/<select> were checked, so typing "?"
 *        inside a contentEditable field popped the overlay and stole focus.
 *
 * SET-4: The `?` keydown listener is registered ONCE (empty deps) and reads
 *        `showHelpOverlay` from a ref. This is implicitly covered by the
 *        UX-25 test (the listener must still be alive across opens/closes
 *        for the test to pass) but isn't directly asserted here.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state hoisted before vi.mock factories run ─────────────────
const { mockCall, mockPythonEvent, mockNavigate } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
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
	Sidebar: ({
		onNavigate,
	}: {
		onNavigate?: (page: string) => void;
		currentPage?: string;
	}) => (
		<nav data-testid="sidebar">
			{onNavigate && (
				// BG-25 / BG-26: nav button so tests can trigger a real
				// `navigate()` call (and thus App's document.title +
				// focus-management effects) without mocking useNavigation.
				// Label is intentionally "Switch to Settings" (not "Go to
				// Settings") to avoid clashing with the page-not-found
				// fallback's "Go to Home" button tested in UX-19.
				<button type="button" onClick={() => onNavigate("settings")}>
					Switch to Settings
				</button>
			)}
		</nav>
	),
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
		// BG-27: PunctuationCheatSheet (rendered inside the extracted
		// <HelpOverlay />) transitively imports SearchField, which
		// imports Search01Icon. Without this mock entry, opening the
		// help overlay crashes the test.
		Search01Icon: make("Search01Icon"),
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
import type { Page } from "@/types/ipc";

/** Minimal valid config (only the fields App.tsx reads in the help overlay). */
function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		fast_startup: true,
		hotkey: "F2",
		sample_rate: 16000,
		microphone: null,
		model_size: "small.en",
		language: "en",
		device: "cpu",
		beam_size: 5,
		best_of: 1,
		condition_on_previous_text: false,
		streaming_transcription: false,
		streaming_chunk_seconds: 0,
		streaming_step_seconds: 0,
		streaming_left_overlap_seconds: 0,
		streaming_right_guard_seconds: 0,
		streaming_min_first_chunk_seconds: 0,
		streaming_silence_threshold: 0,
		autostart: false,
		paste_on_stop: true,
		show_notifications: true,
		clipboard_save_restore: true,
		clipboard_restore_delay_ms: 150,
		asr_backend: "whisper",
		qwen_model_path: null,
		parakeet_model_path: null,
		text_cleanup_enabled: true,
		unsafe_paste_on_unknown_focus: false,
		corrections_path: null,
		log_transcriptions: false,
		recording_mode: "toggle",
		push_to_talk_hotkey: "",
		esc_cancel_enabled: true,
		repaste_hotkey: "",
		auto_punctuation: false,
		templates_enabled: true,
		vocabulary_enabled: true,
		cloud_api_key: "",
		cloud_api_url: "",
		cloud_model: "",
		openai_api_key: "",
		groq_api_key: "",
		deepgram_api_key: "",
		llm_polish: false,
		llm_api_key: "",
		llm_api_url: "",
		llm_model: "",
		llm_preset: "default",
		crash_recovery_enabled: true,
		audio_quality_warnings: false,
		waveform_bubble: true,
		bubble_position: "top",
		bubble_behavior: "show_on_record",
		bubble_draggable: true,
		bubble_show_on_startup: false,
		history_retention_days: 30,
		history_retention_count: 100,
		history_max_entries: 1000,
		onboarding_completed: true,
		tray_left_click_action: "open_app",
		theme_mode: "system",
		theme_preset: "custom",
		custom_theme: {
			light: {},
			dark: {},
		},
		text_size: 14,
		wayland_warned: false,
		silence_warning_seconds: 0,
		stop_on_silence_seconds: 0,
		max_recording_time_seconds: 900,
		volume_duck_enabled: false,
		volume_duck_level: 0,
		volume_duck_per_session: false,
		volume_duck_fade_ms: 0,
		volume_duck_smart: false,
		volume_duck_smart_poll_interval_ms: 0,
		audio_preset: "auto",
		noise_filter_enabled: false,
		noise_filter_highpass: false,
		noise_filter_highpass_cutoff_hz: 0,
		noise_filter_gate: false,
		noise_filter_gate_threshold: 0,
		noise_filter_gate_hold_ms: 0,
		noise_filter_gate_open_threshold_db: 0,
		noise_filter_gate_close_threshold_db: 0,
		noise_filter_gate_attack_ms: 0,
		noise_filter_gate_release_ms: 0,
		noise_filter_rnnoise: false,
		noise_filter_post_capture: false,
		noise_suppression_method: "none",
		noise_filter_eq: false,
		noise_filter_eq_low_db: 0,
		noise_filter_eq_mid_db: 0,
		noise_filter_eq_high_db: 0,
		noise_filter_compressor: false,
		noise_filter_compressor_threshold_db: 0,
		noise_filter_compressor_ratio: 0,
		noise_filter_compressor_attack_ms: 0,
		noise_filter_compressor_release_ms: 0,
		noise_filter_compressor_output_gain_db: 0,
		noise_filter_limiter: false,
		noise_filter_limiter_ceiling_db: 0,
		noise_filter_limiter_release_ms: 0,
		noise_filter_notch: false,
		noise_filter_notch_frequency_hz: 0,
		huggingface_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
		voice_biometric_consent: false,
		llm_polish_consent: false,
		sound_feedback_enabled: false,
		ai_enhancement_enabled: false,
		auto_capitalize: true,
		auto_punctuate: true,
		fix_grammar_basics: true,
		vocabulary_automation_enabled: false,
		vocabulary_auto_confidence_threshold: 0.7,
		vocabulary_auto_apply_threshold: 0.95,
		...overrides,
	} as VoiceTyperConfig;
}

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

// ── UX-19: page-not-found fallback ───────────────────────────────────

describe("UX-19: App page-not-found fallback uses i18n + Go-to-Home button", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockNavigate.mockReset();
		localStorage.clear();
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ onboarding_completed: true }),
		});
	});

	afterEach(() => {
		cleanup();
		// BG-24: vi.resetModules() resets the module registry but does
		// NOT undo vi.doMock registrations from the test body. Without
		// this doUnmock, the UX-19 test's `vi.doMock("@/hooks/useNavigation",
		// ...)` (which forces currentPage to an invalid page literal)
		// leaks into subsequent UX-24 / UX-25 tests, breaking their
		// assumption that App renders the home page on mount.
		vi.doUnmock("@/hooks/useNavigation");
		vi.resetModules();
	});

	it("renders the i18n 'Page not found' heading and the unknown-page value", async () => {
		// Mock useNavigation to return an unknown currentPage so the
		// default branch of renderPage() is hit. We cast to Page to
		// bypass the type system (the whole point of the fallback is
		// to defend against runtime values that escape the type).
		vi.doMock("@/hooks/useNavigation", () => ({
			useNavigation: () => ({
				currentPage: "totally_invalid_page" as Page,
				navigate: mockNavigate,
				goBack: vi.fn(),
				goForward: vi.fn(),
				canGoBack: false,
				canGoForward: false,
			}),
		}));

		const { default: App } = await import("@/App");
		render(<App />);

		// BG-24 / UX-19: the heading uses t("app.pageNotFoundTitle") =
		// "Page not found". The key is translated across all 8 locales;
		// the fallback resolves to the active locale's translation.
		await waitFor(() => {
			expect(screen.getByText("Page not found")).toBeTruthy();
		});

		// BG-24 / UX-19: the unknown-page value uses
		// t("app.pageNotFoundDescription", { page }) which interpolates
		// to "Unknown page: totally_invalid_page".
		expect(screen.getByText("Unknown page: totally_invalid_page")).toBeTruthy();
	});

	it("renders a 'Go to Home' button that calls navigate('home') on click", async () => {
		vi.doMock("@/hooks/useNavigation", () => ({
			useNavigation: () => ({
				currentPage: "totally_invalid_page" as Page,
				navigate: mockNavigate,
				goBack: vi.fn(),
				goForward: vi.fn(),
				canGoBack: false,
				canGoForward: false,
			}),
		}));

		const { default: App } = await import("@/App");
		render(<App />);

		const goHomeButton = await waitFor(() =>
			screen.getByRole("button", { name: "Go to Home" }),
		);
		expect(goHomeButton).toBeTruthy();

		// UX-19: clicking the button calls navigate("home").
		fireEvent.click(goHomeButton);
		expect(mockNavigate).toHaveBeenCalledWith("home");
	});
});

// ── UX-24: help overlay shows the user's actual configured hotkey ────

describe("UX-24: help overlay shows the user's actual configured hotkey", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
	});

	afterEach(() => {
		cleanup();
		vi.resetModules();
	});

	it("renders the configured dictation hotkey (F2) instead of the hardcoded 'Caps Lock' label", async () => {
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ hotkey: "F2" }),
		});

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Open the help overlay.
		dispatchKey("?");
		await waitFor(() => {
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});

		// UX-24: the dictation shortcut label must show the user's
		// actual hotkey ("F2"), not the hardcoded "Caps Lock" string
		// from the previous t("help.keys.dictation") translation.
		// formatHotkeyLabel("F2") returns "F2".
		const kbdElements = screen.getAllByText("F2");
		expect(kbdElements.length).toBeGreaterThanOrEqual(1);
	});

	it("renders the configured repaste hotkey when set", async () => {
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({
				hotkey: "F2",
				repaste_hotkey: "<ctrl>+<shift>+v",
			}),
		});

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		dispatchKey("?");
		await waitFor(() => {
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});

		// UX-24: formatHotkeyLabel("<ctrl>+<shift>+v") returns
		// "Ctrl+Shift+V". The overlay must show this, not the
		// hardcoded "Ctrl+Alt+V" default.
		expect(screen.getByText("Ctrl+Shift+V")).toBeTruthy();
	});

	it("falls back to the Caps Lock default when the config hotkey is empty", async () => {
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ hotkey: "" }),
		});

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		dispatchKey("?");
		await waitFor(() => {
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});

		// UX-24: when config.hotkey is empty, formatHotkeyLabel falls
		// back to "<caps_lock>" which formats to "Caps Lock".
		expect(screen.getByText("Caps Lock")).toBeTruthy();
	});

	it("falls back to the Ctrl+Alt+V default when repaste_hotkey is empty", async () => {
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({
				hotkey: "F2",
				repaste_hotkey: "",
			}),
		});

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		dispatchKey("?");
		await waitFor(() => {
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});

		// UX-24: when repaste_hotkey is empty, formatHotkeyLabel falls
		// back to "<ctrl>+<alt>+v" which formats to "Ctrl+Alt+V".
		expect(screen.getByText("Ctrl+Alt+V")).toBeTruthy();
	});
});

// ── UX-25: `?` keydown skips contentEditable elements ───────────────

describe("UX-25: `?` keydown guard skips contentEditable elements", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ onboarding_completed: true }),
		});
	});

	afterEach(() => {
		cleanup();
		vi.resetModules();
	});

	it("does NOT open the help overlay when '?' is pressed inside a contentEditable element", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Simulate focus moving into a contentEditable element (e.g. a
		// rich-text editor like Slate/ProseMirror). The previous guard
		// only checked <input>/<textarea>/<select>, so typing "?"
		// inside a contentEditable would pop the overlay and steal
		// focus. UX-25 adds `active?.isContentEditable === true` to
		// the skip predicate.
		const editable = document.createElement("div");
		editable.contentEditable = "true";
		document.body.appendChild(editable);
		editable.focus();

		// Sanity check: the simulated element really IS contentEditable
		// and really IS the active element.
		expect(document.activeElement).toBe(editable);
		expect(editable.isContentEditable).toBe(true);

		dispatchKey("?");

		// The help overlay must NOT open.
		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		document.body.removeChild(editable);
	});

	it("STILL opens the help overlay when '?' is pressed outside any editable element", async () => {
		// Regression guard: confirm the new isContentEditable check
		// didn't accidentally disable the shortcut entirely.
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Active element is <body> (no input focused).
		expect(document.activeElement).toBe(document.body);

		dispatchKey("?");

		await waitFor(() => {
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});
	});
});

// ── BG-25: document.title updates on route change ──────────────────────

describe("BG-25: document.title updates on route change", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		document.title = "";
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ onboarding_completed: true }),
		});
	});

	afterEach(() => {
		cleanup();
		vi.resetModules();
		document.title = "";
	});

	it("sets document.title on initial mount based on the active page", async () => {
		// useNavigation defaults to "home" on a clean localStorage, so
		// the initial title should be "Home — Voice Typer".
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// BG-25: title is `t("nav.<page>") + " — " + APP_NAME`.
		// APP_NAME is "Voice Typer" (src/renderer/src/branding.ts).
		expect(document.title).toBe("Home — Voice Typer");
	});

	it("updates document.title when the user navigates to a different page", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});
		expect(document.title).toBe("Home — Voice Typer");

		// Click the mocked Sidebar's "Switch to Settings" button to
		// trigger a real `navigate("settings")` call (useNavigation
		// is NOT mocked here — the real hook handles the state
		// transition so App's document.title effect re-fires).
		fireEvent.click(screen.getByText("Switch to Settings"));

		await waitFor(() => {
			expect(screen.getByTestId("settings-page")).toBeTruthy();
		});

		// BG-25: title updated to the Settings page's nav label.
		expect(document.title).toBe("Settings — Voice Typer");
	});
});

// ── BG-26: focus management on route change ────────────────────────────

describe("BG-26: focus moves to <main> on route change (skip link + tabIndex plumbing)", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: makeConfig({ onboarding_completed: true }),
		});
	});

	afterEach(() => {
		cleanup();
		vi.resetModules();
	});

	it("does NOT steal focus on the initial mount (skipFirstRun guard)", async () => {
		// On mount, the user hasn't navigated yet — stealing focus from
		// whatever they were doing (URL bar, bookmark, etc.) would be
		// rude. The skipFirstRun ref suppresses the focus call on the
		// first run of the effect.
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Active element should remain <body> (the default after render)
		// — NOT the main-content element. This is the skipFirstRun guard.
		const mainEl = document.getElementById("main-content");
		expect(mainEl).toBeTruthy();
		expect(document.activeElement).not.toBe(mainEl);
	});

	it("moves focus to <main id='main-content'> after a navigation event", async () => {
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Before navigation, focus is NOT on main-content.
		const mainEl = document.getElementById("main-content");
		expect(mainEl).toBeTruthy();
		expect(document.activeElement).not.toBe(mainEl);

		// Navigate to Settings via the mocked Sidebar button. Clicking
		// the button moves focus to the button itself, then App's
		// focus-management effect fires and moves focus to main-content.
		fireEvent.click(screen.getByText("Switch to Settings"));

		await waitFor(() => {
			expect(screen.getByTestId("settings-page")).toBeTruthy();
		});

		// BG-26: focus moved to <main id="main-content"> after the
		// route change. The element has tabIndex={-1} so it can
		// receive programmatic focus without being in the tab order.
		expect(document.activeElement).toBe(mainEl);
	});
});
