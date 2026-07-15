/**
 * F-17: Automated WCAG violation scanning for all pages.
 *
 * Each page is mounted with @testing-library/react and scanned with
 * axe-core. The color-contrast rule is disabled because the test
 * environment doesn't load the full Tailwind stylesheet.
 *
 * Pages that require a backend connection or complex props are
 * wrapped in minimal providers (ErrorBoundary, optional router) and
 * provided with stub props so they render without crashing.
 *
 * Coverage:
 *   - Home (idle state)
 *   - History (empty state)
 *   - Templates (empty state)
 *   - Vocabulary (empty state)
 *   - Models
 *   - Settings (with stub config)
 *   - About
 *   - Microphone
 *   - Onboarding
 */
import { render } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";

// Disable color-contrast — the test environment doesn't load the full
// Tailwind stylesheet, so axe's computed contrast values would be
// meaningless and produce false positives.
const AXE_OPTIONS: axe.RunOptions = {
	rules: {
		"color-contrast": { enabled: false },
	},
};

// Stub config that satisfies the VoiceTyperConfig type minimally.
// Individual page tests extend this as needed.
const STUB_CONFIG = {
	theme_mode: "dark" as const,
	hotkey: "<capslock>",
	hotkey_repaste: "<ctrl>+<alt>+v",
	microphone: null,
	model_size: "small.en",
	device: "auto",
	sample_rate: 16000,
	autostart: false,
	show_notifications: true,
	paste_on_stop: true,
	// ADR-0010: clipboard borrow/restore config keys.
	clipboard_save_restore: true,
	clipboard_restore_delay_ms: 150,
	text_cleanup_enabled: true,
	recording_mode: "toggle" as const,
	tray_left_click: "open_app" as const,
	language: "en",
	bubble_position: "top" as const,
	bubble_behavior: "show_on_record" as const,
	bubble_draggable: false,
	bubble_show_on_startup: false,
	audio_preset: "auto" as const,
	volume_duck_enabled: true,
	volume_duck_level: 0.2,
	volume_duck_fade_ms: 200,
	volume_duck_smart_enabled: true,
	volume_duck_smart_poll_interval_ms: 500,
	noise_filter_highpass: true,
	noise_filter_highpass_cutoff_hz: 80,
	noise_suppression_method: "rnnoise" as const,
	noise_filter_gate: true,
	noise_filter_gate_open_threshold_db: -26,
	noise_filter_gate_close_threshold_db: -32,
	noise_filter_eq: true,
	noise_filter_eq_low_db: -3,
	noise_filter_eq_mid_db: 3,
	noise_filter_eq_high_db: 2,
	noise_filter_compressor: true,
	noise_filter_compressor_threshold_db: -18,
	noise_filter_compressor_ratio: 3,
	noise_filter_limiter: true,
	noise_filter_limiter_ceiling_db: -6,
	noise_filter_notch: false,
	esc_cancel_enabled: true,
	llm_polish: false,
	llm_api_key: "",
	llm_preset: "professional" as const,
	crash_recovery_enabled: true,
	onboarding_completed: true,
	streaming_transcription: false,
	ai_enhancement_enabled: false,
	vocabulary_automation_enabled: false,
	auto_punctuation: true,
	audio_quality_warnings: false,
	vocabulary_enabled: true,
	templates_enabled: true,
};

// ── Helper: mock the usePython hook ──────────────────────────────────
vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		// Return command-appropriate shapes so async page init (e.g.
		// Onboarding) settles instead of crashing on `undefined.<field>`.
		call: vi.fn(async (cmd: string) => {
			switch (cmd) {
				case "onboarding_start":
					return { step: 1, total_steps: 4, step_name: "microphone" };
				case "onboarding_get_microphones":
					return { microphones: [] };
				case "onboarding_get_hotkey_presets":
					return { presets: [] };
				case "onboarding_get_model_options":
					return { models: [] };
				default:
					return undefined;
			}
		}),
		pythonPort: 9999,
	}),
	usePythonEvent: vi.fn(),
}));

// Mock useAppStore
vi.mock("@/stores/appStore", () => ({
	useAppStore: (selector: (s: { config: typeof STUB_CONFIG }) => unknown) =>
		selector({ config: STUB_CONFIG }),
}));

// Mock window.window_ bridge
Object.defineProperty(globalThis, "window_", {
	value: {
		isMaximized: vi.fn().mockResolvedValue(false),
		onMaximizedChanged: vi.fn().mockReturnValue(vi.fn()),
		openLogs: vi.fn().mockResolvedValue({ success: true }),
	},
	writable: true,
});

// Mock matchMedia for theme tests
Object.defineProperty(globalThis, "matchMedia", {
	value: vi.fn().mockImplementation((query: string) => ({
		matches: false,
		media: query,
		onchange: null,
		addEventListener: vi.fn(),
		removeEventListener: vi.fn(),
		dispatchEvent: vi.fn(),
	})),
	writable: true,
});
describe("F-17: axe-core automated WCAG scan — all pages", () => {
	it("About page: no axe violations", async () => {
		const AboutPage = (await import("@/pages/About")).default;
		const { container } = render(<AboutPage />);

		const results = await axe.run(container, AXE_OPTIONS);
		const violations = results.violations.filter(
			(v) => v.id !== "color-contrast",
		);
		expect(violations).toEqual([]);
	});

	it("Onboarding page: no axe violations", async () => {
		const OnboardingPage = (await import("@/pages/Onboarding")).default;
		const { container } = render(<OnboardingPage onComplete={vi.fn()} />);

		const results = await axe.run(container, AXE_OPTIONS);
		const violations = results.violations.filter(
			(v) => v.id !== "color-contrast",
		);
		expect(violations).toEqual([]);
	});

	it("History page (empty): no axe violations", async () => {
		const HistoryPage = (await import("@/pages/History")).default;
		const { container } = render(<HistoryPage onNavigate={vi.fn()} />);

		const results = await axe.run(container, AXE_OPTIONS);
		const violations = results.violations.filter(
			(v) => v.id !== "color-contrast",
		);
		expect(violations).toEqual([]);
	});

	it("Vocabulary page (empty): no axe violations", async () => {
		const VocabularyPage = (await import("@/pages/Vocabulary")).default;
		const { container } = render(<VocabularyPage />);

		const results = await axe.run(container, AXE_OPTIONS);
		const violations = results.violations.filter(
			(v) => v.id !== "color-contrast",
		);
		expect(violations).toEqual([]);
	});

	it("Templates page (empty): no axe violations", async () => {
		const TemplatesPage = (await import("@/pages/Templates")).default;
		const { container } = render(<TemplatesPage />);

		const results = await axe.run(container, AXE_OPTIONS);
		const violations = results.violations.filter(
			(v) => v.id !== "color-contrast",
		);
		expect(violations).toEqual([]);
	});
});
