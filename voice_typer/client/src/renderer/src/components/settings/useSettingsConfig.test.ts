/**
 * Tests for `useSettingsConfig` covering the  fixes:
 *
 *  - : debounced text-field saves are flushed on unmount +
 *    `beforeunload` (no longer silently dropped when the user navigates
 *    away or quits the app within the 500ms debounce window).
 *  - : the backend's specific validator text is surfaced in the
 *    error snack (instead of the generic "Failed to save setting").
 *  - : partial-success `model_errors` envelope is surfaced as
 *    a warning (instead of being silently swallowed with "Saved ✓").
 *  - : rejected (unknown) keys are surfaced as a warning
 *    (instead of being silently dropped with "Saved ✓").
 *  - : a failed save does NOT call `loadConfig()` immediately
 *    (the user's attempted value is retained for edit + retry).
 */
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	pythonMock,
	resetStableMocks,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, showSnack: mockShowSnack } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("@/stores/appStore", () => ({
	useAppStore: {
		getState: () => ({ mergeConfig: vi.fn(), setConfig: vi.fn() }),
	},
}));

import { useSettingsConfig } from "@/components/settings/useSettingsConfig";
import type { VoiceTyperConfig } from "@/types/config";

const baseConfig: VoiceTyperConfig = {
	schema_version: 1,
	fast_startup: true,
	offline_pack_consent: false,
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
	theme_preset: "default",
	custom_theme: null,
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

function setConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	).length;
}

function lastSetConfigPayload(): Record<string, unknown> | null {
	const setConfigCalls = mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	) as Array<[string, Record<string, unknown>?]>;
	if (setConfigCalls.length === 0) return null;
	const last = setConfigCalls[setConfigCalls.length - 1];
	return last?.[1] ?? null;
}

describe("useSettingsConfig — XA-14 fixes", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("XA-14-1: flushes pending debounced value on unmount instead of dropping it", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ type: "ack" });
			return Promise.resolve({});
		});

		const { result, unmount } = renderHook(() => useSettingsConfig());

		// The hook does NOT auto-load on mount — the consumer
		// (Settings.tsx) calls loadConfig() in a useEffect.
		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		// Type into a debounced field — the value is mirrored
		// into pendingDebouncedValuesRef but the timer hasn't
		// fired yet (delayMs=500).
		result.current.updateConfigDebounced("llm_api_key", "sk-test-123", 500);

		// The IPC call hasn't fired yet (still inside the
		// 500ms debounce window).
		expect(setConfigCallCount()).toBe(0);

		// Unmount BEFORE the debounce timer fires — pre-fix
		// the value would be dropped. Post-fix, the unmount
		// cleanup merges pendingDebouncedValuesRef into
		// pendingUpdatesRef and flushes synchronously.
		unmount();

		// The flush is fire-and-forget; wait for the set_config
		// call to land.
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("llm_api_key", "sk-test-123");
	});

	it("XA-14-2: surfaces the backend's specific validator text in the error snack", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config")
				return Promise.reject(
					new Error(
						"field 'history_max_entries' must be in [10, 1000000], got 5",
					),
				);
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		await result.current.updateConfig({ history_max_entries: 5 });

		// The snack must include the backend's specific message,
		// not just the generic "Failed to save setting" text.
		expect(mockShowSnack).toHaveBeenCalledWith(
			expect.stringContaining(
				"field 'history_max_entries' must be in [10, 1000000], got 5",
			),
			"error",
		);
		//5: the error state is populated with the
		// backend's message so a downstream indicator can
		// render a "Save failed" state. Wrap in waitFor because
		// React 19 batches state updates from async callbacks
		// and the re-render may not have flushed by the time
		// the await resolves.
		await waitFor(() => {
			expect(result.current.error).toContain("history_max_entries");
		});
	});

	it("XA-14-3: surfaces partial-success model_errors as a warning snack", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config")
				return Promise.resolve({
					type: "ack",
					data: {
						status: "partial",
						model_errors: [
							{
								code: "model_switch_failed",
								field: "model_size",
								message: "Model switch failed",
							},
						],
						applied: ["hotkey"],
					},
				});
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		await result.current.updateConfig({
			model_size: "qwen",
			hotkey: "F3",
		});

		// Warning (not error) snack must mention the failing
		// field so the user knows the model swap didn't take.
		expect(mockShowSnack).toHaveBeenCalledWith(
			expect.stringContaining("model_size"),
			"warning",
		);
		await waitFor(() => {
			expect(result.current.error).toContain("model_size");
		});
	});

	it("XA-14-4: surfaces rejected (unknown) keys as a warning snack", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config")
				return Promise.resolve({
					type: "ack",
					data: {
						accepted: ["hotkey"],
						rejected: ["onboarding_completed"],
					},
				});
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		await result.current.updateConfig({
			onboarding_completed: false,
			hotkey: "F4",
		});

		expect(mockShowSnack).toHaveBeenCalledWith(
			expect.stringContaining("onboarding_completed"),
			"warning",
		);
		await waitFor(() => {
			expect(result.current.error).toContain("onboarding_completed");
		});
	});

	it("XA-14-9: a failed save does NOT auto-reload the config (attempted value retained for retry)", async () => {
		const getConfigSpy = vi.fn(() => Promise.resolve(baseConfig));
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return getConfigSpy();
			if (type === "set_config")
				return Promise.reject(new Error("validation failed"));
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		const initialGetConfigCalls = getConfigSpy.mock.calls.length;

		await result.current.updateConfig({ history_max_entries: 5 });

		// No additional get_config call should fire after the
		// save failure — pre-fix the catch block called
		// loadConfig() which silently overwrote the user's
		// attempted value with the backend's old value.
		expect(getConfigSpy.mock.calls.length).toBe(initialGetConfigCalls);

		// The local state retains the user's attempted value
		// so they can edit + retry without retyping. Wrap in
		// waitFor because the post-error re-render may not
		// have flushed by the time the await resolves.
		await waitFor(() => {
			expect(result.current.config?.history_max_entries).toBe(5);
		});
	});

	it("XA-14-6: hasPendingOrSaving is true while a debounced write is queued", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ type: "ack" });
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		expect(result.current.hasPendingOrSaving).toBe(false);

		// Schedule a debounced write — pending becomes true
		// immediately, so hasPendingOrSaving should be true.
		// Wrap in act because updateConfigDebounced's setPending
		// is a state update from outside a React event handler
		// and the re-render needs to be flushed before the
		// assertion.
		act(() => {
			result.current.updateConfigDebounced("llm_api_key", "sk-test", 500);
		});

		expect(result.current.hasPendingOrSaving).toBe(true);
	});
});

describe("useSettingsConfig — initial load failure surfaces loadError", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("sets loadError when get_config rejects", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config")
				return Promise.reject(new Error("backend unreachable"));
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();

		await waitFor(() => {
			expect(result.current.loadError).toBe("backend unreachable");
		});
		// NOTE: `config` itself may be non-null here — the hook seeds it
		// from its module-level cache (populated by earlier successful
		// loads in this file), which is exactly why the Settings page
		// only falls into its error branch when NO cached config exists.
	});

	it("clears loadError on a subsequent successful load", async () => {
		let failGetConfig = true;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") {
				return failGetConfig
					? Promise.reject(new Error("backend unreachable"))
					: Promise.resolve(baseConfig);
			}
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useSettingsConfig());

		await result.current.loadConfig();
		await waitFor(() => {
			expect(result.current.loadError).toBe("backend unreachable");
		});

		failGetConfig = false;
		await result.current.loadConfig();

		await waitFor(() => {
			expect(result.current.loadError).toBeNull();
		});
		await waitFor(() => {
			expect(result.current.config?.model_size).toBe("tiny");
		});
	});
});
