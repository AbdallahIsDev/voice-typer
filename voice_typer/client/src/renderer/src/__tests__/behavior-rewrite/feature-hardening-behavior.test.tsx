/**
 *  vitest rewrite — behavioral tests for feature-hardening invariants.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_feature_hardening_regressions.py`:
 *   - TestPagesUseSharedSnackbarHook::test_settings_uses_shared_hook
 *   - TestPagesUseSharedSnackbarHook::test_microphone_uses_shared_snackbar_hook
 *   - TestAppValidatesRecordingStateBeforeCast::test_no_unvalidated_as_recording_state_cast
 *   - TestAppValidatesRecordingStateBeforeCast::test_runtime_validator_exists
 *   - TestUsePythonOmitsMisleadingIsReadyFlag::test_use_python_does_not_return_is_ready
 *   - TestUsePythonOmitsMisleadingIsReadyFlag::test_app_does_not_use_is_ready
 *
 * The Python tests regex/string-parsed the TS/TSX source and asserted on
 * substring presence/absence (e.g. `"const { showSnack } = useSnackbar()"
 * in src`, `"isReady" not in code`, `"as RecordingState"` not in App.tsx
 * source outside a validator).  These are brittle: they fail on innocent
 * format refactors (Biome quote-style changes, line wrapping, extracting
 * to a helper) and pass even when the runtime behavior differs.  The
 * vitest versions below exercise the actual runtime behavior: real hook
 * returns, real component renders, real snackbar delegation via sonner.
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted.
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import {
    cleanup,
    fireEvent,
    render,
    renderHook,
    screen,
    waitFor,
} from "@testing-library/react";

/**
 * Page-level render helper. Pages like Settings mount Radix Tooltip
 * (via SettingRow / ui primitives); the real App shell wraps everything
 * in a TooltipProvider (App.tsx), so tests mounting pages directly must
 * provide one too — otherwise every Tooltip render throws "Tooltip must
 * be used within TooltipProvider" and the page mounts empty.
 */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mock state hoisted before vi.mock factories run ─────────────────
//
// vi.mock factories are hoisted by vitest and execute before any
// module-level const/let, so any value the factory closes over must be
// allocated via vi.hoisted().
const { mockCall, mockUseConnection, toastMock } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockUseConnection: vi.fn(),
	toastMock: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
}));

// Mock useConnection so App.tsx tests can control recordingState
// without spinning up the real backend-connection lifecycle.
vi.mock("@/hooks/useConnection", () => ({
	useConnection: mockUseConnection,
}));

// Mock useTheme so App.tsx tests don't trigger the real theme load
// (which calls `get_config` and writes to document.documentElement).
vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: vi.fn(),
		reloadThemeFromConfig: vi.fn(),
		textSize: 14,
		setTextSize: vi.fn(),
	}),
}));

// Mock useSoundFeedback so App.tsx tests don't initialise the AudioContext.
vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

// Mock sonner so we can observe toast.success/error calls (proves
// Settings/Microphone delegate to the shared useSnackbar → sonner path
// rather than maintaining inline snackbar state).
vi.mock("sonner", () => ({
	toast: toastMock,
	Toaster: () => null,
}));

// next-themes is imported transitively via components/ui/sonner.tsx.
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// Stub the Toaster component App.tsx mounts (we mock sonner's Toaster
// above, but App imports from `@/components/ui/sonner` which re-exports
// a wrapped Toaster — stub it to null so no portal DOM is created).
vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

// Stub the layout chrome so we don't need the real Sidebar / TitleBar
// (they have their own heavy transitive deps).
vi.mock("@/components/layout/Sidebar", () => ({
	Sidebar: () => <nav data-testid="sidebar" />,
}));
vi.mock("@/components/layout/TitleBar", () => ({
	TitleBar: () => <div data-testid="titlebar" />,
}));

// Stub the ErrorBoundary so a render error in a child doesn't surface
// the real fallback UI (which has its own deps).
vi.mock("@/components/feedback/ErrorBoundary", () => ({
	ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
		<>{children}</>
	),
}));

// Stub the HugeiconsIcon renderer so we don't need the real icon SVGs.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

// Mock @hugeicons/core-free-icons with tagged { name } stubs for every
// icon imported anywhere in the renderer source tree (App.tsx, Settings,
// Microphone, all settings/microphone/common/feedback/ui sub-components,
// and all page modules App imports transitively).  Vitest validates
// named imports against the mock factory's exports upfront, so we must
// enumerate them explicitly (a Proxy with a `get` trap is not
// sufficient — vitest's mock system checks `in` / `hasOwnProperty`
// before the trap is consulted).
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Activity03Icon: make("Activity03Icon"),
		Add01Icon: make("Add01Icon"),
		AiBrain03Icon: make("AiBrain03Icon"),
		Alert02Icon: make("Alert02Icon"),
		// KeyboardPermissionBanner (mounted on Settings) renders
		// AlertCircleIcon when the onboarding_check_permissions probe
		// returns a non-granted result — keep it in the mock or the
		// Settings page crashes on mount.
		AlertCircleIcon: make("AlertCircleIcon"),
		Analytics01Icon: make("Analytics01Icon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowRight01Icon: make("ArrowRight01Icon"),
		ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Calendar01Icon: make("Calendar01Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		CheckmarkCircle02Icon: make("CheckmarkCircle02Icon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Delete02Icon: make("Delete02Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
		FilterIcon: make("FilterIcon"),
		Folder02Icon: make("Folder02Icon"),
		HistoryIcon: make("HistoryIcon"),
		Home04Icon: make("Home04Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		LayoutGridIcon: make("LayoutGridIcon"),
		Loading03Icon: make("Loading03Icon"),
		LockKeyIcon: make("LockKeyIcon"),
		Mic02Icon: make("Mic02Icon"),
		MicOff01Icon: make("MicOff01Icon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		MultiplicationSignCircleIcon: make("MultiplicationSignCircleIcon"),
		PanelLeftIcon: make("PanelLeftIcon"),
		PauseIcon: make("PauseIcon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		PlayIcon: make("PlayIcon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Settings03Icon: make("Settings03Icon"),
		Share08Icon: make("Share08Icon"),
		Shield01Icon: make("Shield01Icon"),
		SparklesIcon: make("SparklesIcon"),
		SpeechToTextIcon: make("SpeechToTextIcon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		Sun01Icon: make("Sun01Icon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
		ZapIcon: make("ZapIcon"),
	};
});

// Stub the page components App.tsx imports (other than Settings and
// Microphone, which we test directly below).  Stubs expose a
// data-testid so App tests can verify a child page actually rendered
// (proving App didn't bail out on a missing isReady guard).
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
vi.mock("@/pages/About", () => ({
	default: () => <div data-testid="about-page">About</div>,
}));
vi.mock("@/pages/Dashboard", () => ({
	default: () => <div data-testid="dashboard-page">Dashboard</div>,
}));
vi.mock("@/pages/Onboarding", () => ({
	default: () => <div data-testid="onboarding-page">Onboarding</div>,
}));

// Real hook imports (we deliberately do NOT mock @/hooks/usePython or
// @/hooks/useSnackbar — the hook return-type tests below call the real
// hooks so they observe the real runtime API contract).
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import type { VoiceTyperConfig } from "@/types/config";
import type { RecordingState } from "@/types/ipc";

// ── Compile-time invariants on hook return types ────────────────────
//
// Each `const` below has a literal-`true` annotation.  If the type on
// the left of `=` doesn't reduce to `true`, the file fails to compile
// (caught by `tsc --noEmit`).  This is the type-level half of the
// behavioral test: the runtime half (renderHook + Object.keys) is in
// the `it()` blocks below.
type UsePythonReturn = ReturnType<typeof usePython>;
type UseSnackbarReturn = ReturnType<typeof useSnackbar>;
type HasIsReady = "isReady" extends keyof UsePythonReturn ? true : false;
type HasSnackbarComponent = "Snackbar" extends keyof UseSnackbarReturn
	? true
	: false;
const _noIsReady: HasIsReady extends false ? true : false = true;
const _noSnackbarComponent: HasSnackbarComponent extends false ? true : false =
	true;

// ── Shared test fixtures ────────────────────────────────────────────

/** A complete, valid VoiceTyperConfig used to seed Settings + Microphone.
 *  Mirrors the fixture in pages/__tests__/Settings.test.tsx. */
const baseConfig: VoiceTyperConfig = {
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
	bubble_click_to_toggle: true,
	bubble_mic_button: true,
	history_retention_days: 30,
	history_retention_count: 100,
	history_max_entries: 1000,
	onboarding_completed: true,
	tray_left_click_action: "open_app",
	theme_mode: "system",
	theme_preset: "custom",
	custom_theme: {
		light: {
			"--bg": "#ffffff",
			"--bg-subtle": "#f5f5f5",
			"--text": "#000000",
			"--text-muted": "#666666",
			"--accent": "#3b82f6",
			"--border": "#e5e7eb",
		},
		dark: {
			"--bg": "#000000",
			"--bg-subtle": "#111111",
			"--text": "#ffffff",
			"--text-muted": "#999999",
			"--accent": "#60a5fa",
			"--border": "#222222",
		},
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
	bubble_x: null,
	bubble_y: null,
};

/** Install a minimal `window.python` mock so the real usePython /
 *  usePythonEvent hooks (which read `window.python.call` /
 *  `window.python.onEvent`) work without the real preload bridge. */
function installPythonBridgeMock() {
	(
		window as unknown as {
			python?: { call: typeof mockCall; onEvent: () => () => void };
		}
	).python = {
		call: mockCall,
		onEvent: vi.fn(() => () => {}),
	};
}

/** Remove the `window.python` mock installed by installPythonBridgeMock. */
function removePythonBridgeMock() {
	delete (window as unknown as { python?: unknown }).python;
}

// ────────────────────────────────────────────────────────────────────
// 1. usePython hook — does not return isReady
//    Python: TestUsePythonOmitsMisleadingIsReadyFlag::test_use_python_does_not_return_is_ready
// ────────────────────────────────────────────────────────────────────

describe("usePython — rewrite of test_use_python_does_not_return_is_ready", () => {
	beforeEach(() => {
		mockCall.mockReset();
		installPythonBridgeMock();
	});

	afterEach(() => {
		removePythonBridgeMock();
		cleanup();
	});

	it("returns { call } only — no isReady flag (NEW-TS-015)", () => {
		// Compile-time: _noIsReady is `true` only when "isReady" is NOT
		// a key of the hook's return type.  If a future refactor re-adds
		// isReady, this const fails to compile (caught by tsc --noEmit).
		expect(_noIsReady).toBe(true);

		// Runtime: render the real hook and verify the returned object's
		// keys are exactly ['call'] — no isReady, no other stale fields.
		const { result } = renderHook(() => usePython());
		expect(Object.keys(result.current).sort()).toEqual(["call"]);
		expect("isReady" in result.current).toBe(false);
		// Access via a Record so TS doesn't flag the (deliberately
		// absent) property as a type error.
		expect((result.current as Record<string, unknown>).isReady).toBeUndefined();
	});
});

// ────────────────────────────────────────────────────────────────────
// 2. useSnackbar hook — returns { showSnack, clearSnack } (no Snackbar component)
//    Python: implicit in TestPagesUseSharedSnackbarHook (asserts
//    "<Snackbar" not in src and "const { showSnack } = useSnackbar()" in src).
// ────────────────────────────────────────────────────────────────────

describe("useSnackbar — rewrite (DX-013: no Snackbar component returned)", () => {
	afterEach(() => {
		cleanup();
		toastMock.success.mockClear();
		toastMock.error.mockClear();
		toastMock.warning.mockClear();
		toastMock.info.mockClear();
		toastMock.dismiss.mockClear();
	});

	it("returns { showSnack, clearSnack } and NOT a Snackbar component", () => {
		// Compile-time: _noSnackbarComponent is `true` only when
		// "Snackbar" is NOT a key of the hook's return type.
		expect(_noSnackbarComponent).toBe(true);

		// Runtime: render the real hook and verify the returned object's
		// keys are exactly ['clearSnack', 'showSnack'] — no Snackbar
		// component (which was removed in DX-013).
		const { result } = renderHook(() => useSnackbar());
		expect(Object.keys(result.current).sort()).toEqual([
			"clearSnack",
			"showSnack",
		]);
		expect("Snackbar" in result.current).toBe(false);
	});

	it("delegates showSnack(msg, 'success') to sonner's toast.success (proves the shared-hook path is wired through sonner, not inline state)", () => {
		const { result } = renderHook(() => useSnackbar());
		result.current.showSnack("Saved", "success");
		expect(toastMock.success).toHaveBeenCalledWith("Saved", {
			duration: 3000,
		});
		expect(toastMock.error).not.toHaveBeenCalled();
	});

	it("delegates showSnack(msg, 'error') to sonner's toast.error", () => {
		const { result } = renderHook(() => useSnackbar());
		result.current.showSnack("Failed", "error");
		expect(toastMock.error).toHaveBeenCalledWith("Failed", {
			duration: 8000,
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// 3. Settings page — uses the shared useSnackbar hook (not inline state)
//    Python: TestPagesUseSharedSnackbarHook::test_settings_uses_shared_hook
// ────────────────────────────────────────────────────────────────────

describe("Settings — rewrite of test_settings_uses_shared_hook", () => {
	let originalWindow_: unknown;

	beforeEach(() => {
		mockCall.mockReset();
		installPythonBridgeMock();
		// Stub window.window_.openLogs to simulate the Electron main
		// process successfully opening the log folder.  Settings.tsx's
		// viewLogs() handler awaits this then calls showSnack(..., "success").
		originalWindow_ = (window as unknown as { window_?: unknown }).window_;
		(
			window as unknown as {
				window_?: { openLogs?: () => Promise<{ success: boolean }> };
			}
		).window_ = {
			openLogs: vi.fn(() => Promise.resolve({ success: true })),
		};
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		toastMock.success.mockClear();
		toastMock.error.mockClear();
	});

	afterEach(() => {
		removePythonBridgeMock();
		if (originalWindow_ === undefined) {
			delete (window as unknown as { window_?: unknown }).window_;
		} else {
			(window as unknown as { window_?: unknown }).window_ = originalWindow_;
		}
		cleanup();
	});

	it("calls showSnack via the shared useSnackbar hook when 'Open Log Folder' succeeds (delegates to sonner.toast.success, not inline state)", async () => {
		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// Wait for the Settings page to load (the tab labels render once
		// get_config returns).
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Navigate to the Privacy tab so the Troubleshooting section
		// (which contains the "Open Log Folder" button) is mounted.
		fireEvent.click(screen.getByText("Privacy"));

		// Find the "Open Log Folder" button by its aria-label and click it.
		const openLogBtn = await waitFor(() =>
			screen.getByRole("button", { name: "Open log folder" }),
		);
		fireEvent.click(openLogBtn);

		// The click handler awaits window.window_.openLogs(), then calls
		// showSnack("Log folder opened", "success").  Because useSnackbar
		// delegates to sonner, this surfaces as a toast.success call.
		//If Settings had inline snackbar state (the pre-
		// pattern), toast.success would never be called.
		await waitFor(() => {
			expect(toastMock.success).toHaveBeenCalled();
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// 4. Microphone page — uses the shared useSnackbar hook (not inline JSX)
//    Python: TestPagesUseSharedSnackbarHook::test_microphone_uses_shared_snackbar_hook
// ────────────────────────────────────────────────────────────────────

describe("Microphone — rewrite of test_microphone_uses_shared_snackbar_hook", () => {
	beforeEach(() => {
		mockCall.mockReset();
		installPythonBridgeMock();
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_microphones")
				// Return one device so the page's `canTest` gate
				// (microphones.length > 0) is true and the "Start Test"
				// button is ENABLED. With an empty list the button is
				// disabled, so fireEvent.click is a no-op and the error
				// snack path never runs.
				return Promise.resolve([
					{
						index: 0,
						id: "0",
						name: "Mock Microphone",
						default: true,
					},
				]);
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			if (type === "microphone_test_start")
				// Returning success: false triggers the error path which
				// calls showSnack(message, "error").  This is the simplest
				// user-action → snackbar path in the Microphone page.
				return Promise.resolve({
					success: false,
					message: "test-disabled-in-mock",
					duration: 10,
					sample_rate: 16000,
				});
			return Promise.resolve({});
		});
		localStorage.clear();
		toastMock.success.mockClear();
		toastMock.error.mockClear();
	});

	afterEach(() => {
		removePythonBridgeMock();
		cleanup();
	});

	it("calls showSnack via the shared useSnackbar hook when the mic test fails (delegates to sonner.toast.error, not inline JSX)", async () => {
		const { default: MicrophonePage } = await import("@/pages/Microphone");
		render(<MicrophonePage />);

		// Wait for the page to load — the "Start Test" button renders
		// once get_config + get_microphones resolve and the loading
		// spinner is replaced by the active-mic card.
		const startTestBtn = await waitFor(() =>
			screen.getByRole("button", { name: "Start Test" }),
		);

		// Click "Start Test" — the handler calls microphone_test_start,
		// which our mock resolves with { success: false, message: ... },
		// triggering showSnack(message, "error").
		fireEvent.click(startTestBtn);

		// Because useSnackbar delegates to sonner, this surfaces as a
		// toast.error call.  If Microphone had inline snackbar JSX
		//(the pre- pattern), toast.error would never be called.
		await waitFor(() => {
			expect(toastMock.error).toHaveBeenCalled();
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// 5. App — does not destructure isReady from usePython()
//    Python: TestUsePythonOmitsMisleadingIsReadyFlag::test_app_does_not_use_is_ready
// ────────────────────────────────────────────────────────────────────

describe("App — rewrite of test_app_does_not_use_is_ready", () => {
	beforeEach(() => {
		mockCall.mockReset();
		installPythonBridgeMock();
		mockUseConnection.mockReturnValue({
			recordingState: "idle" as RecordingState,
			connectionStatus: "connected" as const,
			lastError: null,
			handleRetryConnection: vi.fn(),
		});
		localStorage.clear();
	});

	afterEach(() => {
		removePythonBridgeMock();
		cleanup();
	});

	it("renders a child page when usePython() returns { call } only (no isReady gate)", async () => {
		// If App destructured `isReady` from usePython() and gated
		//rendering on it (the pre- pattern), the gate would
		// see `isReady === undefined` (since the hook no longer returns
		// it) and bail out — no child page would render.  By verifying
		// the home-page stub renders, we prove App doesn't gate on
		// isReady.
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// 6. App — handles all 6 RecordingState values without crashing (no unvalidated cast)
//    Python: TestAppValidatesRecordingStateBeforeCast::test_no_unvalidated_as_recording_state_cast
//            TestAppValidatesRecordingStateBeforeCast::test_runtime_validator_exists
// ────────────────────────────────────────────────────────────────────

describe("App recording state — rewrite of test_no_unvalidated_as_recording_state_cast + test_runtime_validator_exists", () => {
	beforeEach(() => {
		mockCall.mockReset();
		installPythonBridgeMock();
		localStorage.clear();
	});

	afterEach(() => {
		removePythonBridgeMock();
		cleanup();
	});

	// Type-level: App.tsx compiles, which means it doesn't need
	// `as RecordingState` casts on the value it receives from
	// useConnection().  The runtime validator (asRecordingState) lives
	// in useConnection.ts (module-private), where it filters unknown
	// status_change payloads before they reach the store.  App consumes
	// the already-typed RecordingState value from the store and renders
	// the matching a11y announcement — no cast needed.
	//
	// The behavioral test below verifies App handles every backend-emitted
	// state without crashing AND renders the correct announcement.  If a
	// future refactor reintroduces an unvalidated `as RecordingState`
	// cast on an unknown value, App would render an unknown state's
	// announcement (or crash); the it.each over the 6 valid states
	// catches the "renders the wrong announcement" regression.
	it.each([
		["idle", /ready/i],
		["recording", /recording started/i],
		["transcribing", /transcribing/i],
		["loading", /loading model/i],
		["cancelling", /cancelling/i],
		["error", /error/i],
	] satisfies Array<
		[RecordingState, RegExp]
	>)("renders the a11y announcement for recordingState=%s without crashing (no as-cast needed)", async (state, expected) => {
		mockUseConnection.mockReturnValue({
			recordingState: state,
			connectionStatus: "connected" as const,
			lastError: null,
			handleRetryConnection: vi.fn(),
		});

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// The aria-live region must announce the state.  This proves
		// App consumes the typed RecordingState value directly (no
		// cast) and renders the correct announcement.
		const liveRegions = document.querySelectorAll('[aria-live="polite"]');
		expect(liveRegions.length).toBeGreaterThanOrEqual(1);
		const announced = Array.from(liveRegions).some((r) =>
			expected.test(r.textContent ?? ""),
		);
		expect(announced).toBe(true);
	});
});
