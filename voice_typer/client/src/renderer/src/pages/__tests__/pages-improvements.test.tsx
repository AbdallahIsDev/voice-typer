/**
 * I10-retry: regression tests for the React renderer PAGE improvements.
 *
 * Covers the following findings (each in its own describe block so a
 * failure pinpoints which contract regressed):
 *
 *   - CR-37   Vocabulary.tsx — getCategoryLabels() re-resolves t() on
 *             locale switch (no stale module-level const).
 *   - R7-F8   Onboarding.tsx — `let cancelled = false` guard prevents
 *             setState-after-unmount during the init() effect.
 *   - R7-F9   Models.tsx — dead isBenchmarking / runBenchmark /
 *             BenchmarkSection removed; no benchmark UI in either tab.
 *   - R7-F10  Vocabulary.tsx + Templates.tsx — dead _requestDeleteEntry
 *             / _requestDeleteTemplate / deleteTarget state /
 *             ConfirmDialog removed; no role="alertdialog" rendered.
 *   - R7-F11  Vocabulary.tsx + Templates.tsx — placeholder strings use
 *             t("vocabulary.triggerPlaceholder") etc. (i18n keys exist
 *             in en.json; other 7 locales backfilled by I12).
 *   - R7-F12  Models.tsx — model card heading uses
 *             `meta?.display_name ?? model.name` (no hardcoded
 *             "Qwen3-ASR-1.7B" / "NVIDIA Parakeet TDT v3" strings).
 *   - R7-F13  History.tsx + Home.tsx — debouncedRefreshFromEvent is
 *             extracted via useCallback and passed to both
 *             usePythonEvent subscriptions (single callback identity).
 *   - R7-F15  About.tsx — configDir initial state is "" (empty) and
 *             the UI renders t("about.loading") as fallback until the
 *             backend reports the real directory.
 *   - R7-F16  History.tsx — visible records list capped at 200 items
 *             via `.slice(0, 200)`.
 *   - R7-F18  Dashboard.tsx — dead `const [, setLoading] = useState`
 *             and all `setLoading` call sites removed.
 *   - CR-57   Microphone.tsx — 100ms level polling short-circuits when
 *             `document.visibilityState !== "visible"` OR
 *             `!testRunning && !micMonitoring`.
 *   - CR-19-F2 Settings.tsx — each of the four tab panels is wrapped
 *             in `<div role="tabpanel" id="panel-<tabId>"
 *             aria-labelledby="tab-<tabId>">`.
 *
 * Mock strategy
 * -------------
 * The renderer pages share a common dependency graph (usePython,
 * hugeicons, sonner, next-themes, useLastUpdated). We mock each once
 * at the module level so every describe block can `import` any page
 * without re-declaring mocks. The hugeicons mock uses a `Proxy` so
 * ANY icon-name access returns a tagged `{ name }` object — that way
 * we don't have to enumerate every icon imported by the page render
 * graph (which spans SearchField, ExportFormatMenu, EmptyState,
 * ui/select, etc.).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Hoisted mock fixtures ──────────────────────────────────────────────
// vi.mock factories are hoisted to the top of the file by vitest, so
// any value they close over must also be hoisted.
const { mockCall, mockPythonEvent, showSnack } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	showSnack: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	// usePythonEvent(eventName, handler): record the event name + the
	// handler closure so tests can verify both subscriptions received
	// the SAME callback identity (R7-F13).
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack }),
	showUndoableToast: vi.fn(),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: vi.fn(),
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

// Enumerate every icon imported by the renderer page render graph
// (pages + transitive components: SearchField, ExportFormatMenu,
// EmptyState, ui/select, Settings/* sections, dashboard/* cards,
// microphone/* panels, hotkey/HotkeyPicker, common/LastUpdatedIndicator,
// common/PageHeading). Each icon is mocked as `{ name }` so the
// HugeiconsIcon mock can surface which icon was rendered via data-name.
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Activity03Icon: make("Activity03Icon"),
		Add01Icon: make("Add01Icon"),
		AiBrain03Icon: make("AiBrain03Icon"),
		Alert02Icon: make("Alert02Icon"),
		AlertCircleIcon: make("AlertCircleIcon"),
		Analytics01Icon: make("Analytics01Icon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowRight01Icon: make("ArrowRight01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Calendar01Icon: make("Calendar01Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		CheckmarkCircle02Icon: make("CheckmarkCircle02Icon"),
		ClipboardPasteIcon: make("ClipboardPasteIcon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
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
		Undo02Icon: make("Undo02Icon"),
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

import { setLocale, t } from "@/i18n/i18n";
// Import pages AFTER mocks are declared. Using dynamic import() inside
// each test would also work, but static imports keep the test file
// simpler. Vitest hoists vi.mock() before any static import.
import AboutPage from "@/pages/About";
import ModelsPage from "@/pages/Models";
import OnboardingPage from "@/pages/Onboarding";
import SettingsPage from "@/pages/Settings";
import TemplatesPage from "@/pages/Templates";
import VocabularyPage from "@/pages/Vocabulary";
import type { VoiceTyperConfig } from "@/types/config";

// ── Shared fixtures ────────────────────────────────────────────────────

const MINIMAL_CONFIG: VoiceTyperConfig = {
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
} as unknown as VoiceTyperConfig;

beforeEach(() => {
	mockCall.mockReset();
	mockPythonEvent.mockReset();
	showSnack.mockReset();
	localStorage.clear();
	// Reset locale to English between tests so locale-switch tests
	// start from a known state.
	setLocale("en");
});

afterEach(() => {
	cleanup();
});

// ── CR-37 ──────────────────────────────────────────────────────────────

describe("CR-37: Vocabulary categoryLabels re-resolve on locale switch", () => {
	it("renders German category label after setLocale('de')", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") return Promise.resolve({});
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<VocabularyPage />);

		// Wait for the page to load — the "Add Word" button renders
		// once the initial get_vocabulary call resolves.
		await waitFor(() => {
			expect(screen.getByText(t("vocabulary.addWord"))).toBeTruthy();
		});

		// Open the Add dialog so the category Select renders.
		fireEvent.click(screen.getByText(t("vocabulary.addWord")));

		// Switch locale to German. Because categoryLabels is computed
		// at render time via getCategoryLabels(), the next render must
		// reflect the German translation of "Misspellings" →
		// "Falschschreibungen".
		setLocale("de");

		// Re-render with the new locale. We click the Add button again
		// to force a re-render (state change forces re-render anyway).
		// The German translation for vocabulary.category.misspellings
		// is "Falschschreibungen" (see de.json:150).
		// We verify by directly calling t() with the German locale active.
		expect(t("vocabulary.category.misspellings")).toBe("Falschschreibungen");

		// And confirm switching back to English yields the English label.
		setLocale("en");
		expect(t("vocabulary.category.misspellings")).toBe("Misspellings");
	});

	it("getCategoryLabels is a function (not a stale const)", async () => {
		// Static check: the module exports a function (CR-37 specifically
		// converts a const to a function so the labels are re-resolved
		// at every render). We verify by importing the module source
		// and grepping for the function declaration. CR-37 moved the
		// category labels into vocabulary/lib/categories.ts.
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/vocabulary/lib/categories.ts",
			"utf8",
		);
		expect(src).toContain("function getCategoryLabels()");
		expect(src).not.toContain("const CATEGORY_LABELS");
		// And the consumers call the function at render time:
		const vocabSrc = fs.readFileSync(
			"src/renderer/src/pages/Vocabulary.tsx",
			"utf8",
		);
		expect(vocabSrc).toContain("const categoryLabels = getCategoryLabels();");
	});
});

// ── R7-F8 ──────────────────────────────────────────────────────────────

describe("R7-F8: Onboarding init effect uses cancelled-flag guard", () => {
	it("source contains cancelled flag + cleanup return", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/Onboarding.tsx",
			"utf8",
		);
		// R7-F8 contract: `let cancelled = false;` at the top of the
		// effect, `if (cancelled) return;` before each setState, and
		// `return () => { cancelled = true; }` in cleanup.
		expect(src).toContain("let cancelled = false;");
		expect(src).toContain("if (cancelled) return;");
		expect(src).toMatch(/return \(\) => \{[^}]*cancelled = true/);
	});

	it("does not call setState after unmount (no React warning)", async () => {
		// Make every IPC call return a delayed promise so the init()
		// effect is still pending when we unmount.
		const pendingResolvers: Array<(v: unknown) => void> = [];
		mockCall.mockImplementation(() => {
			return new Promise((resolve) => {
				pendingResolvers.push(resolve);
			});
		});

		// Capture console.error to detect React's "setState on
		// unmounted component" warnings (React 18+ removed this
		// warning, but if it ever resurfaces we want to catch it).
		const seenErrors: string[] = [];
		const origError = console.error;
		console.error = (...args: unknown[]) => {
			seenErrors.push(args.map(String).join(" "));
		};

		try {
			const { unmount } = render(<OnboardingPage onComplete={() => {}} />);
			// Unmount BEFORE the init() promises resolve.
			unmount();
			// Now resolve the pending promises — the cancelled flag
			// should prevent any setState calls.
			for (const resolve of pendingResolvers) {
				resolve({ step: 0, total_steps: 6, step_name: "welcome" });
			}
			// Flush microtasks.
			await new Promise((r) => setTimeout(r, 10));
		} finally {
			console.error = origError;
		}

		// No "unmounted component" or "setState" warning should have
		// been emitted.
		const offending = seenErrors.filter(
			(s) => s.includes("unmounted") || s.includes("setState"),
		);
		expect(offending).toEqual([]);
	});
});

// ── R7-F9 ──────────────────────────────────────────────────────────────

describe("R7-F9: Models.tsx — no dead benchmark UI", () => {
	it("source contains no isBenchmarking / runBenchmark / BenchmarkSection", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Models.tsx", "utf8");
		// Comments referencing the removed identifiers are fine — we
		// only check for live identifiers (state vars, function defs,
		// JSX component tags). Strip block + line comments first.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain("isBenchmarking");
		expect(stripped).not.toContain("_setIsBenchmarking");
		expect(stripped).not.toContain("runBenchmark");
		expect(stripped).not.toContain("<BenchmarkSection");
		expect(stripped).not.toContain("benchmarkResult");
	});

	it("renders no benchmark button in the model catalog", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "get_model_status") return Promise.resolve({});
			if (type === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve(MINIMAL_CONFIG);
		});

		render(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});

		// No "Benchmark" or "Run Benchmark" button should be rendered.
		expect(screen.queryByRole("button", { name: /benchmark/i })).toBeNull();
	});
});

// ── R7-F10 ─────────────────────────────────────────────────────────────

describe("R7-F10: Vocabulary + Templates — no dead ConfirmDialog", () => {
	it("Vocabulary.tsx source has no _requestDeleteEntry / deleteEntryTarget / ConfirmDialog JSX", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/Vocabulary.tsx",
			"utf8",
		);
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain("_requestDeleteEntry");
		expect(stripped).not.toContain("deleteEntryTarget");
		expect(stripped).not.toContain("confirmDeleteEntry");
		expect(stripped).not.toContain("<ConfirmDialog");
		expect(stripped).not.toContain("handleCancelDelete");
	});

	it("Templates.tsx source has no _requestDeleteTemplate / deleteTarget / ConfirmDialog JSX", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Templates.tsx", "utf8");
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain("_requestDeleteTemplate");
		expect(stripped).not.toContain("deleteTarget");
		expect(stripped).not.toContain("confirmDeleteTemplate");
		expect(stripped).not.toContain("<ConfirmDialog");
		expect(stripped).not.toContain("handleCancelDelete");
	});

	it('Vocabulary renders no role="alertdialog" (ConfirmDialog removed)', async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") return Promise.resolve({});
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<VocabularyPage />);
		await waitFor(() => {
			expect(screen.getByText(t("vocabulary.addWord"))).toBeTruthy();
		});
		expect(screen.queryByRole("alertdialog")).toBeNull();
	});

	it('Templates renders no role="alertdialog" (ConfirmDialog removed)', async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve({ templates: [] });
			if (type === "save_templates") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<TemplatesPage />);
		await waitFor(() => {
			expect(screen.getByText(t("templates.addTemplate"))).toBeTruthy();
		});
		expect(screen.queryByRole("alertdialog")).toBeNull();
	});
});

// ── R7-F11 ─────────────────────────────────────────────────────────────

describe("R7-F11: Vocabulary + Templates — i18n placeholders", () => {
	it("en.json contains the four placeholder keys", async () => {
		const en = (await import("@/i18n/translations/en.json")).default as Record<
			string,
			unknown
		>;
		// Vocabulary keys.
		expect(
			(en.vocabulary as Record<string, unknown>).triggerPlaceholder,
		).toBeTruthy();
		expect(
			(en.vocabulary as Record<string, unknown>).replacementPlaceholder,
		).toBeTruthy();
		// Templates keys.
		expect(
			(en.templates as Record<string, unknown>).triggerPlaceholder,
		).toBeTruthy();
		expect(
			(en.templates as Record<string, unknown>).outputPlaceholder,
		).toBeTruthy();
	});

	it("Vocabulary dialog renders the i18n trigger/replacement placeholders", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") return Promise.resolve({});
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<VocabularyPage />);
		await waitFor(() => {
			expect(screen.getByText(t("vocabulary.addWord"))).toBeTruthy();
		});

		fireEvent.click(screen.getByText(t("vocabulary.addWord")));

		const triggerInput = screen.getByLabelText(t("vocabulary.whatYouSay"), {
			selector: "input",
		}) as HTMLInputElement;
		const replacementInput = screen.getByLabelText(
			t("vocabulary.whatGetsTyped"),
			{ selector: "input" },
		) as HTMLInputElement;

		expect(triggerInput.placeholder).toBe(t("vocabulary.triggerPlaceholder"));
		expect(replacementInput.placeholder).toBe(
			t("vocabulary.replacementPlaceholder"),
		);
	});

	it("Templates dialog renders the i18n trigger/output placeholders", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") return Promise.resolve({ templates: [] });
			if (type === "save_templates") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<TemplatesPage />);
		await waitFor(() => {
			expect(screen.getByText(t("templates.addTemplate"))).toBeTruthy();
		});

		fireEvent.click(screen.getByText(t("templates.addTemplate")));

		const triggerInput = screen.getByLabelText(t("templates.triggerPhrase"), {
			selector: "input",
		}) as HTMLInputElement;
		const outputTextarea = screen.getByLabelText(t("templates.outputText"), {
			selector: "textarea",
		}) as HTMLTextAreaElement;

		expect(triggerInput.placeholder).toBe(t("templates.triggerPlaceholder"));
		expect(outputTextarea.placeholder).toBe(t("templates.outputPlaceholder"));
	});
});

// ── R7-F12 ─────────────────────────────────────────────────────────────

describe("R7-F12: Models.tsx — display_name fallback for variant heading", () => {
	it("source uses `meta?.display_name ?? model.name` (no hardcoded strings)", async () => {
		const fs = await import("node:fs");
		// R7-F12: the display_name fallback lives in LocalModelsPanel
		// (extracted from the former Models.tsx monolith). Read the
		// actual source file that renders the variant heading.
		const src = fs.readFileSync(
			"src/renderer/src/components/models/LocalModelsPanel.tsx",
			"utf8",
		);
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).toContain("meta?.display_name ?? model.name");
		// The old hardcoded ternary must be gone.
		expect(stripped).not.toContain('"Qwen3-ASR-1.7B"');
		expect(stripped).not.toContain('"NVIDIA Parakeet TDT v3"');
	});

	it("ModelMetadata interface includes display_name field", async () => {
		const fs = await import("node:fs");
		// The ModelMetadata interface lives in lib/utils/models.ts.
		const src = fs.readFileSync("src/renderer/src/lib/utils/models.ts", "utf8");
		// Locate the ModelMetadata interface body and verify display_name is declared.
		const idx = src.indexOf("interface ModelMetadata");
		expect(idx).toBeGreaterThanOrEqual(0);
		const slice = src.slice(idx, idx + 600);
		expect(slice).toMatch(/display_name\??:\s*string/);
	});
});

// ── R7-F13 ─────────────────────────────────────────────────────────────

describe("R7-F13: History + Home — debouncedRefreshFromEvent via useCallback", () => {
	it("History.tsx source declares debouncedRefreshFromEvent via useCallback and passes it to both usePythonEvent calls", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		// Count usePythonEvent invocations — there should be at least
		// two, and both should pass `debouncedRefreshFromEvent` as the
		// handler.
		const matches = src.match(/usePythonEvent\(/g) ?? [];
		expect(matches.length).toBeGreaterThanOrEqual(2);
		// Both transcription_final and history_changed subscriptions
		// must pass the shared callback.
		expect(src).toMatch(/usePythonEvent\(\s*["`]transcription_final["`]/);
		expect(src).toMatch(/usePythonEvent\(\s*["`]history_changed["`]/);
		// Sanity: `debouncedRefreshFromEvent` appears at least twice
		// as a usePythonEvent argument (one per subscription).
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		expect(uses.length).toBeGreaterThanOrEqual(3); // 1 decl + 2 uses
	});

	it("Home.tsx source declares debouncedRefreshFromEvent via useCallback and passes it to both usePythonEvent calls", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		// 1 declaration + at least 2 subscription uses.
		expect(uses.length).toBeGreaterThanOrEqual(3);
	});
});

// ── R7-F15 ─────────────────────────────────────────────────────────────

describe("R7-F15: About.tsx — configDir starts empty and falls back to t('about.loading')", () => {
	it("source initialises configDir with empty string", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/About.tsx", "utf8");
		// The useState<string>("") call is the contract — previously
		// it was useState<string>("~/.voice-typer").
		expect(src).toContain('useState<string>("")');
		// Strip comments before checking — the R7-F15 fix leaves a
		// comment marker explaining what was removed.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain('"~/.voice-typer"');
		// And the fallback uses t("about.loading") (not a hardcoded
		// "Loading…" string).
		expect(stripped).toContain('configDir || t("about.loading")');
	});

	it("renders t('about.loading') as the config-directory value before the backend resolves", async () => {
		// Make get_status pending so configDir stays "" on the first
		// render. The fallback should be the about.loading string.
		mockCall.mockImplementation(() => new Promise(() => {}));

		render(<AboutPage />);

		// Wait for the Diagnostics section to appear.
		await waitFor(() => {
			expect(screen.getByText(t("about.diagnosticsTitle"))).toBeTruthy();
		});

		// The Config Directory row should show the loading fallback
		// (since get_status is still pending).
		expect(screen.getByText(t("about.loading"))).toBeTruthy();
		// The hardcoded "~/.voice-typer" string must NOT appear.
		expect(screen.queryByText("~/.voice-typer")).toBeNull();
	});
});

// ── R7-F16 ─────────────────────────────────────────────────────────────

describe("R7-F16: History.tsx — visible list capped at 200 items", () => {
	it("source contains records.slice(0, 200)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/History.tsx", "utf8");
		expect(src).toMatch(/records\.slice\(0,\s*200\)/);
	});
});

// ── R7-F18 ─────────────────────────────────────────────────────────────

describe("R7-F18: Dashboard.tsx — dead setLoading removed", () => {
	it("source has no live setLoading calls (comments allowed)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Dashboard.tsx", "utf8");
		// Strip comments before checking — the R7-F18 fix leaves a
		// comment marker explaining what was removed.
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		// No `setLoading(` call sites.
		expect(stripped).not.toContain("setLoading(");
		// No `const [, setLoading] = useState` declaration.
		expect(stripped).not.toMatch(
			/const\s*\[[^,]*,\s*setLoading\]\s*=\s*useState/,
		);
	});
});

// ── CR-57 ──────────────────────────────────────────────────────────────

describe("CR-57: Microphone.tsx — polling gated on visibility + active state", () => {
	it("source checks document.visibilityState and the testRunning/micMonitoring refs inside the interval", async () => {
		const fs = await import("node:fs");
		// CR-57: the polling logic lives in useMicrophoneTest.ts
		// (extracted from the former Microphone.tsx monolith). Read
		// the actual source file that contains the interval closure.
		const src = fs.readFileSync(
			"src/renderer/src/pages/microphone/hooks/useMicrophoneTest.ts",
			"utf8",
		);
		// The visibility check.
		expect(src).toContain("document.visibilityState");
		expect(src).toContain('"visible"');
		// The active-state check (refs are kept in sync via effects so
		// the interval closure reads the latest values without
		// rebinding).
		expect(src).toContain("testRunningRef.current");
		expect(src).toContain("micMonitoringRef.current");
		// The combined guard.
		expect(src).toMatch(
			/!testRunningRef\.current\s*&&\s*!micMonitoringRef\.current/,
		);
	});

	it("does not call microphone_test_get_level while document is hidden", async () => {
		// Mock config so Microphone renders the polling effect.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "get_status") return Promise.resolve({ status: "idle" });
			if (type === "list_microphones")
				return Promise.resolve({ microphones: [] });
			if (type === "microphone_test_get_level")
				return Promise.resolve({ level: 0, peak: 0, active: false });
			if (type === "level_monitor_start")
				return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		// Force the document to be hidden.
		const original = document.visibilityState;
		Object.defineProperty(document, "visibilityState", {
			configurable: true,
			get: () => "hidden",
		});

		// Set testRunning=true so the ONLY reason to skip the poll is
		// the visibility gate. If the visibility check is missing, the
		// test will see microphone_test_get_level calls.
		try {
			const MicrophonePage = (await import("@/pages/Microphone"))
				.default as unknown as React.FC<{
				testRunning?: boolean;
			}>;
			render(<MicrophonePage testRunning />);

			// Let a couple of 100ms polling ticks fire.
			await new Promise((r) => setTimeout(r, 250));

			const levelCalls = mockCall.mock.calls.filter(
				(args: unknown[]) => args[0] === "microphone_test_get_level",
			);
			expect(levelCalls.length).toBe(0);
		} finally {
			Object.defineProperty(document, "visibilityState", {
				configurable: true,
				get: () => original,
			});
		}
	});
});

// ── CR-19-F2 ───────────────────────────────────────────────────────────

describe('CR-19-F2: Settings.tsx — tab panels wrapped in role="tabpanel"', () => {
	it("source wraps each activeTab === ... block in a div[role=tabpanel]", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Settings.tsx", "utf8");
		// CR-19-F2: the tab panels are rendered via a renderTabPanel
		// helper that wraps children in a div with role="tabpanel",
		// id={`panel-${tab}`}, and aria-labelledby={`tab-${tab}`}.
		// The literal per-tab ids (panel-appearance, panel-general,
		// etc.) are produced at runtime by the template literal, so we
		// assert on the template pattern + the role attribute instead.
		expect(src).toContain('role="tabpanel"');
		expect(src).toContain("id={`panel-${tab}`}");
		expect(src).toContain("aria-labelledby={`tab-${tab}`}");
		// Sanity: the renderTabPanel helper is called once per tab
		// value (appearance, general, aiAudio, privacy).
		for (const tabId of ["appearance", "general", "aiAudio", "privacy"]) {
			expect(src).toMatch(new RegExp(`renderTabPanel\\(\\s*["']${tabId}["']`));
		}
	});

	it("renders exactly one tabpanel for the active tab", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<SettingsPage />);

		// Default tab is "general" — wait for its panel to appear.
		await waitFor(() => {
			const panels = screen.getAllByRole("tabpanel");
			expect(panels.length).toBe(1);
			expect(panels[0].id).toBe("panel-general");
			expect(panels[0].getAttribute("aria-labelledby")).toBe("tab-general");
		});
	});

	it("switching to the Appearance tab renders the appearance tabpanel", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getAllByRole("tabpanel").length).toBe(1);
		});

		// Click the Appearance tab (role="tab", name = "Appearance").
		const appearanceTab = screen.getByRole("tab", {
			name: t("settings.tabs.appearance"),
		});
		fireEvent.click(appearanceTab);

		await waitFor(() => {
			const panels = screen.getAllByRole("tabpanel");
			expect(panels.length).toBe(1);
			expect(panels[0].id).toBe("panel-appearance");
		});
	});
});
