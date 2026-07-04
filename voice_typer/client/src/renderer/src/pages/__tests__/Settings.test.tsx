/**
 * Tests for the Settings page — batched config writes (PERF-002).
 *
 * The Settings page owns the `updateConfig` / `updateConfigDebounced`
 * callbacks which persist config changes to the Python backend via the
 * `set_config` IPC.  PERF-002 batches writes so multiple rapid changes
 * within a debounce window collapse into a single `set_config` call,
 * avoiding redundant IPC traffic (the backend's `set_config` accepts a
 * partial dict — see IPC_CONFIG_ALLOWLIST — so a single call can carry
 * any number of changed keys).
 *
 * We mock the Python bridge, the hugeicons renderer, sonner, and
 * next-themes.  The Radix-UI-based Select/Switch/Tooltip components are
 * left un-mocked because jsdom supports them well enough for the color
 * picker interactions exercised here.
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

// Stub every icon used by Settings + its transitive children (SearchField,
// HotkeyPicker, ui/select, PrivacySettingsSection, etc.) with `{ name }`
// tagged objects so the HugeiconsIcon mock can surface which icon was
// rendered via data-name.  Vitest's vi.mock requires named exports to be
// declared explicitly, so we enumerate the full set consumed by the
// Settings render graph.  (sonner / next-themes are mocked separately, so
// ui/sonner.tsx's icons aren't needed here.)
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
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

// sonner is imported transitively via useSnackbar → toast.  Stub it so
// the test doesn't depend on sonner's portal/DOM rendering.
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

// next-themes is imported by components/ui/sonner.tsx (which is pulled
// in via the Settings page's transitive import graph through useSnackbar
// → sonner).  Stub it so the test doesn't depend on next-themes' context
// provider.
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

import type { VoiceTyperConfig } from "@/types/config";

/** A complete, valid VoiceTyperConfig with `theme_preset: "custom"` so the
 *  color picker renders on first paint.  Only the theme-related fields are
 *  meaningful for these tests; the rest are populated with sensible
 *  defaults so the various Settings sections don't blow up on render. */
const baseConfig: VoiceTyperConfig = {
	schema_version: 1,
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
	high_contrast: false,
	text_size: 14,
	wayland_warned: false,
	silence_warning_seconds: 0,
	silence_auto_stop_seconds: 0,
	max_recording_seconds: 60,
	max_recording_seconds_gpu: 120,
	max_recording_seconds_cpu: 60,
	dead_air_timeout: 0,
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
	// P4: AI enhancement fields (off by default)
	ai_enhancement_enabled: false,
	auto_capitalize: true,
	auto_punctuate: true,
	fix_grammar_basics: true,
	// P5: Vocabulary automation fields (off by default)
	vocabulary_automation_enabled: false,
	vocabulary_auto_confidence_threshold: 0.7,
	vocabulary_auto_apply_threshold: 0.95,
};

/** Count `set_config` IPC calls captured by mockCall. */
function setConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	).length;
}

/** Return the payload of the most recent `set_config` call (or null). */
function lastSetConfigPayload(): Record<string, unknown> | null {
	const setConfigCalls = mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	) as Array<[string, Record<string, unknown>?]>;
	if (setConfigCalls.length === 0) return null;
	return setConfigCalls[setConfigCalls.length - 1][1] ?? null;
}

describe("Settings page — PERF-002 batched config writes", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Reset the module registry so Settings' module-level cache
		// (_cachedConfig) is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("mounts and loads config via get_config without firing set_config", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		// The Appearance section heading renders once config loads.
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Loading the config must NOT trigger a save — the
		// lastSavedConfigRef baseline is seeded in loadConfig so the
		// initial snapshot isn't re-persisted as a "change".
		expect(setConfigCallCount()).toBe(0);
	});

	it("batches 3 rapid color-picker changes into a single set_config call", async () => {
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

		// The color pickers live in the ThemeSettingsSection, which is
		// only rendered when the Appearance tab is active.  Click the
		// tab label to navigate there.
		fireEvent.click(screen.getByText("Appearance"));

		// Wait for ThemeSettingsSection to mount and render the color
		// pickers (it calls setCustomDraft during render which needs an
		// extra React pass to finalise).
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(3);
		});
		const colorInputs = document.querySelectorAll('input[type="color"]');

		// Change 3 colors in rapid succession — each change schedules a
		// 300ms per-key debounce via updateConfigDebounced("custom_theme", …).
		// All three use the same key ("custom_theme") so the per-key
		// debounce cancels and reschedules a single timer; when that
		// timer fires, updateConfig merges the final value into the
		// pending buffer and schedules a microtask flush.  The flush
		// sends ONE set_config call with { custom_theme: <latest> }.
		fireEvent.input(colorInputs[0], { target: { value: "#ff0000" } });
		fireEvent.input(colorInputs[1], { target: { value: "#00ff00" } });
		fireEvent.input(colorInputs[2], { target: { value: "#0000ff" } });

		// Wait for the 300ms per-key debounce + microtask flush +
		// set_config IPC to complete.  waitFor polls (flushing
		// microtasks between polls) until the assertion passes or the
		// timeout (default 1000ms) elapses.
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		// The single set_config payload must carry the custom_theme
		// key (the diff against lastSavedConfigRef).
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});

	it("re-saves when a setting is reverted (diff is against the last saved value, not the original load)", async () => {
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

		// Navigate to the Appearance tab so the color pickers are visible.
		// The ThemeSettingsSection calls setCustomDraft during render, so we
		// wait for the color inputs to actually appear before proceeding.
		fireEvent.click(screen.getByText("Appearance"));
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		const colorInputs = document.querySelectorAll('input[type="color"]');

		// Capture the original hex value of the first color input, then
		// change it to something else, then change it back.  The PERF-002
		// diff is computed against `lastSavedConfigRef` (the last value
		// the backend confirmed), NOT the original config loaded at mount.
		// So after the first save updates the baseline to #abcdef,
		// reverting to the original hex is still a real diff and triggers
		// a second set_config call.  This documents that behaviour so
		// future refactors don't accidentally start diffing against the
		// initial load (which would silently drop reverts).
		const originalValue = (colorInputs[0] as HTMLInputElement).value;

		// Change to a different color and wait for the debounced save.
		fireEvent.input(colorInputs[0], { target: { value: "#abcdef" } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		// Change back to the original color — the baseline now has
		// #abcdef, so this is a non-empty diff and a second set_config
		// fires carrying the reverted custom_theme.
		fireEvent.input(colorInputs[0], { target: { value: originalValue } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(2);
		});

		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});
});
