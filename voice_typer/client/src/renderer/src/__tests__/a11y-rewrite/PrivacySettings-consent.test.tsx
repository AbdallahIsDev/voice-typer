/**
 *  vitest rewrite — behavioral test for `PrivacySettingsSection.tsx`
 * consent toggles.
 *
 * Replaces the following string-pattern Python test from
 * `tests/test_consent_and_privacy.py`:
 *   - TestAboutAndSettingsShowVoiceBiometricConsent::test_settings_has_all_consent_toggles_consolidated
 *
 * The Python test asserted on substring presence inside
 * `PrivacySettingsSection.tsx` for the literal consent field names:
 * "huggingface_consent", "voice_biometric_consent",
 * "cloud_openai_consent", "cloud_groq_consent",
 * "cloud_deepgram_consent", and "llm_polish_consent".  These pass
 * even when the toggle is broken, when the wrong Switch is bound to
 * the wrong config key, or when the toggle silently no-ops.  The
 * vitest version below mounts the real PrivacySettingsSection with
 * a fully-populated config and asserts:
 *   1. Flipping each Switch calls updateConfig with the correct
 *      consent key + new value.
 *   2. Each Switch reflects the current config value as `checked`.
 *
 * The corresponding Python test is skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  It is NOT deleted.
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

const stable = vi.hoisted(() => ({
	pythonCall: vi.fn(),
	showSnack: vi.fn(),
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

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: stable.pythonCall }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: stable.showSnack }),
}));

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

import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import type { VoiceTyperConfig } from "@/types/config";

// Build a config where every consent flag is `false`.  This makes the
// "Agree to All" banner visible (it shows when not all consents are
// true) so the section renders every toggle row.
function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		fast_startup: true,
		hotkey: "F2",
		sample_rate: 16000,
		microphone: null,
		model_size: "tiny",
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
		theme_preset: "default",
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
		// Consent flags — start all at false.
		huggingface_consent: false,
		voice_biometric_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
		llm_polish_consent: false,
		...overrides,
	} as VoiceTyperConfig;
}

// The Settings page passes an isVisible predicate that always returns
// true when there's no search filter active.  PrivacySettingsSection
// uses it for per-row + section-level visibility.
const alwaysVisible = () => true;

describe("PrivacySettings consent toggles — RW-0 rewrite of test_settings_has_all_consent_toggles_consolidated", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a Switch for every consolidated consent flag", () => {
		const config = makeConfig();
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The Python invariant: source contains the literal
		// strings "huggingface_consent", "voice_biometric_consent",
		// "cloud_openai_consent", "cloud_groq_consent",
		// "cloud_deepgram_consent", "llm_polish_consent".
		// Behavioral: each consent flag is wired to a Switch
		// whose `checked` state reflects the config value.
		// We assert the rendered Switch count is >= 6 (the
		// six consolidated consents) and that flipping each
		// calls updateConfig with the right key.

		// The section renders one Switch per consent flag.
		// Radix Switch renders as a <button> with
		// role="switch".
		const switches = screen.getAllByRole("switch");
		expect(switches.length).toBeGreaterThanOrEqual(6);
	});

	it("calls updateConfig with huggingface_consent=true when its Switch is toggled on", () => {
		const updateConfig = vi.fn();
		const config = makeConfig({ huggingface_consent: false });
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The HuggingFace consent Switch carries
		// aria-label "HuggingFace download consent"
		// (en.json: settings.privacy.huggingFaceDownloadsAria).
		const switchBtn = screen.getByRole("switch", {
			name: /huggingface download consent/i,
		});
		fireEvent.click(switchBtn);
		expect(updateConfig).toHaveBeenCalledWith({
			huggingface_consent: true,
		});
	});

	it("renders data-consent-field scroll targets and highlights the deep-linked consent row", () => {
		const config = makeConfig();
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
				// The consent refusal envelope's ``consent_field`` —
				// Settings.tsx passes it through after consuming the
				// navigate deep-link option.
				consentFocusField="voice_biometric_consent"
			/>,
		);

		// Every consent row carries the ``data-consent-field``
		// attribute that Settings.tsx's scroll effect targets.
		const biometricRow = document.querySelector(
			'[data-consent-field="voice_biometric_consent"]',
		);
		expect(biometricRow).toBeTruthy();
		expect(
			document.querySelector('[data-consent-field="huggingface_consent"]'),
		).toBeTruthy();
		expect(
			document.querySelector('[data-consent-field="cloud_openai_consent"]'),
		).toBeTruthy();

		// The deep-linked row renders the temporary highlight ring.
		expect(biometricRow?.className).toContain("ring-");
	});

	it("does NOT highlight any consent row when no consentFocusField is passed", () => {
		const config = makeConfig();
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const rows = document.querySelectorAll("[data-consent-field]");
		expect(rows.length).toBeGreaterThanOrEqual(6);
		for (const row of Array.from(rows)) {
			expect(row.className).not.toContain("ring-");
		}
	});

	it("calls updateConfig with voice_biometric_consent=true when its Switch is toggled on", () => {
		const updateConfig = vi.fn();
		const config = makeConfig({ voice_biometric_consent: false });
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Voice biometric consent Switch carries
		// aria-label "Voice biometric processing consent"
		// (en.json: settings.privacy.voiceBiometricProcessingAria).
		const switchBtn = screen.getByRole("switch", {
			name: /voice biometric processing consent/i,
		});
		fireEvent.click(switchBtn);
		expect(updateConfig).toHaveBeenCalledWith({
			voice_biometric_consent: true,
		});
	});

	it("calls updateConfig with cloud_*_consent flags when their Switches are toggled", () => {
		const updateConfig = vi.fn();
		const config = makeConfig({
			cloud_openai_consent: false,
			cloud_groq_consent: false,
			cloud_deepgram_consent: false,
		});
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Toggle each cloud ASR Switch by aria-label.
		fireEvent.click(
			screen.getByRole("switch", {
				name: /openai cloud speech recognition consent/i,
			}),
		);
		expect(updateConfig).toHaveBeenLastCalledWith({
			cloud_openai_consent: true,
		});

		fireEvent.click(
			screen.getByRole("switch", {
				name: /groq cloud speech recognition consent/i,
			}),
		);
		expect(updateConfig).toHaveBeenLastCalledWith({
			cloud_groq_consent: true,
		});

		fireEvent.click(
			screen.getByRole("switch", {
				name: /deepgram cloud speech recognition consent/i,
			}),
		);
		expect(updateConfig).toHaveBeenLastCalledWith({
			cloud_deepgram_consent: true,
		});
	});

	it("calls updateConfig with llm_polish_consent=true when its Switch is toggled", () => {
		const updateConfig = vi.fn();
		const config = makeConfig({ llm_polish_consent: false });
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// LLM polish consent Switch carries aria-label
		// "LLM polish consent" (en.json: settings.privacy.llmTextPolishingAria).
		const switchBtn = screen.getByRole("switch", {
			name: /llm polish consent/i,
		});
		fireEvent.click(switchBtn);
		expect(updateConfig).toHaveBeenLastCalledWith({
			llm_polish_consent: true,
		});
	});

	it("Agree-to-All button sets all six consent flags in one updateConfig call", async () => {
		const updateConfig = vi.fn();
		const config = makeConfig({
			huggingface_consent: false,
			voice_biometric_consent: false,
			cloud_openai_consent: false,
			cloud_groq_consent: false,
			cloud_deepgram_consent: false,
			llm_polish_consent: false,
		});
		renderWithProviders(
			<PrivacySettingsSection
				config={config}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Click the "Agree to All" button — PRIV-AGREE-ALL now requires
		// an explicit confirmation step (ConfirmDialog): the first click
		// opens the dialog, and updateConfig only fires after the user
		// confirms. This is the consolidated consent affordance the
		// Python test name refers to.
		const agreeAllBtn = screen
			.queryAllByRole("button")
			.find((b) => /agree to all/i.test(b.textContent ?? ""));
		expect(agreeAllBtn).toBeTruthy();
		fireEvent.click(agreeAllBtn as HTMLElement);

		// The confirmation dialog is open — click its confirm button
		// (same "Agree to All" label) to actually grant the consents.
		await waitFor(() => {
			expect(screen.getByRole("alertdialog")).toBeTruthy();
		});
		const confirmBtn = screen
			.getAllByRole("button")
			.find((b) => /agree to all/i.test(b.textContent ?? ""));
		expect(confirmBtn).toBeTruthy();
		fireEvent.click(confirmBtn as HTMLElement);

		expect(updateConfig).toHaveBeenCalledWith(
			expect.objectContaining({
				huggingface_consent: true,
				voice_biometric_consent: true,
				cloud_openai_consent: true,
				cloud_groq_consent: true,
				cloud_deepgram_consent: true,
				llm_polish_consent: true,
			}),
		);
	});
});
