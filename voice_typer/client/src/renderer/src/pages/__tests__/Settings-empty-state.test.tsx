/**
 * FIX-14 — UX-18 regression test: Settings search empty state.
 *
 * When the user types a search query that matches no row on the active
 * tab, the Settings page must render an empty-state banner with the
 * i18n string "No settings match \"{query}\"" so the user knows the
 * search ran but found nothing (instead of staring at a blank tab).
 *
 * The sentinel `hasAnyVisibleRow` is computed by lifting the per-section
 * visibility calls via the `_filter_settings` function — it bumps a
 * render-phase counter on each positive match, and a layout effect reads
 * the counter to derive the boolean state. This test verifies the
 * end-to-end behaviour (typing a non-matching query shows the banner;
 * clearing the query hides it) without depending on the internal
 * counter mechanism.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoist the mock call/event handlers so they're available inside the
// vi.mock factory (which is hoisted to the top of the file by vitest
// and runs before any other code).
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
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

// Stub every icon used by Settings + its transitive children.
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Delete01Icon: make("Delete01Icon"),
		File02Icon: make("File02Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Sun01Icon: make("Sun01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
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

import type { VoiceTyperConfig } from "@/types/config";

/** Minimal valid config — same shape as Settings.test.tsx's baseConfig. */
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
} as VoiceTyperConfig;

describe("UX-18: Settings search empty state", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the 'No settings match' banner when the query matches no row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		// Wait for the page to load (the tab labels are always visible).
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Type a nonsense query that matches no row label/info/section-title
		// on any tab. Use a long random string to avoid coincidental matches
		// against translated labels.
		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		fireEvent.change(searchInput, { target: { value: "zzzqqqxxxyyy999" } });

		// UX-18: the empty-state banner must appear, interpolating the
		// query into the i18n string "No settings match \"{query}\"".
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});
	});

	it("hides the 'No settings match' banner when the query is cleared", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		// Type a non-matching query.
		fireEvent.change(searchInput, { target: { value: "zzzqqqxxxyyy999" } });
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});

		// Clear the query — the banner must disappear.
		fireEvent.change(searchInput, { target: { value: "" } });
		await waitFor(() => {
			expect(
				screen.queryByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeNull();
		});
	});

	it("does NOT render the banner when the query is empty (initial state)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// No banner on initial render (empty query → no search active).
		expect(screen.queryByText(/No settings match/)).toBeNull();
	});

	it("does NOT render the banner when the query matches a row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		// "appearance" matches the Appearance tab hint AND the Appearance
		// section title — so it's a positive match (no banner).
		fireEvent.change(searchInput, { target: { value: "appearance" } });

		// Wait a tick for the layout effect to settle.
		await new Promise((r) => setTimeout(r, 50));

		expect(screen.queryByText(/No settings match/)).toBeNull();
	});
});
