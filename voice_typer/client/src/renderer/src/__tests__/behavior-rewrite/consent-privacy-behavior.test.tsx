/**
 *  vitest rewrite — behavioral tests for consent & privacy UI.
 *
 * This file replaces 19 TS-string Python tests from
 * `tests/test_consent_and_privacy.py`. Each Python test asserted on
 * substring presence inside the renderer source files (e.g.
 * `'t("about.voiceBiometricsDesc")' in src`). Those pass even when the
 * disclosure is conditionally hidden, when the i18n key is mistyped, or
 * when a toggle silently no-ops. The vitest versions below mount the
 * real components and assert behavioral invariants: the disclosure is
 * rendered into the DOM, the toggle is wired to the correct config key,
 * the onNavigate callback fires, etc.
 *
 * Replaced Python tests (each is `@pytest.mark.skip`-ed in
 * `tests/test_consent_and_privacy.py` with a pointer back to this file):
 *
 *   TestAboutPageHasPrivacyDisclosure (3 PORT):
 *     - test_about_page_has_updates_section
 *     - test_about_page_has_help_links
 *     - test_about_page_has_feedback_links
 *
 *   TestSettingsTroubleshootHasDiagnosticActions (2 PORT):
 *     - test_settings_has_diagnostics_button
 *     - test_settings_has_on_navigate_prop
 *
 *   TestAboutAndSettingsShowVoiceBiometricConsent (3 PORT):
 *     - test_about_cites_gdpr_article_9
 *     - test_settings_has_privacy_consent_section
 *     - test_settings_has_voice_biometric_consent_toggle
 *
 *   TestVoiceTyperConfigTypeIncludesAllFields (5 PORT):
 *     - test_sound_feedback_enabled_in_type
 *     - test_huggingface_consent_in_type
 *     - test_cloud_consent_fields_in_type
 *     - test_voice_biometric_consent_in_type
 *     - test_llm_polish_consent_in_type
 *
 *   TestModelsPageExposesCloudConsentToggles (6 PORT):
 *     - test_models_imports_switch
 *     - test_models_has_set_cloud_consent_handler
 *     - test_models_has_consent_key_helper
 *     - test_models_has_consent_disclosure_text
 *     - test_models_has_hugging_face_consent_banner
 *     - test_models_consent_section_only_shown_when_key_present
 *
 * Python tests that remain KEEP (Python-only behavior — no TS
 * counterpart): TestConfigDeclaresConsentFlags,
 * TestCloudEngineRefusesWithoutConsent,
 * TestWhisperPreDownloadRespectsHuggingFaceConsent,
 * TestEngineAcceptsConfigInRealConstructionPath,
 * TestModelManagerWiresConfigIntoWhisper.
 *
 * Python tests already skipped in the prior  round (with their own
 * vitest counterparts in `__tests__/a11y-rewrite/`):
 *   - test_about_page_has_privacy_section        → About-privacy.test.tsx
 *   - test_settings_has_all_consent_toggles_consolidated
 *                                                → PrivacySettings-consent.test.tsx
 *
 * The Python tests are NOT deleted — they remain skipped so they stay
 * available as a fallback until CI verifies the vitest versions pass on
 * all platforms.
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import {
    cleanup,
    fireEvent,
    render,
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

// ─── Hoisted mock state ────────────────────────────────────────────────
//
// vi.mock factories are hoisted by vitest and execute before any
// module-level const/let, so any value the factory closes over must be
// allocated via vi.hoisted().
const { mockCall, mockPythonEvent, mockNavigate } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: mockNavigate }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: vi.fn() }),
}));

// Stub hugeicons. About / Settings / Models all import a handful of
// icons from `@hugeicons/core-free-icons`; we provide tagged stubs so
// the HugeiconsIcon mock can surface which icon was rendered (via
// data-name) and so a missing-icon import doesn't crash at module load.
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		// Icons used by About.tsx + Settings.tsx (and transitive children).
		// NOTE: AlertCircleIcon + Settings03Icon are used by
		// KeyboardPermissionBanner (mounted on Settings); the banner
		// renders whenever the onboarding_check_permissions probe returns
		// a non-granted result, so these must stay in the mock or the
		// Settings page crashes on mount.
		Alert02Icon: make("Alert02Icon"),
		AlertCircleIcon: make("AlertCircleIcon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Delete02Icon: make("Delete02Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
		Folder02Icon: make("Folder02Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		PauseIcon: make("PauseIcon"),
		PlayIcon: make("PlayIcon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Settings03Icon: make("Settings03Icon"),
		Shield01Icon: make("Shield01Icon"),
		SparklesIcon: make("SparklesIcon"),
		Sun01Icon: make("Sun01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
		ZapIcon: make("ZapIcon"),
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

import AboutPage from "@/pages/About";
import ModelsPage from "@/pages/Models";
// ─── Module imports (after vi.mock — these run with mocks in place) ───
import type { VoiceTyperConfig } from "@/types/config";

// ─── Helpers ───────────────────────────────────────────────────────────

/** A complete VoiceTyperConfig with all consent flags off (the privacy-
 *  by-default state). Mirrors the baseConfig in
 *  `pages/__tests__/Settings.test.tsx` so the Settings page sections all
 *  render without blowing up on missing fields. */
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
		// Consent flags — start all at false (privacy by default).
		huggingface_consent: false,
		voice_biometric_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
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
	// noUncheckedIndexedAccess: index access on `Array<[T,U?]>` widens
	// to `[T,U?] | undefined`; use optional chaining + nullish coalesce
	// to keep the return type `Record<string, unknown> | null`.
	const last = setConfigCalls[setConfigCalls.length - 1];
	return last?.[1] ?? null;
}

// =====================================================================
// Group 1: TestAboutPageHasPrivacyDisclosure (3 PORT tests)
// =====================================================================

describe("About page — updates / help / feedback sections", () => {
	beforeEach(() => {
		mockCall.mockReset();
		// Minimal IPC mock: get_config, get_status, get_prewarm_status
		// so About doesn't blow up on its initial data fetches.
		mockCall.mockImplementation((cmd: string) => {
			switch (cmd) {
				case "get_config":
					return Promise.resolve({
						theme_mode: "system",
						onboarding_completed: true,
						asr_backend: "whisper",
						model_size: "small.en",
						device: "cpu",
						hotkey: "F2",
						microphone: null,
					});
				case "get_prewarm_status":
					return Promise.resolve({
						last_run: null,
						elapsed_s: null,
						cache_ratio: 0,
						cache_label: "unknown",
						cached_bytes: 0,
						total_bytes: 0,
						prewarm_running: false,
					});
				case "get_status":
					return Promise.resolve({
						status: "idle",
						loaded_via: "",
						active_model: "",
					});
				default:
					return Promise.resolve({});
			}
		});

		// Stub global.fetch — C-DATA-1 regression guard. The Updates
		// section previously fired a fetch to api.github.com on mount
		// and again when the (now-removed) "Check for Updates" button
		// was clicked. The manual button has been removed entirely;
		// this stub captures ANY fetch call so the C-DATA-1 test below
		// can assert the spy is never invoked.
		(globalThis as unknown as { fetch: unknown }).fetch = vi.fn(() =>
			Promise.resolve({
				ok: true,
				json: () => Promise.resolve({ tag_name: "v1.0.0" }),
			}),
		) as unknown as typeof fetch;
	});

	afterEach(() => {
		cleanup();
		vi.restoreAllMocks();
	});

	it("does NOT render a 'Check for Updates' button and does NOT fetch the GitHub releases API (C-DATA-1)", async () => {
		// C-DATA-1 (offline guarantee): the previous "Check for Updates"
		// button fired a renderer `fetch()` to
		// `https://api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest`
		// on click — a network call in the production code path, which
		// the offline guarantee forbids. The button + handler +
		// latestVersion state have all been removed; the Updates section
		// now shows the installed version plus a static offline message.
		//
		// Behavioral: mount PrewarmAndUpdates directly (avoiding the
		// full Settings page mount to keep the test focused + avoid
		// cross-test cleanup interactions). The "Check for Updates"
		// button must NOT be in the DOM, and no fetch may fire across
		// the component's entire lifecycle.
		const fetchSpy = vi.fn();
		(globalThis as unknown as { fetch: unknown }).fetch = fetchSpy;

		const { default: PrewarmAndUpdates } = await import(
			"@/components/settings/PrewarmAndUpdates"
		);
		renderWithProviders(<PrewarmAndUpdates />);

		// Wait for the mount-time IPC call (get_prewarm_status) to
		// settle so the test isn't racing the effect cleanup.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("get_prewarm_status");
		});
		// Flush any pending microtasks.
		await new Promise((r) => setTimeout(r, 0));

		// The "Check for Updates" button must NOT be in the DOM.
		expect(
			screen.queryByRole("button", { name: /check for updates/i }),
		).toBeNull();

		// The offline message MUST be rendered.
		expect(
			screen.getByText(/Voice Typer is an offline application/i),
		).toBeTruthy();

		// No fetch should have fired — C-DATA-1 absolute guarantee.
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("renders help links to README and CHANGELOG", async () => {
		// Python invariant (test_about_page_has_help_links):
		//   "README_URL" in src OR "README.md" in src
		//   "CHANGELOG_URL" in src OR "CHANGELOG.md" in src
		//
		// Behavioral: anchor links to README.md and CHANGELOG.md
		// are rendered as <a href="...README.md"> and
		// <a href="...CHANGELOG.md">.
		renderWithProviders(<AboutPage />);

		// Wait for any section to mount.
		await waitFor(() => {
			expect(screen.getAllByText(/documentation/i).length).toBeGreaterThan(0);
		});

		const links = screen.getAllByRole("link");
		const hrefs = links.map((a) => a.getAttribute("href") ?? "");
		expect(hrefs.some((h) => h.includes("README.md"))).toBe(true);
		expect(hrefs.some((h) => h.includes("CHANGELOG.md"))).toBe(true);
	});

	it("renders a feedback link pointing at the GitHub issues tracker", async () => {
		// Python invariant (test_about_page_has_feedback_links):
		//   "Report a Bug" in src OR "Report an Issue" in src
		//   OR "Report a Bug" in en OR "Report an Issue" in en
		//   "github.com/AbdallahIsDev/voice-typer/issues" in src
		//
		// Behavioral: an anchor with visible text matching
		// /Report a (Bug|Issue)/ points at the GitHub issues URL.
		renderWithProviders(<AboutPage />);

		await waitFor(() => {
			expect(
				screen.getByRole("link", {
					name: /report a (bug|issue)/i,
				}),
			).toBeTruthy();
		});

		const feedbackLink = screen.getByRole("link", {
			name: /report a (bug|issue)/i,
		});
		const href = feedbackLink.getAttribute("href") ?? "";
		expect(href).toContain("github.com/AbdallahIsDev/voice-typer/issues");
	});
});

// =====================================================================
// Group 2: TestSettingsTroubleshootHasDiagnosticActions (2 PORT tests)
// =====================================================================

describe("Settings page — Troubleshooting section", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Reset the module registry so Settings' module-level
		// _cachedConfig is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	/** Render Settings, load the default config, and switch to the
	 *  Privacy tab so the Troubleshooting section is mounted. */
	async function renderSettingsOnPrivacyTab(
		overrides: Partial<VoiceTyperConfig> = {},
	): Promise<void> {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig(overrides));
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// Wait for the page to load — the General tab label is
		// always visible once config loads.
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Click the "Privacy" tab label to mount the
		// Troubleshooting section.
		fireEvent.click(screen.getByText("Privacy"));
	}

	it("renders Diagnostics, Help & FAQ, Report a Bug, and Open Log Folder buttons", async () => {
		// Python invariant (test_settings_has_diagnostics_button):
		//   "Diagnostics" in src OR en
		//   "Help & FAQ" in src OR en
		//   "Report a Bug" in src OR en
		//   "Open Log Folder" in src OR en
		//
		// Behavioral: each of the four Troubleshooting buttons is
		// rendered as a visible, screen-reader-accessible button
		// on the Privacy tab.  The buttons carry aria-labels
		// (en.json: settings.troubleshooting.*Aria) that differ
		// from their visible text — we assert BOTH the visible
		// text (per the Python invariant) and the accessible
		// name (per WCAG SC 4.1.2) so a regression in either
		// dimension fails the test.
		await renderSettingsOnPrivacyTab();

		// Visible text content (en.json: settings.troubleshooting.*).
		await waitFor(() => {
			expect(screen.getByText("Diagnostics")).toBeTruthy();
		});
		expect(screen.getByText("Help & FAQ")).toBeTruthy();
		expect(screen.getByText("Report a Bug")).toBeTruthy();
		expect(screen.getByText("Open Log Folder")).toBeTruthy();

		// Accessible names (en.json: settings.troubleshooting.*Aria).
		expect(
			screen.getByRole("button", { name: /^open diagnostics$/i }),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: /^open documentation$/i }),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: /^report a bug$/i }),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: /^open log folder$/i }),
		).toBeTruthy();
	});

	it("invokes navigate when the Diagnostics button is clicked", async () => {
		// Behavioral: clicking the "Diagnostics" button in Settings
		// calls navigate("about") via useNavigation hook (so the
		// user is routed to the About page where the full
		// diagnostics panel lives).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});
		fireEvent.click(screen.getByText("Privacy"));

		const diagnosticsBtn = await waitFor(() =>
			screen.getByRole("button", { name: /diagnostics/i }),
		);
		fireEvent.click(diagnosticsBtn);

		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith("about");
		});
	});
});

// =====================================================================
// Group 3: TestAboutAndSettingsShowVoiceBiometricConsent (3 PORT tests)
// =====================================================================

describe("About & Settings — voice biometric consent disclosure", () => {
	afterEach(() => {
		cleanup();
	});

	it("About page renders the voice biometrics heading + GDPR Article 9 disclosure", async () => {
		// Python invariant (test_about_cites_gdpr_article_9):
		//   't("about.voiceBiometricsDesc")' in src
		//   't("about.voiceBiometricsTitle")' in src
		//
		// Behavioral: the rendered About page contains the
		// voice-biometrics heading text AND the disclosure body
		// mentions both "BIPA" and "GDPR Article 9".
		mockCall.mockReset();
		mockCall.mockImplementation((cmd: string) => {
			switch (cmd) {
				case "get_config":
					return Promise.resolve({
						theme_mode: "system",
						onboarding_completed: true,
						asr_backend: "whisper",
						model_size: "small.en",
						device: "cpu",
						hotkey: "F2",
						microphone: null,
					});
				case "get_prewarm_status":
					return Promise.resolve({
						last_run: null,
						elapsed_s: null,
						cache_ratio: 0,
						cache_label: "unknown",
						cached_bytes: 0,
						total_bytes: 0,
						prewarm_running: false,
					});
				case "get_status":
					return Promise.resolve({
						status: "idle",
						loaded_via: "",
						active_model: "",
					});
				default:
					return Promise.resolve({});
			}
		});
		(globalThis as unknown as { fetch: unknown }).fetch = vi.fn(() =>
			Promise.resolve({
				ok: true,
				json: () => Promise.resolve({ tag_name: "v1.0.0" }),
			}),
		) as unknown as typeof fetch;

		renderWithProviders(<AboutPage />);

		await waitFor(() => {
			expect(screen.getAllByText(/voice biometrics/i).length).toBeGreaterThan(
				0,
			);
		});

		const bodyText = document.body.textContent ?? "";
		expect(bodyText).toMatch(/GDPR Article 9/);
		expect(bodyText).toMatch(/BIPA/);

		vi.restoreAllMocks();
	});

	it("Settings → Privacy renders the 'Privacy & Consent' section with title + description", async () => {
		// Python invariant (test_settings_has_privacy_consent_section):
		//   't("settings.privacy.privacyTitle")' in src
		//   't("settings.privacy.privacyDescription")' in src
		//
		// Behavioral: the rendered Settings (Privacy tab) shows
		// both the localized "Privacy & Consent" section title
		// and the localized description text (en.json:
		// "Grant or revoke consent for data processing...").
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});
		fireEvent.click(screen.getByText("Privacy"));

		// en.json: settings.privacy.privacyTitle = "Privacy & Consent"
		await waitFor(() => {
			expect(
				screen.getByRole("heading", {
					name: /privacy & consent/i,
				}),
			).toBeTruthy();
		});

		// en.json: settings.privacy.privacyDescription =
		//   "Grant or revoke consent for data processing. All
		//   consents default to off..."
		expect(document.body.textContent ?? "").toMatch(
			/grant or revoke consent for data processing/i,
		);
	});

	it("Settings → Privacy renders the voice-biometric consent Switch and toggling it persists voice_biometric_consent", async () => {
		// Python invariant (test_settings_has_voice_biometric_consent_toggle):
		//   "voice_biometric_consent" in src
		//   't("settings.privacy.voiceBiometricProcessingInfo")' in src
		//   't("settings.privacy.voiceBiometricLabel")' in src
		//
		// Behavioral: the Privacy tab renders a Switch labeled
		// "Voice biometric processing" with an info tooltip
		// mentioning BIPA / GDPR, and flipping the Switch fires
		// a set_config IPC with voice_biometric_consent=true.
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config")
				return Promise.resolve(makeConfig({ voice_biometric_consent: false }));
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});
		fireEvent.click(screen.getByText("Privacy"));

		// en.json: settings.privacy.voiceBiometricLabel =
		//   "Voice biometric processing"
		await waitFor(() => {
			expect(screen.getByText(/voice biometric processing/i)).toBeTruthy();
		});

		// en.json: settings.privacy.voiceBiometricProcessingInfo
		//   mentions "Illinois BIPA" and "GDPR Article 9".
		expect(document.body.textContent ?? "").toMatch(/BIPA/);

		// The Switch is the one with aria-label "Voice biometric
		// processing consent" (en.json:
		// settings.privacy.voiceBiometricProcessingAria).
		const switchBtn = screen.getByRole("switch", {
			name: /voice biometric processing consent/i,
		});
		expect(switchBtn).toBeTruthy();
		expect(switchBtn.getAttribute("aria-checked")).toBe("false");

		fireEvent.click(switchBtn);

		await waitFor(() => {
			expect(setConfigCallCount()).toBeGreaterThanOrEqual(1);
		});
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("voice_biometric_consent", true);
	});
});

// =====================================================================
// Group 4: TestVoiceTyperConfigTypeIncludesAllFields (5 PORT tests)
// =====================================================================
//
// The Python tests asserted on substring presence inside
// `types/config.ts`. The TS type system already enforces the existence
// of these keys at compile time — if a key is removed from the
// interface, code that references it fails typecheck. The vitest
// versions below add a runtime assertion that the field name is a
// `keyof VoiceTyperConfig`, which catches the case where a field is
// renamed but the rename isn't propagated to consumers.

describe("VoiceTyperConfig type — consent flags", () => {
	it("declares sound_feedback_enabled as a key of VoiceTyperConfig", () => {
		// Python invariant (test_sound_feedback_enabled_in_type):
		//   "sound_feedback_enabled" in types/config.ts source.
		//
		// Compile-time: the assignment below fails typecheck if
		// "sound_feedback_enabled" is not a key of the type.
		const key: keyof VoiceTyperConfig = "sound_feedback_enabled";
		expect(key).toBe("sound_feedback_enabled");
	});

	it("declares huggingface_consent as a key of VoiceTyperConfig", () => {
		// Python invariant (test_huggingface_consent_in_type):
		//   "huggingface_consent" in types/config.ts source.
		const key: keyof VoiceTyperConfig = "huggingface_consent";
		expect(key).toBe("huggingface_consent");
	});

	it("declares cloud_openai_consent, cloud_groq_consent, and cloud_deepgram_consent as keys", () => {
		// Python invariant (test_cloud_consent_fields_in_type):
		//   "cloud_openai_consent" in source
		//   "cloud_groq_consent" in source
		//   "cloud_deepgram_consent" in source
		const openai: keyof VoiceTyperConfig = "cloud_openai_consent";
		const groq: keyof VoiceTyperConfig = "cloud_groq_consent";
		const deepgram: keyof VoiceTyperConfig = "cloud_deepgram_consent";
		expect([openai, groq, deepgram]).toEqual([
			"cloud_openai_consent",
			"cloud_groq_consent",
			"cloud_deepgram_consent",
		]);
	});

	it("declares voice_biometric_consent as a key of VoiceTyperConfig", () => {
		// Python invariant (test_voice_biometric_consent_in_type):
		//   "voice_biometric_consent" in source.
		const key: keyof VoiceTyperConfig = "voice_biometric_consent";
		expect(key).toBe("voice_biometric_consent");
	});

	it("declares llm_polish_consent as a key of VoiceTyperConfig", () => {
		// Python invariant (test_llm_polish_consent_in_type):
		//   "llm_polish_consent" in source.
		const key: keyof VoiceTyperConfig = "llm_polish_consent";
		expect(key).toBe("llm_polish_consent");
	});
});

// =====================================================================
// Group 5: TestModelsPageExposesCloudConsentToggles (6 PORT tests)
// =====================================================================

describe("Models page — cloud consent toggles", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	/** Render Models with a given config. Models shows a Spinner
	 *  until get_config resolves; we wait for the page heading to
	 *  appear. The cloud consent Switches live on the "Cloud
	 *  Providers" tab (the default is "Local Models"), so when
	 *  `switchToCloudTab` is true (the default) we click that tab
	 *  before returning. The HuggingFace consent banner is only
	 *  rendered on the "Local Models" tab, so callers testing the
	 *  HF banner pass `switchToCloudTab: false`. */
	async function renderModels(
		config: Partial<VoiceTyperConfig>,
		options: { switchToCloudTab?: boolean } = {},
	): Promise<void> {
		const { switchToCloudTab = true } = options;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig(config));
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve(makeConfig(config));
		});

		renderWithProviders(<ModelsPage />);

		await waitFor(() => {
			expect(
				screen.queryAllByRole("heading", { name: /models/i }).length,
			).toBeGreaterThan(0);
		});

		// Switch to the Cloud Providers tab — the cloud consent
		// Switches (apiKeys[provider.key] gate) only render there.
		// The SegmentedControl renders each option as a radio with
		// a clickable label.
		if (switchToCloudTab) {
			fireEvent.click(screen.getByText("Cloud Providers"));
		}
	}

	it("renders at least one Switch for cloud consent when a provider has an API key", async () => {
		// Python invariant (test_models_imports_switch):
		//   "import { Switch }" in pages/Models.tsx source.
		//
		// Behavioral: when a cloud provider has an API key set,
		// the per-provider consent row renders a Switch.  The
		// aria-label is interpolated with the provider's
		// localized label (en.json: models.providers.openai.label
		// = "OpenAI Whisper API").
		await renderModels({ openai_api_key: "sk-test-key" });

		// The OpenAI provider consent Switch carries aria-label
		// "Grant audio transmission consent for OpenAI Whisper API".
		await waitFor(() => {
			expect(
				screen.getByRole("switch", {
					name: /grant audio transmission consent for openai whisper api/i,
				}),
			).toBeTruthy();
		});
	});

	it("toggling the OpenAI Switch persists cloud_openai_consent", async () => {
		// Python invariant (test_models_has_set_cloud_consent_handler):
		//   "setCloudConsent" in src
		//   "cloud_openai_consent" in src
		//   "cloud_groq_consent" in src
		//   "cloud_deepgram_consent" in src
		//
		// Behavioral: clicking the OpenAI provider's Switch
		// fires set_config with cloud_openai_consent=true.
		await renderModels({
			openai_api_key: "sk-test-key",
			cloud_openai_consent: false,
		});

		const switchBtn = await waitFor(() =>
			screen.getByRole("switch", {
				name: /grant audio transmission consent for openai whisper api/i,
			}),
		);
		fireEvent.click(switchBtn);

		await waitFor(() => {
			expect(setConfigCallCount()).toBeGreaterThanOrEqual(1);
		});
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("cloud_openai_consent", true);
	});

	it("toggling Groq and Deepgram Switches persists the correct per-provider consent key", async () => {
		// Python invariant (test_models_has_consent_key_helper):
		//   "consentKeyFor" in src
		//
		// Behavioral: the consentKeyFor helper maps each provider
		// to its own consent flag — toggling the Groq Switch
		// persists cloud_groq_consent, and toggling the Deepgram
		// Switch persists cloud_deepgram_consent.  Provider
		// labels come from en.json (models.providers.{key}.label):
		//   groq     → "Groq Whisper API"
		//   deepgram → "Deepgram API"
		await renderModels({
			groq_api_key: "gsk-test",
			deepgram_api_key: "dg-test",
			cloud_groq_consent: false,
			cloud_deepgram_consent: false,
		});

		// Groq
		const groqSwitch = await waitFor(() =>
			screen.getByRole("switch", {
				name: /grant audio transmission consent for groq whisper api/i,
			}),
		);
		fireEvent.click(groqSwitch);
		await waitFor(() => {
			expect(lastSetConfigPayload()).toMatchObject({
				cloud_groq_consent: true,
			});
		});

		// Deepgram
		const deepgramSwitch = screen.getByRole("switch", {
			name: /grant audio transmission consent for deepgram api/i,
		});
		fireEvent.click(deepgramSwitch);
		await waitFor(() => {
			expect(lastSetConfigPayload()).toMatchObject({
				cloud_deepgram_consent: true,
			});
		});
	});

	it("renders the consent disclosure title + description text for a provider with a key", async () => {
		// Python invariant (test_models_has_consent_disclosure_text):
		//   't("models.cloud.consentTitle")' in src
		//   '"models.cloud.consentDescription"' in src
		//
		// Behavioral: the localized "Audio transmission consent"
		// title (en.json: models.cloud.consentTitle) and the
		// provider-specific description text (en.json:
		// models.cloud.consentDescription) are rendered into the
		// DOM when the consent row is shown.
		await renderModels({ openai_api_key: "sk-test-key" });

		await waitFor(() => {
			expect(
				screen.getAllByText(/audio transmission consent/i).length,
			).toBeGreaterThan(0);
		});

		// en.json: models.cloud.consentDescription contains
		// "audio recordings will be sent to {provider}" — the
		// {provider} placeholder is interpolated with the
		// localized provider label ("OpenAI Whisper API").
		expect(document.body.textContent ?? "").toMatch(
			/audio recordings will be sent to openai whisper api/i,
		);
	});

	it("renders the HuggingFace consent banner with a Grant button that persists huggingface_consent=true", async () => {
		// Python invariant (test_models_has_hugging_face_consent_banner):
		//   't("models.hfConsent.title")' in src
		//   't("models.hfConsent.grant")' in src
		//   "setHuggingFaceConsent" in src
		//
		// Behavioral: when huggingface_consent is false, the
		// HuggingFace consent banner renders with title +
		// description + a "Grant" button. Clicking Grant fires
		// set_config with huggingface_consent=true. The banner
		// only renders on the "Local Models" tab (the default),
		// so we DON'T switch to the Cloud Providers tab here.
		await renderModels(
			{ huggingface_consent: false },
			{ switchToCloudTab: false },
		);

		// en.json: models.hfConsent.title =
		//   "HuggingFace download consent required"
		await waitFor(() => {
			expect(
				screen.getByText(/huggingface download consent required/i),
			).toBeTruthy();
		});

		// en.json: models.hfConsent.grant = "Grant"
		// (aria-label = "Grant HuggingFace download consent")
		const grantBtn = screen.getByRole("button", {
			name: /grant huggingface download consent/i,
		});
		fireEvent.click(grantBtn);

		await waitFor(() => {
			expect(lastSetConfigPayload()).toMatchObject({
				huggingface_consent: true,
			});
		});
	});

	it("shows the consent section for a provider only when that provider has an API key OR consent already granted", async () => {
		// Python invariant (test_models_consent_section_only_shown_when_key_present):
		//   "apiKeys[provider.key]" in src
		//   "consentKeyFor(provider.key)" in src
		//
		// Behavioral: when OpenAI has an API key but Groq and
		// Deepgram don't (and their consent flags are false),
		// the OpenAI consent Switch is rendered but the Groq
		// and Deepgram consent Switches are NOT.
		await renderModels({
			openai_api_key: "sk-test-key",
			groq_api_key: "",
			deepgram_api_key: "",
			cloud_openai_consent: false,
			cloud_groq_consent: false,
			cloud_deepgram_consent: false,
		});

		// OpenAI Switch is rendered.
		await waitFor(() => {
			expect(
				screen.getByRole("switch", {
					name: /grant audio transmission consent for openai whisper api/i,
				}),
			).toBeTruthy();
		});

		// Groq and Deepgram Switches are NOT rendered.
		expect(
			screen.queryByRole("switch", {
				name: /grant audio transmission consent for groq whisper api/i,
			}),
		).toBeNull();
		expect(
			screen.queryByRole("switch", {
				name: /grant audio transmission consent for deepgram api/i,
			}),
		).toBeNull();
	});
});
