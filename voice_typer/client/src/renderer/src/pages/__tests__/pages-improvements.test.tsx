/**
 * I10-retry: regression tests for the React renderer PAGE improvements.
 *
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

// SOURCE (the hook must pass one callback to both usePythonEvent
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	lastUpdatedMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@/hooks/useLastUpdated", () => lastUpdatedMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { setLocale, t } from "@/i18n/i18n";
import ModelsPage from "@/pages/Models";
import OnboardingPage from "@/pages/Onboarding";
import SettingsPage from "@/pages/Settings";
import TemplatesPage from "@/pages/Templates";
import VocabularyPage from "@/pages/Vocabulary";
import type { VoiceTyperConfig } from "@/types/config";

const MINIMAL_CONFIG: VoiceTyperConfig = {
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
	resetStableMocks();
	localStorage.clear();
	setLocale("en");
});

afterEach(() => {
	cleanup();
});

describe("R7-F8: Onboarding init effect uses cancelled-flag guard", () => {
	it("source contains cancelled flag + cleanup return", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/onboarding/hooks/useOnboardingWizard.ts",
			"utf8",
		);
		expect(src).toContain("let cancelled = false;");
		expect(src).toContain("if (cancelled) return;");
		expect(src).toMatch(/return \(\) => \{[^}]*cancelled = true/);
	});

	it("does not call setState after unmount (no React warning)", async () => {
		const pendingResolvers: Array<(v: unknown) => void> = [];
		mockCall.mockImplementation(() => {
			return new Promise((resolve) => {
				pendingResolvers.push(resolve);
			});
		});

		const seenErrors: string[] = [];
		const origError = console.error;
		console.error = (...args: unknown[]) => {
			seenErrors.push(args.map(String).join(" "));
		};

		try {
			const { unmount } = render(<OnboardingPage onComplete={() => {}} />);
			unmount();
			for (const resolve of pendingResolvers) {
				resolve({ step: 0, total_steps: 6, step_name: "welcome" });
			}
			await new Promise((r) => setTimeout(r, 10));
		} finally {
			console.error = origError;
		}

		// been emitted.
		const offending = seenErrors.filter(
			(s) => s.includes("unmounted") || s.includes("setState"),
		);
		expect(offending).toEqual([]);
	});
});

describe("R7-F9: Models.tsx, no dead benchmark UI", () => {
	it("source contains no isBenchmarking / runBenchmark / BenchmarkSection", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Models.tsx", "utf8");
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

		renderWithProviders(<ModelsPage />);
		await waitFor(() => {
			expect(screen.queryByRole("heading", { name: /Models/i })).toBeTruthy();
		});

		expect(screen.queryByRole("button", { name: /benchmark/i })).toBeNull();
	});
});

describe("R7-F10: Vocabulary + Templates, no dead ConfirmDialog", () => {
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
		expect(stripped).not.toContain("handleCancelDelete");
		// its symbols are pinned above. Do NOT re-add a
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
		expect(stripped).not.toContain("handleCancelDelete");
		// symbols are pinned above. Do NOT re-add a
	});

	it('Vocabulary renders no role="alertdialog" (ConfirmDialog removed)', async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") return Promise.resolve({});
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		renderWithProviders(<VocabularyPage />);
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

		renderWithProviders(<TemplatesPage />);
		await waitFor(() => {
			expect(screen.getByText(t("templates.addTemplate"))).toBeTruthy();
		});
		expect(screen.queryByRole("alertdialog")).toBeNull();
	});
});

describe("R7-F11: Vocabulary + Templates, i18n placeholders", () => {
	it("en.json contains the four placeholder keys", async () => {
		const en = (await import("@/i18n/translations/en.json")).default as Record<
			string,
			unknown
		>;
		expect(
			(en.vocabulary as Record<string, unknown>).triggerPlaceholder,
		).toBeTruthy();
		expect(
			(en.vocabulary as Record<string, unknown>).replacementPlaceholder,
		).toBeTruthy();
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

		renderWithProviders(<VocabularyPage />);
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

	it("Templates Add dialog renders the i18n trigger/output placeholders; the Edit dialog keeps them", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates")
				return Promise.resolve({
					templates: [
						{
							trigger: "brb",
							output: "be right back",
							match_mode: "exact",
						},
					],
				});
			if (type === "save_templates") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		renderWithProviders(<TemplatesPage />);
		await waitFor(() => {
			expect(screen.getByText(t("templates.addTemplate"))).toBeTruthy();
		});

		fireEvent.click(screen.getByText(t("templates.addTemplate")));
		const dialogTrigger = (await screen.findByLabelText(
			t("templates.triggerPhrase"),
			{ selector: "input" },
		)) as HTMLInputElement;
		const dialogOutput = screen.getByLabelText(t("templates.outputText"), {
			selector: "textarea",
		}) as HTMLTextAreaElement;

		expect(dialogTrigger.placeholder).toBe(t("templates.triggerPlaceholder"));
		expect(dialogOutput.placeholder).toBe(t("templates.outputPlaceholder"));

		fireEvent.click(screen.getByRole("button", { name: t("common.cancel") }));
		await waitFor(() => {
			expect(screen.queryByLabelText(t("templates.triggerPhrase"))).toBeNull();
		});

		fireEvent.click(
			screen.getByLabelText(t("templates.editAria", { name: "brb" })),
		);
		const editTrigger = (await screen.findByLabelText(
			t("templates.triggerPhrase"),
			{ selector: "input" },
		)) as HTMLInputElement;
		const editOutput = screen.getByLabelText(t("templates.outputText"), {
			selector: "textarea",
		}) as HTMLTextAreaElement;

		expect(editTrigger.placeholder).toBe(t("templates.triggerPlaceholder"));
		expect(editOutput.placeholder).toBe(t("templates.outputPlaceholder"));
	});
});

describe("R7-F12: Models.tsx, display_name fallback for variant heading", () => {
	it("variant headings derive from getModelVariantDisplayName (no hardcoded strings)", async () => {
		const fs = await import("node:fs");
		const panelSrc = fs.readFileSync(
			"src/renderer/src/components/models/LocalModelsPanel.tsx",
			"utf8",
		);
		const panelStripped = panelSrc
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(panelStripped).toContain("getModelVariantDisplayName(");
		// The old hardcoded ternary must be gone.
		expect(panelStripped).not.toContain("meta?.display_name ?? model.name");
		expect(panelStripped).not.toContain('"Qwen3-ASR-1.7B"');
		expect(panelStripped).not.toContain('"NVIDIA Parakeet TDT v3"');

		const libSrc = fs.readFileSync(
			"src/renderer/src/lib/utils/models.ts",
			"utf8",
		);
		const libStripped = libSrc
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(libStripped).toContain("meta?.display_name");
		expect(libStripped).toContain("formatModelDisplayName(model.name)");
	});

	it("ModelMetadata interface includes display_name field", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/lib/utils/models.ts", "utf8");
		const idx = src.indexOf("interface ModelMetadata");
		expect(idx).toBeGreaterThanOrEqual(0);
		const slice = src.slice(idx, idx + 600);
		expect(slice).toMatch(/display_name\??:\s*string/);
	});
});

describe("R7-F13: History + Home, debouncedRefreshFromEvent via useCallback", () => {
	it("History's refresh hook declares debouncedRefreshFromEvent via useCallback and passes it to both usePythonEvent calls", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/history/hooks/useHistoryEventRefresh.ts",
			"utf8",
		);
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		const matches = src.match(/usePythonEvent\(/g) ?? [];
		expect(matches.length).toBeGreaterThanOrEqual(2);
		// must pass the shared callback.
		expect(src).toMatch(/usePythonEvent\(\s*["`]transcription_final["`]/);
		expect(src).toMatch(/usePythonEvent\(\s*["`]history_changed["`]/);
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		expect(uses.length).toBeGreaterThanOrEqual(3); // 1 decl + 2 uses
	});

	it("Home.tsx source declares debouncedRefreshFromEvent via useCallback and passes it to both usePythonEvent calls", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Home.tsx", "utf8");
		expect(src).toContain("const debouncedRefreshFromEvent = useCallback(");
		const uses = src.match(/debouncedRefreshFromEvent\b/g) ?? [];
		expect(uses.length).toBeGreaterThanOrEqual(3);
	});
});

describe("R7-F15: DiagnosticsSettingsSection, configDir starts empty and falls back to t('about.loading')", () => {
	it("source initialises configDir with empty string", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/components/settings/DiagnosticsSettingsSection.tsx",
			"utf8",
		);
		expect(src).toContain('useState<string>("")');
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain('"~/.voice-typer"');
		expect(stripped).toContain('t("about.loading")');
		expect(stripped).not.toContain('"Loading…"');
	});

	it("renders t('about.loading') as the config-directory value before the backend resolves", async () => {
		const { DiagnosticsSettingsSection } = await import(
			"@/components/settings/DiagnosticsSettingsSection"
		);
		mockCall.mockImplementation(() => new Promise(() => {}));

		renderWithProviders(<DiagnosticsSettingsSection isVisible={() => true} />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: t("about.diagnosticsTitle") }),
			).toBeTruthy();
		});

		expect(screen.getByText(t("about.loading"))).toBeTruthy();
		// The hardcoded "~/.voice-typer" string must NOT appear.
		expect(screen.queryByText("~/.voice-typer")).toBeNull();
	});
});

describe("R7-F16: History.tsx, Load More reveals paged rows (no dead zone)", () => {
	function makeRecord(index: number) {
		return {
			id: index + 1,
			text: `Record ${index + 1}`,
			timestamp: new Date(Date.now() - index * 1000).toISOString(),
			duration: 1,
			model: "tiny",
			device: "cpu",
			word_count: 2,
			char_count: 9,
			favorite: 0,
			language: "en",
		};
	}

	it("renders only the first page initially, then reveals the appended rows after Load More", async () => {
		let historyCalls = 0;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_today_stats") {
				return Promise.resolve({
					count: 60,
					chars: 600,
					word_count: 120,
					duration: 60,
				});
			}
			if (type === "get_history") {
				historyCalls += 1;
				const start = historyCalls === 1 ? 0 : 50;
				const count = historyCalls === 1 ? 50 : 10;
				return Promise.resolve(
					Array.from({ length: count }, (_, i) => makeRecord(start + i)),
				);
			}
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		renderWithProviders(<HistoryPage />);

		// First paint shows exactly the first page, row 51 must NOT be
		await waitFor(() => {
			expect(screen.getByText("Record 1")).toBeTruthy();
		});
		expect(screen.getByText("Record 50")).toBeTruthy();
		expect(screen.queryByText("Record 51")).toBeNull();

		fireEvent.click(
			screen.getByRole("button", { name: t("history.loadMore") }),
		);

		await waitFor(() => {
			expect(screen.getByText("Record 60")).toBeTruthy();
		});
		expect(historyCalls).toBe(2);
	});
});

describe("R7-F18: Dashboard.tsx, dead setLoading removed", () => {
	it("source has no live setLoading calls (comments allowed)", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync("src/renderer/src/pages/Dashboard.tsx", "utf8");
		const stripped = src
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/.*$/gm, "");
		expect(stripped).not.toContain("setLoading(");
		expect(stripped).not.toMatch(
			/const\s*\[[^,]*,\s*setLoading\]\s*=\s*useState/,
		);
	});
});

describe("CR-57: Microphone.tsx, polling gated on visibility + active state", () => {
	it("source checks document.visibilityState and the testRunning/micMonitoring refs inside the interval", async () => {
		const fs = await import("node:fs");
		const src = fs.readFileSync(
			"src/renderer/src/pages/microphone/hooks/useMicrophoneLevelMonitor.ts",
			"utf8",
		);
		expect(src).toContain("document.visibilityState");
		expect(src).toContain('"visible"');
		expect(src).toContain("testRunningRef.current");
		expect(src).toContain("micMonitoringRef.current");
		expect(src).toMatch(
			/!testRunningRef\.current\s*&&\s*!micMonitoringRef\.current/,
		);
	});

	it("does not call microphone_test_get_level while document is hidden", async () => {
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

		const original = document.visibilityState;
		Object.defineProperty(document, "visibilityState", {
			configurable: true,
			get: () => "hidden",
		});

		try {
			const MicrophonePage = (await import("@/pages/Microphone"))
				.default as unknown as React.FC<{
				testRunning?: boolean;
			}>;
			vi.useFakeTimers();
			try {
				renderWithProviders(<MicrophonePage testRunning />);

				await vi.advanceTimersByTimeAsync(250);
			} finally {
				vi.useRealTimers();
			}

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

describe("Settings.tsx, hub + section pages render model", () => {
	it("renders only the Audio section's cards on the Audio section page", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		renderWithProviders(<SettingsPage page="settingsAudio" />);

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: "Audio Enhancement" }),
			).toBeTruthy();
		});

		expect(
			screen.queryByRole("button", { name: "Re-run setup wizard" }),
		).toBeNull();
		expect(screen.queryByText("Troubleshooting")).toBeNull();
	});

	it("renders the hub card of section rows on the settings landing page", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MINIMAL_CONFIG);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		renderWithProviders(<SettingsPage page="settings" />);

		await waitFor(() => {
			expect(
				document.querySelector(
					'[data-testid="settings-hub-row-settingsGeneral"]',
				),
			).toBeTruthy();
		});
		expect(
			document.querySelector(
				'[data-testid="settings-hub-row-settingsAdvanced"]',
			),
		).toBeTruthy();
	});
});
