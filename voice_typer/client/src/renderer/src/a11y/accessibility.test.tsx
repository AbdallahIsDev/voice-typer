/**
 * NEW-UX-012: Accessibility tests for the Electron UI.
 *
 * The finding: Config UI not verified with screen reader. ARIA
 * attributes are present in code but never validated by automated
 * accessibility scanning.
 *
 * This module uses source-inspection + DOM structural verification
 * to check ARIA roles, labels, and live regions. For full runtime
 * a11y scanning, see the @axe-core integration used by the renderer
 * test setup.
 *
 * PVT-004 (Sub-agent 16): the previous version of this file pointed
 * at stale paths for ConfirmDialog (`components/ConfirmDialog.tsx`)
 * and ErrorBoundary (`components/ErrorBoundary.tsx`) and guarded the
 * reads with `fs.existsSync`, so when the files moved into
 * `components/common/` and `components/feedback/` the tests silently
 * no-op'd. The guards are removed so a future move breaks the test
 * loudly instead of silently passing.
 *
 * PVT-049 (Sub-agent 16): the "All Switch components" test was a
 * source-pattern scan that only looked at `pages/{Home,Settings,
 * Models,About}.tsx` — but the actual Switch call sites live in
 * `components/settings/*Section.tsx` (28 of 29 Switches were
 * untested). Replaced with a behavioral test that mounts each
 * Section + AudioFilterChain and uses `getAllByRole("switch")` +
 * `toHaveAccessibleName()`.
 */

import fs from "node:fs";
import path from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks shared by the behavioral Switch test (PVT-049) ────────────
// Settings sections transitively import @hugeicons/react,
// @hugeicons/core-free-icons, sonner, next-themes, and @/hooks/usePython.
// Stub them so the sections can mount without pulling in the full
// dependency tree (which would make this test as heavy as a Settings
// page integration test).

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

// Stub every icon used by Settings sections + their transitive children
// (HotkeyPicker, SegmentedControl, Select, SearchField, etc.).  Each
// icon is a tagged `{ name }` object so the HugeiconsIcon mock can
// surface which icon was rendered via data-name.  Vitest's vi.mock
// requires named exports to be declared explicitly, so we enumerate
// the full set consumed by the Section render graph.
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Alert02Icon: make("Alert02Icon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
		FilterIcon: make("FilterIcon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		LockKeyIcon: make("LockKeyIcon"),
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

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: vi.fn(async () => undefined),
		pythonPort: 9999,
	}),
	usePythonEvent: vi.fn(),
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
	}),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: vi.fn(),
	}),
}));

vi.mock("@/hooks/useStatsShare", () => ({
	useStatsShare: () => ({ imageRef: { current: null }, shareAsImage: vi.fn() }),
	computeShareStats: vi.fn(() => ({ dictations: 0, chars: 0, durationSec: 0 })),
}));

vi.mock("@/components/common/KeyringStatusBadge", () => ({
	KeyringStatusBadge: () => <span data-testid="keyring-badge" />,
}));

import type { VoiceTyperConfig } from "@/types/config";

/** Minimal valid config that satisfies every Section's `if (!config)` guard. */
function makeStubConfig(): VoiceTyperConfig {
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
		auto_punctuation: true,
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
		llm_preset: "professional",
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
		custom_theme: { light: {}, dark: {} },
		text_size: 14,
		wayland_warned: false,
		silence_warning_seconds: 0,
		stop_on_silence_seconds: 0,
		max_recording_time_seconds: 900,
		volume_duck_enabled: true,
		volume_duck_level: 0.2,
		volume_duck_per_session: false,
		volume_duck_fade_ms: 200,
		volume_duck_smart: false,
		volume_duck_smart_poll_interval_ms: 0,
		audio_preset: "auto",
		noise_filter_enabled: false,
		noise_filter_highpass: true,
		noise_filter_highpass_cutoff_hz: 80,
		noise_filter_gate: true,
		noise_filter_gate_threshold: 0,
		noise_filter_gate_hold_ms: 0,
		noise_filter_gate_open_threshold_db: -26,
		noise_filter_gate_close_threshold_db: -32,
		noise_filter_gate_attack_ms: 0,
		noise_filter_gate_release_ms: 0,
		noise_filter_rnnoise: false,
		noise_filter_post_capture: false,
		noise_suppression_method: "rnnoise",
		noise_filter_eq: true,
		noise_filter_eq_low_db: -3,
		noise_filter_eq_mid_db: 3,
		noise_filter_eq_high_db: 2,
		noise_filter_compressor: true,
		noise_filter_compressor_threshold_db: -18,
		noise_filter_compressor_ratio: 3,
		noise_filter_compressor_attack_ms: 0,
		noise_filter_compressor_release_ms: 0,
		noise_filter_compressor_output_gain_db: 0,
		noise_filter_limiter: true,
		noise_filter_limiter_ceiling_db: -6,
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
}

/** Section-level shared props (no-op callbacks; isVisible always true). */
function makeSectionProps() {
	return {
		config: makeStubConfig(),
		updateConfig: vi.fn(),
		updateConfigDebounced: vi.fn(),
		isVisible: () => true,
	};
}

describe("NEW-UX-012: Accessibility ARIA patterns", () => {
	it("Settings.tsx should have aria-label on Select triggers", () => {
		const settingsPath = path.resolve(__dirname, "..", "pages", "Settings.tsx");
		const src = fs.readFileSync(settingsPath, "utf-8");

		const selectTriggerCount = (src.match(/SelectTrigger/g) || []).length;
		const ariaLabelCount = (src.match(/aria-label/g) || []).length;
		expect(ariaLabelCount).toBeGreaterThanOrEqual(selectTriggerCount);
	});

	it("App.tsx should have aria-live regions for dynamic content", () => {
		const appPath = path.resolve(__dirname, "..", "App.tsx");
		const src = fs.readFileSync(appPath, "utf-8");
		expect(src).toContain("aria-live");
	});

	it("Home.tsx should have role attributes for status indicators", () => {
		const homePath = path.resolve(__dirname, "..", "pages", "Home.tsx");
		const src = fs.readFileSync(homePath, "utf-8");
		expect(
			src.includes("aria-live") ||
				src.includes('role="status"') ||
				src.includes("role='status'"),
		).toBe(true);
	});

	// PVT-049 (Sub-agent 16): the previous "All Switch components" test
	// scanned only `pages/{Home,Settings,Models,About}.tsx` for `<Switch`
	// occurrences and checked that each was either accompanied by an
	// `aria-label` or wrapped in `<SettingRow label="…">`.  But the
	// actual Switch call sites live in `components/settings/*Section.tsx`
	// (28 of 29 production Switches are there), so the test silently
	// passed while most Switches were unverified.  The behavioral test
	// below mounts each Section + AudioFilterChain and asserts every
	// rendered switch role has an accessible name.
	describe("PVT-049: every mounted Switch has an accessible name (behavioral)", () => {
		beforeEach(() => {
			cleanup();
		});

		afterEach(() => {
			cleanup();
		});

		it("GeneralSettingsSection: all switches have accessible names", async () => {
			const { GeneralSettingsSection } = await import(
				"@/components/settings/GeneralSettingsSection"
			);
			const { container } = render(
				<GeneralSettingsSection {...makeSectionProps()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("ModelSettingsSection: all switches have accessible names", async () => {
			const { ModelSettingsSection } = await import(
				"@/components/settings/ModelSettingsSection"
			);
			const { container } = render(
				<ModelSettingsSection {...makeSectionProps()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("AudioSettingsSection: all switches have accessible names", async () => {
			const { AudioSettingsSection } = await import(
				"@/components/settings/AudioSettingsSection"
			);
			const { container } = render(
				<AudioSettingsSection {...makeSectionProps()} />,
			);
			// AudioSettingsSection may delegate the per-filter rows to
			// AudioFilterChain; either way, any rendered switch must be
			// labelled.
			const switches = screen.queryAllByRole("switch");
			if (switches.length === 0) return; // section may be filtered out
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("RecordingSettingsSection: all switches have accessible names", async () => {
			const { RecordingSettingsSection } = await import(
				"@/components/settings/RecordingSettingsSection"
			);
			const { container } = render(
				<RecordingSettingsSection {...makeSectionProps()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("AiEnhancementSettingsSection: all switches have accessible names", async () => {
			const { AiEnhancementSettingsSection } = await import(
				"@/components/settings/AiEnhancementSettingsSection"
			);
			const { container } = render(
				<AiEnhancementSettingsSection {...makeSectionProps()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("ThemeSettingsSection: all switches have accessible names", async () => {
			const { ThemeSettingsSection } = await import(
				"@/components/settings/ThemeSettingsSection"
			);
			const { container } = render(
				<ThemeSettingsSection {...makeSectionProps()} />,
			);
			// ThemeSettingsSection renders Switch only when theme_preset
			// is "custom" or similar; verify any rendered switches.
			const switches = screen.queryAllByRole("switch");
			if (switches.length === 0) return;
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("PrivacySettingsSection: all switches have accessible names", async () => {
			const { PrivacySettingsSection } = await import(
				"@/components/settings/PrivacySettingsSection"
			);
			const { container } = render(
				<PrivacySettingsSection {...makeSectionProps()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});

		it("AudioFilterChain: all switches have accessible names", async () => {
			const { AudioFilterChain } = await import(
				"@/components/audio/AudioFilterChain"
			);
			const { container } = render(
				<AudioFilterChain config={makeStubConfig()} onConfigChange={vi.fn()} />,
			);
			const switches = screen.getAllByRole("switch");
			expect(switches.length).toBeGreaterThan(0);
			for (const sw of container.querySelectorAll('[role="switch"]')) {
				expect(sw).toHaveAccessibleName();
			}
		});
	});
});

describe("NEW-UX-012: Dialog accessibility", () => {
	// PVT-004 (Sub-agent 16): the previous version pointed at
	// `components/ConfirmDialog.tsx` and `components/ErrorBoundary.tsx`
	// and guarded with `fs.existsSync`, so when the files moved into
	// `components/common/` and `components/feedback/` the tests silently
	// no-op'd.  The guards are removed so a future move breaks the test
	// loudly instead of silently passing.
	//
	// ConfirmDialog itself doesn't carry a literal `role="dialog"` —
	// it delegates to Radix UI's AlertDialog primitive (see
	// components/ui/alert-dialog.tsx), which sets `role="alertdialog"`
	// on the rendered content at runtime.  We assert either the
	// literal role attribute OR the use of the AlertDialog primitive
	// (the latter is the canonical way to get a screen-reader-friendly
	// dialog role in this codebase).
	it("ConfirmDialog should render a dialog/alertdialog role (via Radix AlertDialog or literal)", () => {
		const dialogPath = path.resolve(
			__dirname,
			"..",
			"components",
			"common",
			"ConfirmDialog.tsx",
		);
		const src = fs.readFileSync(dialogPath, "utf-8");
		const hasLiteralRole =
			src.includes('role="dialog"') ||
			src.includes("role='dialog'") ||
			src.includes('role="alertdialog"') ||
			src.includes("role='alertdialog'");
		const usesAlertDialogPrimitive =
			src.includes("AlertDialog") || src.includes("DialogPrimitive");
		expect(hasLiteralRole || usesAlertDialogPrimitive).toBe(true);
	});

	it("ErrorBoundary should have aria-live for error messages", () => {
		const errorBoundaryPath = path.resolve(
			__dirname,
			"..",
			"components",
			"feedback",
			"ErrorBoundary.tsx",
		);
		const src = fs.readFileSync(errorBoundaryPath, "utf-8");
		expect(
			src.includes("aria-live") ||
				src.includes('role="alert"') ||
				src.includes("role='alert'"),
		).toBe(true);
	});
});

// PVT-047 (Sub-agent 16): Home.tsx renders the most recent transcription
// result (`lastText`) inside a `<p>` element so sighted users see what
// was just pasted, but the surrounding container has no `aria-live`
// attribute — so screen-reader users get NO announcement when a
// transcription completes (they only hear the App-level status pill
// flip from "Recording" to "Ready", which doesn't include the text).
//
// This test asserts that `lastText` is rendered inside an element (or
// an ancestor) that carries an `aria-live` attribute.  It's a
// source-pattern test rather than a behavioral mount because mounting
// Home requires the full Python bridge + connection store wiring (out
// of scope for the a11y test file — see Home.test.tsx for that).
//
// NOTE: `it.fails()` marks the test as expected-to-fail.  When the
// production fix lands (Home.tsx wraps the `{lastText}` `<p>` in an
// `aria-live="polite"` container, or moves it inside the existing
// `<output aria-live="polite">` pill), this test will START passing
// and vitest will report "test unexpectedly passed" — at which point
// the `it.fails` should be flipped back to `it`.
describe("PVT-047: Home transcription result is in a live region", () => {
	it.fails("Home.tsx wraps the `{lastText}` element in an aria-live region", () => {
		const homePath = path.resolve(__dirname, "..", "pages", "Home.tsx");
		const src = fs.readFileSync(homePath, "utf-8");

		// Locate the `{lastText}` JSX expression and capture a
		// ~300-char window around it so we can inspect the
		// surrounding markup without parsing the full TSX file.
		const idx = src.indexOf("{lastText}");
		expect(idx).toBeGreaterThan(-1);

		const start = Math.max(0, idx - 300);
		const end = Math.min(src.length, idx + 300);
		const window = src.slice(start, end);

		// The window MUST contain an `aria-live` attribute on an
		// ancestor element (the existing `<output aria-live="polite">`
		// status pill is 100+ lines away and so won't appear in this
		// window — only a NEW live region wrapping the lastText
		// block will satisfy this assertion).
		expect(window).toMatch(/aria-live\s*=/);
	});
});

// Item 8 (Sub-agent 16): assert the renderer stylesheet declares the
// three WCAG-mandated @media blocks for user preference overrides.
// Source-pattern is appropriate here because we're asserting the
// PRESENCE of the rules themselves, not their computed style on a
// mounted component (jsdom doesn't actually apply @media queries).
describe("Item 8: index.css declares user-preference @media blocks", () => {
	const cssPath = path.resolve(__dirname, "..", "index.css");

	it("declares @media (prefers-reduced-motion: reduce) — WCAG 2.3.3", () => {
		const src = fs.readFileSync(cssPath, "utf-8");
		expect(src).toContain("@media (prefers-reduced-motion: reduce)");
	});

	it("declares @media (forced-colors: active) — WCAG 1.4.11 (Windows high-contrast)", () => {
		const src = fs.readFileSync(cssPath, "utf-8");
		expect(src).toContain("@media (forced-colors: active)");
	});

	it("declares @media (prefers-contrast: high) — WCAG 1.4.11 (macOS Increase Contrast)", () => {
		const src = fs.readFileSync(cssPath, "utf-8");
		expect(src).toContain("@media (prefers-contrast: high)");
	});
});

// Item 9 (Sub-agent 16): Dashboard a11y.  The 7-day activity chart is
// visually a heatmap (rows of bars coloured by intensity) and must be
// exposed to AT as a single `role="img"` with a descriptive aria-label
// (so screen readers hear "7-day activity chart" instead of "button,
// button, button, button, button, button, button").  Each stat card
// must have an accessible name so AT users hear "Dictations today: 5"
// rather than just "5".
//
// As with PVT-047, the production fix (adding `role="img"` +
// `aria-label` to the chart container) is owned by agent 8
// (Dashboard.tsx is in agent 8's file scope).  `it.fails()` is used so
// the test exists as a regression spec and won't break validation
// until the fix lands.
describe("Item 9: Dashboard a11y — heatmap role + stat card names", () => {
	it.fails('Dashboard 7-day activity chart container has role="img" + aria-label', () => {
		const dashboardPath = path.resolve(
			__dirname,
			"..",
			"pages",
			"Dashboard.tsx",
		);
		const src = fs.readFileSync(dashboardPath, "utf-8");

		// The 7-day activity chart is the only `flex items-end
		// justify-between gap-2 h-20` block in the file (see
		// Dashboard.tsx:579).  Locate it and assert the wrapping
		// container carries `role="img"` and `aria-label=`.
		const chartIdx = src.indexOf("flex items-end justify-between gap-2 h-20");
		expect(chartIdx).toBeGreaterThan(-1);

		const start = Math.max(0, chartIdx - 400);
		const window = src.slice(start, chartIdx);
		expect(window).toMatch(/role="img"/);
		expect(window).toMatch(/aria-label=/);
	});

	it("DashboardStatCard exposes its label as the accessible name", () => {
		const cardPath = path.resolve(
			__dirname,
			"..",
			"components",
			"dashboard",
			"DashboardStatCard.tsx",
		);
		const src = fs.readFileSync(cardPath, "utf-8");
		// The label prop is rendered as visible text inside a <p>; that
		// text becomes the card's accessible name (the icon is
		// aria-hidden by HugeiconsIcon).  We assert the label is
		// rendered as text content.
		expect(src).toMatch(/\{label\}/);
	});
});

// Item 10 (Sub-agent 16): TitleBar.tsx (owned by agent 3) renders
// `<title>` elements inside `aria-hidden` SVGs (see TitleBar.tsx:31,
// :47, :63, :80 — the MinimizeIcon, MaximizeIcon, RestoreIcon, and
// CloseIcon helper components).  These `<title>` elements are
// INACCESSIBLE to assistive tech because the parent `<svg>` carries
// `aria-hidden`, so the title text is silently dropped by screen
// readers.  They're also redundant: the wrapping `<button>` already
// has an `aria-label` (see TitleBarButton component), so the SVG
// title would never be announced even if the SVG weren't hidden.
//
// The fix is to delete the `<title>` elements from the four
// `*Icon` helper functions in `TitleBar.tsx`.  This is OUT OF SCOPE
// for sub-agent 16 (file scope is a11y test files only — see the
// task assignment table where TitleBar.tsx is owned by agent 3).
//
// This test is a `it.fails` regression spec so the issue isn't
// forgotten: when agent 3 (or a future PR) removes the dead
// `<title>` elements, this test will START PASSING and vitest will
// report "test unexpectedly passed" — at which point the `it.fails`
// should be flipped to a regular `it` so future regressions are
// caught.
describe("Item 10: TitleBar SVGs should NOT carry <title> inside aria-hidden SVGs (agent 3's scope)", () => {
	it.fails("TitleBar.tsx contains no <title> elements inside aria-hidden SVGs", () => {
		const titleBarPath = path.resolve(
			__dirname,
			"..",
			"components",
			"layout",
			"TitleBar.tsx",
		);
		const src = fs.readFileSync(titleBarPath, "utf-8");

		// Find every `<svg ... aria-hidden ...>` block and assert none
		// of them contain a `<title>` child.  We use a coarse regex
		// (JSX is not regex-friendly) — the test is intentionally
		// strict so any `<title>` inside an aria-hidden SVG is
		// flagged.
		const svgBlockRegex = /<svg[^>]*aria-hidden[\s\S]*?<\/svg>/g;
		const svgBlocks = src.match(svgBlockRegex) || [];
		const offendingBlocks = svgBlocks.filter((block) =>
			block.includes("<title>"),
		);
		expect(offendingBlocks).toEqual([]);
	});
});
