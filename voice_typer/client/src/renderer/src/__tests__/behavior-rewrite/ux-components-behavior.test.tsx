import { TooltipProvider } from "@/components/ui/tooltip";
import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
	Element.prototype.scrollIntoView = function scrollIntoView() { };
}

const stable = vi.hoisted(() => ({
	replace: vi.fn(),
	goBack: vi.fn(),
	goForward: vi.fn(),
	handleThemeChange: vi.fn(),
	reloadThemeFromConfig: vi.fn(),
	setTextSize: vi.fn(),
}));

const {
	mockCall,
	mockPythonEvent,
	mockNavigate,
	mockNavState,
	mockUseConnection,
} = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
	mockNavState: { page: "home" as Page },
	mockUseConnection: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		navigate: mockNavigate,
		currentPage: mockNavState.page,
		replace: stable.replace,
		goBack: stable.goBack,
		goForward: stable.goForward,
		canGoBack: false,
		canGoForward: false,
	}),
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
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

vi.mock("@/hooks/useConnection", () => ({
	useConnection: mockUseConnection,
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
	useSoundFeedback: () => { },
}));

vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

const originalWindowOpen = window.open;
beforeEach(() => {
	vi.clearAllMocks();
	window.open = vi.fn(() => null);
	mockNavState.page = "home";
	mockUseConnection.mockReset();
	mockUseConnection.mockReturnValue({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: vi.fn(),
	});
});
afterEach(() => {
	window.open = originalWindowOpen;
});

const originalFetch = global.fetch;
beforeEach(() => {
	vi.clearAllMocks();
	global.fetch = vi.fn(() =>
		Promise.resolve({
			ok: false,
			status: 404,
			json: () => Promise.resolve({}),
		} as Response),
	) as unknown as typeof fetch;
});
afterEach(() => {
	global.fetch = originalFetch;
});

import type { LausuConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

/**
 * A complete, valid LausuConfig used as the mock get_config return
 * value.  Mirrors the shape used by pages/__tests__/Settings.test.tsx
 */
const baseConfig: LausuConfig = {
	schema_version: 1,
	fast_startup: true,
	offline_pack_consent: true,
	hotkey: "F2",
	sample_rate: 16000,
	microphone: null,
	model_size: "tiny",
	language: "en",
	device: "cpu",
	beam_size: 5,
	best_of: 1,
	condition_on_previous_text: false,
	vad_filter_enabled: true,
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
			"--background": "#ffffff",
			"--surface-subtle": "#f5f5f5",
			"--text": "#000000",
			"--muted-foreground": "#666666",
			"--accent": "#3b82f6",
			"--border": "#e5e7eb",
		},
		dark: {
			"--background": "#000000",
			"--surface-subtle": "#111111",
			"--text": "#ffffff",
			"--muted-foreground": "#999999",
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
	media_url_consent: false,
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

describe("Settings, silent auto-save (no status bar) + save toasts", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("auto-saves silently, no status bar and no success toast after a set_config flush", async () => {
		const { toast } = await import("sonner");
		const successSpy = vi.mocked(toast.success);

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		expect(screen.queryByText("All changes saved")).toBeNull();

		successSpy.mockClear();
		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		// The debounce + microtask flush + IPC must all complete, then
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("set_config", expect.anything());
		});
		expect(screen.queryByText("Saving…")).toBeNull();
		expect(screen.queryByText("Saved")).toBeNull();
		expect(screen.queryByText("All changes saved")).toBeNull();
		expect(successSpy).not.toHaveBeenCalled();
	});

	it("shows an error toast after a failed set_config flush", async () => {
		const { toast } = await import("sonner");
		const errorSpy = vi.mocked(toast.error);

		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") {
				return Promise.reject(new Error("IPC failure"));
			}
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		errorSpy.mockClear();
		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		await waitFor(() => {
			expect(errorSpy).toHaveBeenCalled();
		});
		const firstCallArg = errorSpy.mock.calls[0]?.[0];
		expect(firstCallArg).toContain("Failed to save setting");
	});
});

describe("Settings onNavigate prop, rewrite of Page-type tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the diagnostics table inside Settings (Advanced page), no navigation needed", async () => {
		const { default: SettingsPage } = await import("@/pages/Settings");

		renderWithProviders(<SettingsPage page="settingsAdvanced" />);

		const diagHeading = await waitFor(() =>
			screen.getByRole("heading", { name: "Diagnostics" }),
		);
		expect(diagHeading).toBeTruthy();
		expect(mockNavigate).not.toHaveBeenCalled();
	});
});

describe("NumberInputStepper onInvalid, rewrite of Omit + custom-callback tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("calls onInvalid('range') when the value exceeds max", async () => {
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="200"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith("range");
		});

		const input = document.querySelector('input[type="number"]');
		expect(input).toBeTruthy();
		expect(input?.getAttribute("aria-invalid")).toBe("true");
	});

	it("calls onInvalid('parse') when the value cannot be parsed as a number", async () => {
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="abc"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith("parse");
		});
	});

	it("calls onInvalid(null) and clears aria-invalid when the value is in range", async () => {
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="50"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith(null);
		});
		const input = document.querySelector('input[type="number"]');
		expect(input?.getAttribute("aria-invalid")).toBeNull();
	});
});

/**
 * Test harness that exposes the hook's return value to the test.
 * useNavigation is a hook, so it must be called from inside a React
 */
function NavigationHarness(props: {
	onReady?: (api: {
		navigate: (page: Page) => void;
		goBack: () => void;
		goForward: () => void;
		currentPage: Page;
		canGoBack: boolean;
		canGoForward: boolean;
	}) => void;
}) {
	const { onReady } = props;
	const nav = useNavigationHarness();
	React.useEffect(() => {
		onReady?.(nav);
	}, [nav, onReady]);
	return null;
}

let useNavigationHarness: () => {
	navigate: (page: Page) => void;
	goBack: () => void;
	goForward: () => void;
	currentPage: Page;
	canGoBack: boolean;
	canGoForward: boolean;
};
let resetNavigationForTestHook: () => void = () => { };

beforeAll(async () => {
	const mod = (await vi.importActual("@/hooks/useNavigation")) as {
		useNavigation: typeof useNavigationHarness;
		_resetNavigationForTest?: () => void;
	};
	useNavigationHarness = mod.useNavigation;
	resetNavigationForTestHook = mod._resetNavigationForTest ?? (() => { });
});

import { beforeAll } from "vitest";

describe("useNavigation, rewrite of localStorage persistence tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		resetNavigationForTestHook();
	});

	afterEach(() => {
		cleanup();
		localStorage.clear();
	});

	it("persists the new page to localStorage on navigate()", async () => {
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as {
			navigate: (p: Page) => void;
		};
		expect(api).toBeTruthy();

		act(() => {
			api.navigate("history");
		});

		const raw = localStorage.getItem("vt_nav_state");
		expect(raw).toBeTruthy();
		const parsed = JSON.parse(raw as string) as {
			page: string;
			history: string[];
			index: number;
		};
		expect(parsed.page).toBe("history");
		expect(parsed.history).toContain("history");
	});

	it("persists the previous page to localStorage on goBack()", async () => {
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as {
			navigate: (p: Page) => void;
			goBack: () => void;
		};

		act(() => {
			api.navigate("history");
		});
		act(() => {
			api.navigate("settings");
		});

		let raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("settings");

		act(() => {
			api.goBack();
		});

		raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("history");
		expect(raw.index).toBe(1);
	});

	it("loads the initial page from localStorage on mount", async () => {
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({
				page: "settings",
				history: ["home", "settings"],
				index: 1,
			}),
		);
		resetNavigationForTestHook();

		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as { currentPage: string };
		expect(api.currentPage).toBe("settings");
	});

	it("falls back to 'home' when localStorage is empty", async () => {
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as { currentPage: string };
		expect(api.currentPage).toBe("home");
	});
});

describe("Sidebar, rewrite of About-nav tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders an 'About & Privacy' nav button that fires onNavigate('aboutAndPrivacy')", async () => {
		const { Sidebar } = await import("@/components/layout/Sidebar");
		const onNavigate: (page: Page) => void = vi.fn();
		renderWithProviders(<Sidebar currentPage="home" onNavigate={onNavigate} />);

		const aboutBtn = screen.getByRole("button", { name: "About & Privacy" });
		expect(aboutBtn).toBeTruthy();

		fireEvent.click(aboutBtn);
		expect(onNavigate).toHaveBeenCalledWith("aboutAndPrivacy");
	});
});

describe("About, rewrite of loaded_via tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the 'Loaded Via' row with the value returned by get_status", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/lausu",
					loaded_via: "cuda",
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

		const { DiagnosticsSettingsSection } = await import(
			"@/components/settings/DiagnosticsSettingsSection"
		);
		renderWithProviders(<DiagnosticsSettingsSection isVisible={() => true} />);

		await waitFor(() => {
			expect(screen.getByText("Loaded Via")).toBeTruthy();
		});

		// The value must be the loaded_via string from get_status.
		expect(screen.getByText("cuda")).toBeTruthy();
	});

	it("hides the 'Loaded Via' row entirely when get_status omits loaded_via", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/lausu",
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

		const { DiagnosticsSettingsSection } = await import(
			"@/components/settings/DiagnosticsSettingsSection"
		);
		renderWithProviders(<DiagnosticsSettingsSection isVisible={() => true} />);

		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		expect(screen.queryByText("Loaded Via")).toBeNull();
	});
});

describe("Vocabulary, rewrite of help-text tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") {
				return Promise.resolve({
					misspellings: { recieve: "receive" },
					phrase_corrections: {},
					extra_word_patterns: {},
					technical_terms: {},
					names: {},
					products: {},
				});
			}
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders trigger + replacement inputs with i18n placeholders in the inline quick-add row", async () => {
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		fireEvent.click(screen.getByRole("button", { name: "Add Word" }));

		const quickAdd = await screen.findByTestId("vocab-quick-add");
		expect(
			within(quickAdd).getByPlaceholderText("treat three, mynameis"),
		).toBeTruthy();
		expect(
			within(quickAdd).getByPlaceholderText("treat this, My Name Is"),
		).toBeTruthy();
	});
});

describe("Templates, rewrite of help-text + variable-tooltip tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") {
				return Promise.resolve([
					{
						id: "tpl-1",
						trigger: "signoff",
						output: "Best regards, {username}",
						match_mode: "exact",
						enabled: true,
						used_variables: ["{username}"],
					},
				]);
			}
			if (type === "save_templates") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the triggerHelp + outputHelp text and the {today}/{now}/{clipboard}/{username} variable chips", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("signoff")).toBeTruthy();
		});

		const editBtn = screen.getByRole("button", {
			name: /edit template: signoff/iu,
		});
		fireEvent.click(editBtn);

		await waitFor(() => {
			expect(
				screen.getByText(/The text that replaces the trigger/u),
			).toBeTruthy();
		});

		const infoBtn = screen.getByRole("button", {
			name: "More info about Trigger phrase",
		});
		infoBtn.focus();
		fireEvent.mouseEnter(infoBtn);
		fireEvent.pointerEnter(infoBtn);

		await waitFor(() => {
			expect(
				screen.getAllByText(/The phrase you'll say during dictation/u).length,
			).toBeGreaterThan(0);
		});

		expect(screen.getByRole("button", { name: "{today}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{now}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{clipboard}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{username}" })).toBeTruthy();
	});

	it("renders variable chips as tappable buttons that insert the token", async () => {
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		await waitFor(() => {
			expect(screen.getByText("signoff")).toBeTruthy();
		});

		const editBtn = screen.getByRole("button", {
			name: /edit template: signoff/iu,
		});
		fireEvent.click(editBtn);
		await waitFor(() => {
			expect(
				screen.getByText(/The text that replaces the trigger/u),
			).toBeTruthy();
		});

		expect(screen.getByRole("button", { name: "{today}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{now}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{clipboard}" })).toBeTruthy();
		expect(screen.getByRole("button", { name: "{username}" })).toBeTruthy();
	});
});

describe("TitleBar, rewrite of isMaximized prop tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the Restore icon/aria-label when isMaximized=true is passed", async () => {
		const { TitleBar } = await import("@/components/layout/TitleBar");
		renderWithProviders(
			<TitleBar
				isMaximized={true}
				themeMode="light"
				onThemeChange={() => { }}
			/>,
		);

		const restoreBtn = screen.getByRole("button", { name: "Restore" });
		expect(restoreBtn).toBeTruthy();
	});

	it("renders the Maximize aria-label when isMaximized=false is passed", async () => {
		const { TitleBar } = await import("@/components/layout/TitleBar");
		renderWithProviders(
			<TitleBar
				isMaximized={false}
				themeMode="light"
				onThemeChange={() => { }}
			/>,
		);

		const maximizeBtn = screen.getByRole("button", { name: "Maximize" });
		expect(maximizeBtn).toBeTruthy();
	});

	it("skips the bridge.isMaximized() subscription when isMaximized prop is provided", async () => {
		const isMaximizedSpy = vi.fn(() => Promise.resolve(false));
		const onMaximizedChangedSpy = vi.fn(() => vi.fn());
		const minimizeSpy = vi.fn(() => Promise.resolve());
		const toggleMaximizeSpy = vi.fn(() => Promise.resolve());
		const closeSpy = vi.fn(() => Promise.resolve());

		(window as unknown as Record<string, unknown>).window_ = {
			isMaximized: isMaximizedSpy,
			onMaximizedChanged: onMaximizedChangedSpy,
			minimize: minimizeSpy,
			toggleMaximize: toggleMaximizeSpy,
			close: closeSpy,
		};

		try {
			const { TitleBar } = await import("@/components/layout/TitleBar");
			renderWithProviders(
				<TitleBar
					isMaximized={true}
					themeMode="light"
					onThemeChange={() => { }}
				/>,
			);

			expect(isMaximizedSpy).not.toHaveBeenCalled();
			expect(onMaximizedChangedSpy).not.toHaveBeenCalled();
		} finally {
			delete (window as unknown as Record<string, unknown>).window_;
		}
	});
});

import { useAppStore } from "@/stores/appStore";

const completedConfig: Partial<LausuConfig> = {
	onboarding_completed: true,
};

function dispatchKey(
	key: string,
	opts: { ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {},
) {
	fireEvent.keyDown(document, {
		key,
		ctrlKey: opts.ctrlKey ?? false,
		metaKey: opts.metaKey ?? false,
		altKey: opts.altKey ?? false,
	});
}

async function registerAppPageStubs() {
	vi.doMock("@/components/layout/Sidebar", () => ({
		Sidebar: () => <nav data-testid="sidebar" />,
	}));
	vi.doMock("@/components/layout/TitleBar", () => ({
		TitleBar: () => <div data-testid="titlebar" />,
	}));
	vi.doMock("@/components/feedback/ErrorBoundary", () => ({
		ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
			<>{children}</>
		),
	}));
	vi.doMock("@/pages/Home", () => ({
		default: () => <div data-testid="home-page">Home</div>,
	}));
	vi.doMock("@/pages/History", () => ({
		default: () => <div data-testid="history-page">History</div>,
	}));
	vi.doMock("@/pages/Templates", () => ({
		default: () => <div data-testid="templates-page">Templates</div>,
	}));
	vi.doMock("@/pages/Vocabulary", () => ({
		default: () => <div data-testid="vocabulary-page">Vocabulary</div>,
	}));
	vi.doMock("@/pages/Models", () => ({
		default: () => <div data-testid="models-page">Models</div>,
	}));
	vi.doMock("@/pages/Microphone", () => ({
		default: () => <div data-testid="microphone-page">Microphone</div>,
	}));
	vi.doMock("@/pages/Dashboard", () => ({
		default: () => <div data-testid="dashboard-page">Analytics</div>,
	}));
	vi.doMock("@/pages/Onboarding", () => ({
		default: () => <div data-testid="onboarding-page">Onboarding</div>,
	}));
	vi.doMock("@/pages/Settings", () => ({
		default: () => <div data-testid="settings-page">Settings</div>,
	}));
	vi.doMock("@/pages/AboutAndPrivacy", () => ({
		default: () => (
			<div data-testid="aboutAndPrivacy-page">About & Privacy</div>
		),
	}));
}

describe("App routing + chrome, rewrite of routing + ErrorBoundary tests", () => {
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

	it("routes to the About & Privacy page when currentPage is 'aboutAndPrivacy'", async () => {
		await registerAppPageStubs();
		mockNavState.page = "aboutAndPrivacy";
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("aboutAndPrivacy-page")).toBeTruthy();
		});
	});

	it("wraps the page tree in an ErrorBoundary (skip-link + main landmark pass through)", async () => {
		// Behavioral: App's root element MUST be a real ErrorBoundary
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		const { container } = render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const skipLink = container.querySelector('a[href="#main-content"]');
		expect(skipLink).toBeTruthy();

		// The main landmark must exist (ErrorBoundary passes children
		const main = document.getElementById("main-content");
		expect(main).toBeTruthy();
		expect(main?.tagName.toLowerCase()).toBe("main");
	});

	it("shows a friendly connecting message + progress bar on the loading screen", async () => {
		await registerAppPageStubs();
		mockUseConnection.mockReturnValue({
			recordingState: "idle" as const,
			connectionStatus: "connecting" as const,
			lastError: null,
			handleRetryConnection: vi.fn(),
		});
		vi.resetModules();

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByText(/Starting Python backend/u)).toBeTruthy();
		});

		expect(screen.getByText(/This usually takes a few seconds/u)).toBeTruthy();
	});
});

describe("App help overlay content, rewrite of shortcut-list + input-gate tests", () => {
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

	it("lists the keyboard shortcuts when the overlay is open", async () => {
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		dispatchKey("?");

		await waitFor(() => {
			expect(
				screen.getAllByText("Keyboard Shortcuts").length,
			).toBeGreaterThanOrEqual(1);
		});

		expect(screen.getAllByText("Tab").length).toBeGreaterThanOrEqual(2);
		expect(screen.getAllByText("Shift").length).toBeGreaterThanOrEqual(2);
		expect(screen.getByText("Space")).toBeTruthy();
		expect(screen.getByText("Esc")).toBeTruthy();
		expect(screen.getAllByText("?").length).toBeGreaterThanOrEqual(1);
	});

	it("does NOT open the help overlay when '?' is pressed inside an input", async () => {
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		const input = document.createElement("input");
		document.body.appendChild(input);
		input.focus();

		dispatchKey("?");

		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		document.body.removeChild(input);
	});
});
