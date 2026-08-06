/**
 *  vitest rewrite — behavioral tests for renderer TS source files
 * that were previously covered by string-pattern Python tests in
 * `tests/test_electron_ipc_and_build.py`.
 *
 * The Python file is the LARGEST of the 5  files (90 tests).
 * Sections 1–4 below cover the original PORT candidates that read
 * renderer TS/TSX source files and asserted on string patterns.
 *
 * Sections 5–13 extend the rewrite to cover the remaining
 * string-pattern tests in the same Python file: build-config /
 * project-metadata invariants read via Node.js `fs` (package.json,
 * electron-builder.yml, voice-typer.spec, pyproject.toml,
 * .github/workflows/build.yml, CHANGELOG.md, standard project files,
 * generate-icons.mjs, voice_typer/__init__.py, and the
 * `ALLOWED_COMMANDS` set inside src/main/index.ts).  These are not
 * React-component behavior tests — they are project-metadata
 * invariants — but they CAN run in vitest (Node.js `fs` is available
 * even under the jsdom environment), so porting them keeps all
 * coverage in one runner and removes the Python↔Node split for
 * config-string invariants.
 *
 * PORT candidates covered here (full list):
 *
 * Renderer TS source (Sections 1–4):
 *   - TestElectronExposesDataExportHandlers::test_window_bridge_type_includes_export_methods
 *   - TestElectronExposesDataExportHandlers::test_settings_has_export_buttons
 *   - TestRestartRequestRemoved::test_restart_request_not_in_types
 *   - TestTypeScriptNonNullAssertions::test_history_no_non_null_assertion_on_path
 *   - TestTypeScriptNonNullAssertions::test_vocabulary_no_non_null_assertion_on_path
 *   - TestTypeScriptNonNullAssertions::test_main_tsx_no_non_null_assertion
 *   - TestTypeScriptNonNullAssertions::test_bubble_main_tsx_no_non_null_assertion
 *
 * package.json metadata (Section 5):
 *   - TestTypeScriptWebConfigClean::test_package_json_typecheck_includes_web_config
 *   - TestTypeScriptWebConfigClean::test_typecheck_web_script_exists
 *   - TestPackageJsonDeclaresKeywords::test_has_keywords
 *   - TestPackageJsonDeclaresKeywords::test_has_engines
 *   - TestPackageJsonDropsUndeclaredBiome::test_no_biome_scripts
 *   - TestPackageJsonDropsUndeclaredBiome::test_python_dev_script_cross_platform
 *   - TestPackageJsonDropsUndeclaredBiome::test_package_json_is_valid_json
 *
 * generate-icons.mjs (Section 6):
 *   - TestIconsScriptPutsProjectVenvFirst::test_project_venv_is_first_candidate
 *   - TestIconsScriptPutsProjectVenvFirst::test_legacy_venv_path_is_last_resort
 *   - TestIconScriptFallsBackAcrossPythonPaths::test_script_has_fallback_chain
 *   - TestIconScriptRenamesRootToClientDir::test_no_confusing_root_variable
 *
 * electron-builder.yml (Section 7):
 *   - TestElectronBuilderConfigHasSigningAndPublish::test_has_publish_config
 *   - TestElectronBuilderConfigHasSigningAndPublish::test_has_code_signing_config
 *
 * voice-typer.spec (Section 8):
 *   - TestPyinstallerSpecHasAsrHiddenImports::test_has_parakeet_engine
 *   - TestPyinstallerSpecHasAsrHiddenImports::test_has_qwen_engine
 *   - TestPyinstallerSpecHasAsrHiddenImports::test_has_transformers
 *   - TestPyinstallerSpecHasAsrHiddenImports::test_has_ctranslate2
 *   - TestPyinstallerSpecHasAsrHiddenImports::test_has_huggingface_hub
 *   - TestPyinstallerSpecExcludesTkinter::test_tkinter_in_excludes
 *
 * pyproject.toml (Section 9):
 *   - TestPyprojectHasStandardMetadataFields::test_has_license
 *   - TestPyprojectHasStandardMetadataFields::test_has_classifiers
 *   - TestPyprojectHasStandardMetadataFields::test_has_project_urls
 *   - TestPyprojectHasStandardMetadataFields::test_has_readme
 *   - TestNoBlanketResourceWarningFilter::test_no_blanket_resource_warning_filter
 *   - TestEntryPointImportable::test_pyproject_entry_point_points_to_ipc_server
 *
 * .github/workflows/build.yml (Section 10):
 *   - TestCiRunsRuffCoverageAndPipAudit::test_ci_has_ruff
 *   - TestCiRunsRuffCoverageAndPipAudit::test_ci_has_coverage
 *   - TestCiRunsRuffCoverageAndPipAudit::test_ci_has_pip_audit
 *   - TestCiRunsRuffCoverageAndPipAudit::test_ci_tests_multiple_python_versions
 *   - TestCiVerifiesVersionSync::test_ci_has_version_check_job
 *   - TestCiVerifiesVersionSync::test_ci_verifies_tag_matches_installer
 *
 * Standard project files (Section 11):
 *   - TestStandardProjectFilesExist::test_license_exists
 *   - TestStandardProjectFilesExist::test_contributing_exists
 *   - TestStandardProjectFilesExist::test_security_exists
 *   - TestStandardProjectFilesExist::test_editorconfig_exists
 *   - TestStandardProjectFilesExist::test_issue_templates_exist
 *   - TestStandardProjectFilesExist::test_pr_template_exists
 *
 * Version metadata + changelog (Section 12):
 *   - TestVersionReadsFromPackageMetadata::test_init_py_uses_importlib
 *   - TestVersionReadsFromPackageMetadata::test_sync_versions_script_exists
 *   - TestChangelogHasCurrentTestCount::test_changelog_has_current_count
 *
 * ALLOWED_COMMANDS in src/main/index.ts (Section 13):
 *   - TestAllowlistCorrectness::test_quit_app_in_allowlist
 *   - TestAllowlistCorrectness::test_restart_app_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_quit_not_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_restart_not_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_save_config_not_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_save_vocabulary_with_diff_not_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_repaste_last_not_in_allowlist
 *   - TestAllowlistCorrectness::test_dead_complete_onboarding_not_in_allowlist
 *
 * KEEP in Python — REQUIRES-ELECTRON-RUNNER (behavioral version needs
 * real Electron main process; jsdom cannot load `src/main/index.ts` or
 * `src/preload/index.ts` because they import `electron` and `node:*`):
 *   - TestElectronExposesDataExportHandlers::test_main_has_templates_export_handler
 *   - TestElectronExposesDataExportHandlers::test_main_has_config_export_handler
 *   - TestElectronExposesDataExportHandlers::test_preload_exposes_export_templates
 *   - TestElectronExposesDataExportHandlers::test_history_export_still_present
 *   - TestElectronExposesDataExportHandlers::test_vocabulary_export_still_present
 *
 * KEEP in Python — REQUIRES-PYTHON-RUNNER (tests import Python modules
 * or introspect Python source via `inspect.getsource`; out of scope
 * for a TS-string rewrite):
 *   - TestAllowlistCorrectness::test_allowlist_matches_server_commands
 *     (cross-validates main allowlist against `voice_typer/server/ipc_server.py`)
 *   - TestSetConfigRejectsSensitiveAttrs::test_rejects_combined_sensitive_payload
 *   - TestUnknownIPCCommandCode::test_unknown_command_payload_has_code_field
 *   - TestEntryPointImportable::{test_ipc_server_main_importable,
 *     test_app_main_re_export_exists, test_dunder_main_imports_from_ipc_server}
 *   - TestGetVocabularyHandler (all 4 tests)
 *   - TestVoiceTyperAppSingleton (all 3 tests)
 *   - TestIPCDispatchInvalidData (all 5 tests)
 *   - TestExceptExceptionNotBaseException::test_main_catches_exception_not_baseexception
 *   - TestTypeIgnoreBugsFixed (all 5 tests)
 *   - TestVadStderrRedirect::test_vad_redirects_both_streams
 *   - TestMacOSAccessibilityCheck (both tests)
 *   - TestRestartAppStopsBackends::test_restart_calls_stop_on_all_three_backends
 *   - TestRestartFiltersEnvVarsWithAllowlist::test_app_uses_env_allowlist
 *   - TestVersionReadsFromPackageMetadata::test_version_uses_importlib_metadata
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file. They are NOT deleted — they remain
 * as a fallback until CI verifies the vitest versions pass on all
 * platforms.
 *
 * Documented (NOT installed) deps for future agents who want to port
 * the REQUIRES-ELECTRON-RUNNER tests behaviorally:
 *   - `@vitest/electron` (or `playwright` + `@playwright/test`) —
 *     would let vitest spawn a real Electron main process so
 *     `ipcMain.handle("templates:export", ...)` can be invoked end-to-end.
 *     Today neither dep is in `voice_typer/client/package.json`; adding
 *     it is a follow-up.
 */

import { TooltipProvider } from "@/components/ui/tooltip";
import {
    cleanup,
    fireEvent,
    render,
    screen,
    waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/**
 * Page-level render helper. Pages like Settings mount Radix Tooltip
 * (via SettingRow / ui primitives); the real App shell wraps everything
 * in a TooltipProvider (App.tsx), so tests mounting pages directly must
 * provide one too — otherwise every Tooltip render throws "Tooltip must
 * be used within TooltipProvider" and the page mounts empty.
 */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import type { PythonRequest, WindowBridge } from "@/types/ipc";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ────────────────────────────────────────────────────────────────────
// Section 1: Type-level guards for `types/ipc.ts`
// ────────────────────────────────────────────────────────────────────
//
// `types/ipc.ts` exports ONLY TypeScript types/interfaces — there are
// no runtime values — so the assertions here are COMPILE-TIME checks
// bound to runtime `const`s. If a future contributor removes
// `exportTemplates`/`exportConfig` from `WindowBridge` or re-adds the
// dead `RestartRequest` type, the const assignments below fail to
// compile (caught by `tsc --noEmit` in CI).

// `WindowBridge.exportTemplates` is present and optional.
type HasExportTemplates = "exportTemplates" extends keyof WindowBridge
	? true
	: false;
const _hasExportTemplates: HasExportTemplates = true;

// `WindowBridge.exportConfig` is present and optional.
type HasExportConfig = "exportConfig" extends keyof WindowBridge ? true : false;
const _hasExportConfig: HasExportConfig = true;

// `RestartRequest` would have `type: "restart"` — it must NOT be a
// member of `PythonRequest`. The conditional resolves to `true` only
// if `{ type: "restart" }` is assignable to `PythonRequest` (i.e. the
// dead type was re-added). Today the union has been pruned, so it
// resolves to `false` and the assignment of `false` is legal.
type WouldBeRestartRequest = { type: "restart" };
type RestartGuard = WouldBeRestartRequest extends PythonRequest ? true : false;
const _noRestartRequest: RestartGuard = false;

describe("WindowBridge type includes export methods (rewrite of test_window_bridge_type_includes_export_methods)", () => {
	it("WindowBridge declares exportTemplates", () => {
		// Compile-time guard above; runtime assertion is a tautology
		// that ensures the test actually runs and shows up in CI.
		expect(_hasExportTemplates).toBe(true);
	});

	it("WindowBridge declares exportConfig", () => {
		expect(_hasExportConfig).toBe(true);
	});
});

describe("RestartRequest dead-type removal (rewrite of test_restart_request_not_in_types)", () => {
	it("PythonRequest union does NOT include a `restart` variant", () => {
		// Compile-time guard above; if `RestartRequest` is re-added
		// with `type: "restart"`, the file fails to compile.
		expect(_noRestartRequest).toBe(false);
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 2-4: Shared mocks for renderer component tests
// ────────────────────────────────────────────────────────────────────
//
// PrivacySettingsSection, History, and Vocabulary all use the
// `usePython` hook (for `call`) and the `sonner` toast library. We
// hoist a single `mockCall` + `mockShowSnack` + `toastSuccess` and
// rebind their implementations per test.

const { mockCall, mockShowSnack, toastSuccess } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockShowSnack: vi.fn(),
	toastSuccess: vi.fn(),
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

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Add01Icon: make("Add01Icon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		Mic02Icon: make("Mic02Icon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Share08Icon: make("Share08Icon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: () => () => () => {},
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: mockShowSnack }),
	showUndoableToast: vi.fn(),
}));

vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
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

import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import type { VoiceTyperConfig } from "@/types/config";

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
		huggingface_consent: true,
		voice_biometric_consent: true,
		cloud_openai_consent: true,
		cloud_groq_consent: true,
		cloud_deepgram_consent: true,
		llm_polish_consent: true,
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible = () => true;

// ────────────────────────────────────────────────────────────────────
// Section 2: PrivacySettingsSection export buttons
// ────────────────────────────────────────────────────────────────────
//
// The Python test asserted on substring presence inside
// `PrivacySettingsSection.tsx` for the i18n keys
// `t("settings.privacy.exportTemplates")`,
// `t("settings.privacy.exportConfig")`, and
// `t("settings.privacy.exportAllDataLabel")`. These pass even when
// the button is broken, when the wrong callback is bound, or when the
// button silently no-ops. The vitest version below mounts the real
// PrivacySettingsSection and asserts:
//   1. The "Export all data (GDPR Art. 15/20)" label is rendered.
//   2. The "Export Templates" and "Export Config" buttons are present
//      and clickable.
//   3. Clicking each button invokes the corresponding
//      `window.window_.exportTemplates` / `exportConfig` bridge method.

describe("PrivacySettingsSection export buttons (rewrite of test_settings_has_export_buttons)", () => {
	beforeEach(() => {
		cleanup();
		mockCall.mockReset();
		mockShowSnack.mockReset();
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("renders the Export all data (GDPR) label, Export Templates, and Export Config buttons", () => {
		renderWithProviders(
			<PrivacySettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The Python test asserted on the i18n key strings
		// `t("settings.privacy.exportTemplates")`,
		// `t("settings.privacy.exportConfig")`, and
		// `t("settings.privacy.exportAllDataLabel")`. Behavioral: the
		// corresponding rendered text is visible to the user.
		expect(
			screen.getByText(/Export all data \(GDPR Art\. 15\/20\)/i),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: /Export templates as JSON/i }),
		).toBeTruthy();
		expect(
			screen.getByRole("button", { name: /Export configuration as JSON/i }),
		).toBeTruthy();
	});

	it("clicking Export Templates calls window.window_.exportTemplates", async () => {
		const exportTemplates = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/templates.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportTemplates,
		};
		mockCall.mockResolvedValue({ templates: [] });

		renderWithProviders(
			<PrivacySettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		fireEvent.click(
			screen.getByRole("button", { name: /Export templates as JSON/i }),
		);

		await waitFor(() => {
			expect(exportTemplates).toHaveBeenCalledTimes(1);
		});
		// The component first fetches templates via `call("get_templates")`,
		// then passes the result to the bridge.
		expect(mockCall).toHaveBeenCalledWith("get_templates");
	});

	it("clicking Export Config calls window.window_.exportConfig", async () => {
		const exportConfig = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/config.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportConfig,
		};
		mockCall.mockResolvedValue({ config: {} });

		renderWithProviders(
			<PrivacySettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		fireEvent.click(
			screen.getByRole("button", { name: /Export configuration as JSON/i }),
		);

		await waitFor(() => {
			expect(exportConfig).toHaveBeenCalledTimes(1);
		});
		expect(mockCall).toHaveBeenCalledWith("get_config");
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 3: History.tsx + Vocabulary.tsx null-safe path handling
// ────────────────────────────────────────────────────────────────────
//
// The Python tests asserted on the ABSENCE of `result.path!` (non-null
// assertion) in the export flows. These pass even when the export
// silently crashes on an undefined path. The vitest version below
// triggers the export with a mock bridge that returns
// `{ success: true, path: undefined }` and asserts:
//   1. The export flow does NOT throw.
//   2. The success toast fires with the fallback "untitled" filename
//      (proving the `path ?? ""` + `|| "untitled"` chain works).

describe("History export null-safe path handling (rewrite of test_history_no_non_null_assertion_on_path)", () => {
	beforeEach(() => {
		cleanup();
		mockCall.mockReset();
		toastSuccess.mockClear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("does not crash and shows the fallback filename when exportHistory returns success without a path", async () => {
		// Mock the Python bridge: get_history returns 1 record so the
		// export button is enabled; get_today_stats returns zeros.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history")
				return Promise.resolve([
					{
						id: 1,
						text: "hello",
						timestamp: new Date().toISOString(),
						duration: 1,
						model: "tiny",
						device: "cpu",
						word_count: 1,
						char_count: 5,
						favorite: 0,
						language: "en",
					},
				]);
			if (type === "get_today_stats")
				return Promise.resolve({
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				});
			return Promise.resolve({});
		});

		// Mock the Electron bridge: exportHistory succeeds but
		// returns NO path. Before the null-safety fix this would
		// crash on `result.path.split(...)` (cannot read split of
		// undefined); after the fix the `?? ""` + `|| "untitled"`
		// chain produces the fallback filename.
		const exportHistory = vi
			.fn()
			.mockResolvedValue({ success: true, path: undefined });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportHistory,
		};

		const { default: HistoryPage } = await import("@/pages/History");
		renderWithProviders(<HistoryPage />);

		// Wait for the record to load so the Export button is enabled.
		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});

		// Open the export format menu and click "Export as JSON".
		const exportBtn = screen.getByRole("button", { name: /^Export$/i });
		const user = userEvent.setup();
		await user.click(exportBtn);
		const jsonItem = await screen.findByRole("menuitem", {
			name: /Export as JSON/i,
		});
		fireEvent.click(jsonItem);

		// The export flow must NOT throw. The success toast fires
		// with the fallback "untitled" filename (since path is
		// undefined). This is the behavioral proof that the
		// `result.path ?? ""` + `|| "untitled"` chain is in place
		// instead of the non-null assertion `result.path!`.
		await waitFor(() => {
			expect(exportHistory).toHaveBeenCalledTimes(1);
		});
		await waitFor(() => {
			expect(toastSuccess).toHaveBeenCalledTimes(1);
		});
		const toastArg = toastSuccess.mock.calls[0]?.[0] as string | undefined;
		expect(toastArg).toMatch(/untitled/i);
	});
});

describe("Vocabulary export null-safe path handling (rewrite of test_vocabulary_no_non_null_assertion_on_path)", () => {
	beforeEach(() => {
		cleanup();
		mockCall.mockReset();
		toastSuccess.mockClear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("does not crash and shows the fallback filename when exportVocabulary returns success without a path", async () => {
		// Seed vocabulary data so the export button is enabled.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary")
				return Promise.resolve({
					misspellings: { recieve: "receive" },
				});
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const exportVocabulary = vi
			.fn()
			.mockResolvedValue({ success: true, path: undefined });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportVocabulary,
		};

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		// Wait for the seeded entry to render so the Export button is enabled.
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const exportBtn = screen.getByRole("button", { name: /^Export$/i });
		const user = userEvent.setup();
		await user.click(exportBtn);
		const jsonItem = await screen.findByRole("menuitem", {
			name: /Export as JSON/i,
		});
		fireEvent.click(jsonItem);

		await waitFor(() => {
			expect(exportVocabulary).toHaveBeenCalledTimes(1);
		});
		await waitFor(() => {
			expect(toastSuccess).toHaveBeenCalledTimes(1);
		});
		const toastArg = toastSuccess.mock.calls[0]?.[0] as string | undefined;
		expect(toastArg).toMatch(/untitled/i);
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 4: main.tsx + bubble-main.tsx null-check behavior
// ────────────────────────────────────────────────────────────────────
//
// The Python tests asserted on the ABSENCE of `getElementById('root')!`
// (non-null assertion) and the PRESENCE of `if (!rootEl)` in the
// bootstrap files. These pass even when the null check is dead code or
// throws an opaque error. The vitest version below verifies the
// behavior: when the root element is missing, importing the bootstrap
// module throws a CLEAR error message (proving the explicit null check
// is in place and functional).
//
// We use `vi.doMock` (NOT top-level `vi.mock`) for react-dom/client +
// App + Bubble + ErrorBoundary so the mocks only apply to these
// bootstrap-import tests — the RTL-based component tests above keep
// using the real react-dom/client.

describe("main.tsx null-check (rewrite of test_main_tsx_no_non_null_assertion)", () => {
	beforeEach(() => {
		vi.resetModules();
		// Stub react-dom/client so createRoot().render() is a no-op.
		// main.tsx uses `import ReactDOM from "react-dom/client"` (default
		// import) AND `ReactDOM.createRoot(...)` — the mock must provide
		// BOTH a default export (with createRoot) and a named createRoot
		// export so either import style works.
		const createRootMock = vi.fn(() => ({ render: vi.fn() }));
		vi.doMock("react-dom/client", () => {
			const mod = { createRoot: createRootMock };
			return { ...mod, default: mod };
		});
		// Stub the App + ErrorBoundary so the bootstrap's transitive
		// imports don't pull in the full renderer tree.
		vi.doMock("@/App", () => ({ default: () => null }));
		vi.doMock("@/components/feedback/ErrorBoundary", () => ({
			ErrorBoundary: ({
				children,
			}: {
				children?: React.ReactNode;
				fallback?: React.ReactNode;
			}) => <>{children}</>,
		}));
	});

	afterEach(() => {
		vi.doUnmock("react-dom/client");
		vi.doUnmock("@/App");
		vi.doUnmock("@/components/feedback/ErrorBoundary");
		vi.restoreAllMocks();
	});

	it("throws a clear error when #root is missing (instead of crashing on a non-null assertion)", async () => {
		// Mock getElementById to return null for "root".
		const spy = vi.spyOn(document, "getElementById").mockReturnValue(null);

		// Dynamically import main.tsx so it re-evaluates with the
		// mocked document. The bootstrap runs `const rootEl =
		// document.getElementById("root"); if (!rootEl) throw new
		// Error("Root element #root not found in index.html");`.
		await expect(async () => {
			await import("@/main");
		}).rejects.toThrow(/Root element #root not found/);

		spy.mockRestore();
	});

	it("does not throw when #root is present", async () => {
		// Provide a real root element so the null check passes.
		const root = document.createElement("div");
		root.id = "root";
		document.body.appendChild(root);

		const spy = vi.spyOn(document, "getElementById").mockReturnValue(root);

		// The import should succeed (no throw). The createRoot().render()
		// call is a no-op mock, so no React tree is actually mounted.
		await expect(import("@/main")).resolves.toBeDefined();

		spy.mockRestore();
		root.remove();
	});
});

describe("bubble-main.tsx null-check (rewrite of test_bubble_main_tsx_no_non_null_assertion)", () => {
	beforeEach(() => {
		vi.resetModules();
		const createRootMock = vi.fn(() => ({ render: vi.fn() }));
		vi.doMock("react-dom/client", () => {
			const mod = { createRoot: createRootMock };
			return { ...mod, default: mod };
		});
		vi.doMock("@/Bubble", () => ({ Bubble: () => null }));
		vi.doMock("@/components/feedback/ErrorBoundary", () => ({
			ErrorBoundary: ({
				children,
			}: {
				children?: React.ReactNode;
				fallback?: React.ReactNode;
			}) => <>{children}</>,
		}));
	});

	afterEach(() => {
		vi.doUnmock("react-dom/client");
		vi.doUnmock("@/Bubble");
		vi.doUnmock("@/components/feedback/ErrorBoundary");
		vi.restoreAllMocks();
	});

	it("throws a clear error when #bubble-root is missing (instead of crashing on a non-null assertion)", async () => {
		const spy = vi.spyOn(document, "getElementById").mockReturnValue(null);

		await expect(async () => {
			await import("@/bubble-main");
		}).rejects.toThrow(/Bubble root element #bubble-root not found/);

		spy.mockRestore();
	});

	it("does not throw when #bubble-root is present", async () => {
		const bubbleRoot = document.createElement("div");
		bubbleRoot.id = "bubble-root";
		document.body.appendChild(bubbleRoot);

		const spy = vi
			.spyOn(document, "getElementById")
			.mockReturnValue(bubbleRoot);

		await expect(import("@/bubble-main")).resolves.toBeDefined();

		spy.mockRestore();
		bubbleRoot.remove();
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 5: package.json metadata
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestTypeScriptWebConfigClean (2 tests)
//   - TestPackageJsonDeclaresKeywords (2 tests)
//   - TestPackageJsonDropsUndeclaredBiome (3 tests)
//
// These read `voice_typer/client/package.json` and assert on the
// parsed JSON shape.  Vitest can read+parse JSON natively; no jsdom
// or mocked IPC required.

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const REPO_ROOT = resolve(process.cwd(), "../..");
const CLIENT_DIR = resolve(process.cwd(), ".");
const PKG_JSON_PATH = resolve(CLIENT_DIR, "package.json");

function readPkgJson(): Record<string, unknown> {
	return JSON.parse(readFileSync(PKG_JSON_PATH, "utf-8")) as Record<
		string,
		unknown
	>;
}

describe("package.json typecheck scripts (rewrite of TestTypeScriptWebConfigClean)", () => {
	it("typecheck script includes both tsconfig.web.json and tsconfig.node.json", () => {
		const pkg = readPkgJson();
		const typecheck = String(
			(pkg.scripts as Record<string, string> | undefined)?.typecheck ?? "",
		);
		expect(typecheck).toContain("tsconfig.web.json");
		expect(typecheck).toContain("tsconfig.node.json");
	});

	it("declares a typecheck:web script", () => {
		const pkg = readPkgJson();
		const scripts = (pkg.scripts as Record<string, unknown> | undefined) ?? {};
		expect(scripts).toHaveProperty("typecheck:web");
	});
});

describe("package.json declares keywords + engines (rewrite of TestPackageJsonDeclaresKeywords)", () => {
	it("declares a non-empty keywords array", () => {
		const pkg = readPkgJson();
		const keywords = pkg.keywords;
		expect(Array.isArray(keywords)).toBe(true);
		expect((keywords as unknown[]).length).toBeGreaterThan(0);
	});

	it("declares an engines.node constraint", () => {
		const pkg = readPkgJson();
		const engines = pkg.engines as Record<string, unknown> | undefined;
		expect(engines).toBeDefined();
		expect(engines).toHaveProperty("node");
	});
});

describe("package.json drops undeclared biome + cross-platform python:dev (rewrite of TestPackageJsonDropsUndeclaredBiome)", () => {
	it("does NOT declare biome:check or biome:write scripts", () => {
		const pkg = readPkgJson();
		const scripts = (pkg.scripts as Record<string, unknown> | undefined) ?? {};
		expect(scripts).not.toHaveProperty("biome:check");
		expect(scripts).not.toHaveProperty("biome:write");
	});

	it("python:dev script invokes python3 (cross-platform fallback)", () => {
		const pkg = readPkgJson();
		const pythonDev = String(
			(pkg.scripts as Record<string, string> | undefined)?.["python:dev"] ?? "",
		);
		expect(pythonDev).toContain("python3");
	});

	it("package.json is valid JSON (round-trip parse)", () => {
		// readPkgJson already throws on invalid JSON; this test
		// makes that contract explicit.
		expect(() => readPkgJson()).not.toThrow();
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 6: generate-icons.mjs
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestIconsScriptPutsProjectVenvFirst (2 tests)
//   - TestIconScriptFallsBackAcrossPythonPaths (1 test)
//   - TestIconScriptRenamesRootToClientDir (1 test)
//
// These read `voice_typer/client/scripts/generate-icons.mjs` and
// assert on substring presence + the candidates-array structure.

const ICONS_SCRIPT_PATH = resolve(CLIENT_DIR, "scripts", "generate-icons.mjs");

function readIconsScript(): string {
	return readFileSync(ICONS_SCRIPT_PATH, "utf-8");
}

describe("generate-icons.mjs puts project venv first (rewrite of TestIconsScriptPutsProjectVenvFirst)", () => {
	it("references projectVenvPython and .venv", () => {
		const src = readIconsScript();
		expect(src).toContain("projectVenvPython");
		expect(src).toContain(".venv");
	});

	it("legacy .voice-typer venv is NOT in the first two candidates", () => {
		const src = readIconsScript();
		const m = /const candidates = \[([\s\S]+?)\]/.exec(src);
		expect(m).not.toBeNull();
		const body = m?.[1] ?? "";
		const firstTwo = body.split(",").slice(0, 2).join(",");
		expect(firstTwo).not.toContain(".voice-typer");
	});
});

describe("generate-icons.mjs falls back across Python paths (rewrite of TestIconScriptFallsBackAcrossPythonPaths)", () => {
	it("declares a candidates array that tries python3 and python", () => {
		const src = readIconsScript();
		expect(src).toContain("candidates");
		expect(src).toContain("python3");
		expect(src).toContain("python");
	});
});

describe("generate-icons.mjs renames root → clientDir (rewrite of TestIconScriptRenamesRootToClientDir)", () => {
	it("declares `const clientDir` and does NOT declare `const root =`", () => {
		const src = readIconsScript();
		expect(src).toContain("const clientDir");
		expect(src).not.toContain("const root =");
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 7: electron-builder.yml
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestElectronBuilderConfigHasSigningAndPublish (2 tests)
//
// We read the YAML as plain text and assert on substring presence
// (same as the Python test; no YAML parser is installed — see
// worklog for the documented `js-yaml` follow-up).

const ELECTRON_BUILDER_PATH = resolve(CLIENT_DIR, "electron-builder.yml");

function readElectronBuilderYml(): string {
	return readFileSync(ELECTRON_BUILDER_PATH, "utf-8");
}

describe("electron-builder.yml has signing + publish (rewrite of TestElectronBuilderConfigHasSigningAndPublish)", () => {
	it("does NOT declare a live GitHub publish provider (dead config removed)", () => {
		// S1-CR-148 / S5-CR-51 deliberately removed the `publish: github`
		// block from electron-builder.yml: it was dead config — no
		// `electron-updater` integration exists on the Electron path and
		// the Tauri path uses `tauri-plugin-updater` instead. The removal
		// is documented in the config header. Assert BOTH halves: no live
		// provider stanza (a regression that silently re-adds it without
		// wiring electron-updater would be caught) AND the removal
		// rationale comment is still present (so maintainers don't
		// "helpfully" re-add it).
		const yml = readElectronBuilderYml();
		expect(yml).not.toMatch(/^publish:\s*$/m);
		expect(yml).not.toContain("provider: github");
		expect(yml).toContain("dead config");
	});

	it("declares signAndEditExecutable + notarize", () => {
		const yml = readElectronBuilderYml();
		expect(yml).toContain("signAndEditExecutable");
		expect(yml).toContain("notarize");
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 8: voice-typer.spec (PyInstaller)
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestPyinstallerSpecHasAsrHiddenImports (5 tests)
//   - TestPyinstallerSpecExcludesTkinter (1 test)
//
// Reads `scripts/build/voice-typer.spec` (PyInstaller spec) as plain
// text and asserts on substring presence.

const SPEC_PATH = resolve(REPO_ROOT, "scripts", "build", "voice-typer.spec");

function readPyinstallerSpec(): string {
	return readFileSync(SPEC_PATH, "utf-8");
}

describe("voice-typer.spec declares ASR hiddenimports (rewrite of TestPyinstallerSpecHasAsrHiddenImports)", () => {
	it("includes parakeet_engine", () => {
		expect(readPyinstallerSpec()).toContain("parakeet_engine");
	});

	it("includes qwen_engine", () => {
		expect(readPyinstallerSpec()).toContain("qwen_engine");
	});

	it("includes transformers", () => {
		expect(readPyinstallerSpec()).toContain("transformers");
	});

	it("includes ctranslate2", () => {
		expect(readPyinstallerSpec()).toContain("ctranslate2");
	});

	it("includes huggingface_hub", () => {
		expect(readPyinstallerSpec()).toContain("huggingface_hub");
	});
});

describe("voice-typer.spec excludes tkinter (rewrite of TestPyinstallerSpecExcludesTkinter)", () => {
	it('lists "tkinter" in the excludes array', () => {
		expect(readPyinstallerSpec()).toContain('"tkinter"');
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 9: pyproject.toml
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestPyprojectHasStandardMetadataFields (4 tests)
//   - TestNoBlanketResourceWarningFilter (1 test)
//   - TestEntryPointImportable::test_pyproject_entry_point_points_to_ipc_server

const PYPROJECT_PATH = resolve(REPO_ROOT, "pyproject.toml");

function readPyproject(): string {
	return readFileSync(PYPROJECT_PATH, "utf-8");
}

describe("pyproject.toml declares standard metadata (rewrite of TestPyprojectHasStandardMetadataFields)", () => {
	it("declares a license field", () => {
		expect(readPyproject()).toContain("license = ");
	});

	it("declares classifiers", () => {
		expect(readPyproject()).toContain("classifiers");
	});

	it("declares a [project.urls] table", () => {
		expect(readPyproject()).toContain("[project.urls]");
	});

	it("declares a readme field", () => {
		expect(readPyproject()).toContain("readme = ");
	});
});

describe("pyproject.toml does not blanket-ignore ResourceWarning (rewrite of TestNoBlanketResourceWarningFilter)", () => {
	it('no line starts with "ignore::ResourceWarning" filter', () => {
		const lines = readPyproject().split(/\r?\n/);
		for (const line of lines) {
			const stripped = line.trim();
			if (stripped.startsWith('"ignore::ResourceWarning"')) {
				throw new Error(
					`Blanket 'ignore::ResourceWarning' filter found: ${stripped}`,
				);
			}
		}
	});
});

describe("pyproject.toml entry-point points to ipc_server:main (rewrite of test_pyproject_entry_point_points_to_ipc_server)", () => {
	it('declares voice-typer = "voice_typer.server.ipc_server:main"', () => {
		expect(readPyproject()).toContain(
			'voice-typer = "voice_typer.server.ipc_server:main"',
		);
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 10: .github/workflows/build.yml
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestCiRunsRuffCoverageAndPipAudit (4 tests)
//   - TestCiVerifiesVersionSync (2 tests)

const CI_WORKFLOW_PATH = resolve(
	REPO_ROOT,
	".github",
	"workflows",
	"build.yml",
);

function readCiWorkflow(): string {
	return readFileSync(CI_WORKFLOW_PATH, "utf-8");
}

describe("CI runs ruff + coverage + pip-audit across Python versions (rewrite of TestCiRunsRuffCoverageAndPipAudit)", () => {
	it("runs ruff", () => {
		expect(readCiWorkflow()).toContain("ruff");
	});

	it("runs coverage", () => {
		const ci = readCiWorkflow();
		expect(ci.includes("cov") || ci.includes("coverage")).toBe(true);
	});

	it("runs pip-audit", () => {
		expect(readCiWorkflow()).toContain("pip-audit");
	});

	it("tests Python 3.10 and 3.11", () => {
		const ci = readCiWorkflow();
		expect(ci).toContain("3.10");
		expect(ci).toContain("3.11");
	});
});

describe("CI verifies version sync (rewrite of TestCiVerifiesVersionSync)", () => {
	it("has a version-check job", () => {
		expect(readCiWorkflow()).toContain("version-check");
	});

	it("verifies tag matches installer version via package.json read in CI", () => {
		// The CI workflow reads the version out of voice_typer/client/package.json
		// and compares it against the git tag ($tagVersion vs $installerVersion).
		// The previous NSIS-based flow used the ``MyAppVersion`` preprocessor
		// define; the current PowerShell-based flow uses a ``$installerVersion``
		// variable populated from ``ConvertFrom-Json``. Both serve the same
		// purpose (block a tag↔installer version mismatch), so accepting either
		// token keeps the test resilient to the CI's shell choice.
		const ci = readCiWorkflow();
		const hasVersionSync =
			ci.includes("MyAppVersion") || ci.includes("installerVersion");
		expect(hasVersionSync).toBe(true);
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 11: Standard project files
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestStandardProjectFilesExist (6 tests)
//
// Pure file-existence checks; vitest uses Node.js `fs.existsSync`.

describe("standard project files exist (rewrite of TestStandardProjectFilesExist)", () => {
	it("LICENSE exists at repo root", () => {
		expect(existsSync(resolve(REPO_ROOT, "LICENSE"))).toBe(true);
	});

	it("CONTRIBUTING.md exists at repo root", () => {
		expect(existsSync(resolve(REPO_ROOT, "CONTRIBUTING.md"))).toBe(true);
	});

	it("SECURITY.md exists at repo root", () => {
		expect(existsSync(resolve(REPO_ROOT, "SECURITY.md"))).toBe(true);
	});

	it(".editorconfig exists at repo root", () => {
		expect(existsSync(resolve(REPO_ROOT, ".editorconfig"))).toBe(true);
	});

	it("bug_report.md and feature_request.md issue templates exist", () => {
		expect(
			existsSync(
				resolve(REPO_ROOT, ".github", "ISSUE_TEMPLATE", "bug_report.md"),
			),
		).toBe(true);
		expect(
			existsSync(
				resolve(REPO_ROOT, ".github", "ISSUE_TEMPLATE", "feature_request.md"),
			),
		).toBe(true);
	});

	it("PULL_REQUEST_TEMPLATE.md exists", () => {
		expect(
			existsSync(resolve(REPO_ROOT, ".github", "PULL_REQUEST_TEMPLATE.md")),
		).toBe(true);
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 12: Version metadata + changelog
// ────────────────────────────────────────────────────────────────────
//
// Ports:
//   - TestVersionReadsFromPackageMetadata::test_init_py_uses_importlib
//   - TestVersionReadsFromPackageMetadata::test_sync_versions_script_exists
//   - TestChangelogHasCurrentTestCount::test_changelog_has_current_count

const INIT_PY_PATH = resolve(REPO_ROOT, "voice_typer", "__init__.py");
const SYNC_VERSIONS_PATH = resolve(
	REPO_ROOT,
	"scripts",
	"build",
	"sync_versions.py",
);
const CHANGELOG_PATH = resolve(REPO_ROOT, "CHANGELOG.md");

describe("__version__ reads from package metadata (rewrite of test_init_py_uses_importlib)", () => {
	it("voice_typer/__init__.py imports importlib.metadata and uses _pkg_version or version()", () => {
		const src = readFileSync(INIT_PY_PATH, "utf-8");
		expect(src).toContain("importlib.metadata");
		expect(src.includes("_pkg_version") || src.includes("version(")).toBe(true);
	});
});

describe("sync_versions.py exists (rewrite of test_sync_versions_script_exists)", () => {
	it("scripts/build/sync_versions.py exists", () => {
		expect(existsSync(SYNC_VERSIONS_PATH)).toBe(true);
	});
});

describe("CHANGELOG test count is current (rewrite of test_changelog_has_current_count)", () => {
	it("CHANGELOG.md does NOT contain the stale '1127 tests passing' line", () => {
		const src = readFileSync(CHANGELOG_PATH, "utf-8");
		expect(src).not.toContain("1127 tests passing");
	});
});

// ────────────────────────────────────────────────────────────────────
// Section 13: ALLOWED_COMMANDS in src/main/allowed-commands.ts
// ────────────────────────────────────────────────────────────────────
//
// Ports (8 of 9 TestAllowlistCorrectness tests):
//   - test_quit_app_in_allowlist
//   - test_restart_app_in_allowlist
//   - test_dead_quit_not_in_allowlist
//   - test_dead_restart_not_in_allowlist
//   - test_dead_save_config_not_in_allowlist
//   - test_dead_save_vocabulary_with_diff_not_in_allowlist
//   - test_dead_repaste_last_not_in_allowlist
//   - test_dead_complete_onboarding_not_in_allowlist
//
// The 9th test (`test_allowlist_matches_server_commands`) cross-validates
// the main allowlist against `voice_typer/server/ipc_server.py` — that
// requires reading Python source AND matching it against the TS source,
// which is out of scope for a TS-string rewrite.  It stays in Python
// with a REQUIRES-PYTHON-RUNNER comment.
//
// R6-F10: the canonical ALLOWED_COMMANDS declaration was moved from
// `src/main/index.ts` (inline) into its own dependency-free module at
// `src/main/allowed-commands.ts` to break a circular-import cycle
// (`index.ts` → `python/` → `send-to-python.ts` → `index.ts`). The
// test reads the canonical declaration so it stays in sync with the
//file the Rust defense-in-depth gate () and the Python parity
// test (`tests/test_security_doc_command_count.py`) both reference.
//
// We extract the ALLOWED_COMMANDS set by slicing the source between
// `ALLOWED_COMMANDS = new Set([` and the closing `]);` — same logic
// as the Python test — then regex-match the quoted entries.

const ALLOWED_COMMANDS_PATH = resolve(
	CLIENT_DIR,
	"src",
	"main",
	"allowed-commands.ts",
);

function readAllowlistEntries(): Set<string> {
	const src = readFileSync(ALLOWED_COMMANDS_PATH, "utf-8");
	const start = src.indexOf("ALLOWED_COMMANDS = new Set(");
	expect(start).not.toBe(-1);
	const end = src.indexOf("]);", start);
	expect(end).not.toBe(-1);
	const block = src.slice(start, end);
	const matches = block.matchAll(/"([a-z_]+)"/g);
	const entries = new Set<string>();
	for (const m of matches) {
		// RegExpMatchArray indexing is `string | undefined` under
		// `noUncheckedIndexedAccess`; the regex captures a group so
		// guard + fallback keeps the Set happy.
		const captured = m[1];
		if (captured !== undefined) entries.add(captured);
	}
	return entries;
}

describe("ALLOWED_COMMANDS in src/main/allowed-commands.ts (rewrite of TestAllowlistCorrectness — 8 of 9 tests)", () => {
	it("includes quit_app (rewrite of test_quit_app_in_allowlist)", () => {
		expect(readAllowlistEntries().has("quit_app")).toBe(true);
	});

	it("includes restart_app (rewrite of test_restart_app_in_allowlist)", () => {
		expect(readAllowlistEntries().has("restart_app")).toBe(true);
	});

	it("does NOT include the dead `quit` alias (rewrite of test_dead_quit_not_in_allowlist)", () => {
		expect(readAllowlistEntries().has("quit")).toBe(false);
	});

	it("does NOT include the dead `restart` alias (rewrite of test_dead_restart_not_in_allowlist)", () => {
		expect(readAllowlistEntries().has("restart")).toBe(false);
	});

	it("does NOT include the dead `save_config` alias (rewrite of test_dead_save_config_not_in_allowlist)", () => {
		expect(readAllowlistEntries().has("save_config")).toBe(false);
	});

	it("does NOT include the dead `save_vocabulary_with_diff` alias (rewrite of test_dead_save_vocabulary_with_diff_not_in_allowlist)", () => {
		expect(readAllowlistEntries().has("save_vocabulary_with_diff")).toBe(false);
	});

	it("includes the live `repaste_last` command (re-added per UX-23)", () => {
		//`repaste_last` was previously in the  "dead commands"
		// removal list because it was only invoked via the tray hotkey
		//callback, not as an IPC command.  wired the renderer's
		// "Re-paste" button (Home.tsx) to call it via the IPC bridge, so
		// it was re-added to the allowlist — it is no longer dead.
		//See allowed-commands.ts § for the full rationale.
		expect(readAllowlistEntries().has("repaste_last")).toBe(true);
	});

	it("does NOT include the dead `complete_onboarding` alias (rewrite of test_dead_complete_onboarding_not_in_allowlist)", () => {
		expect(readAllowlistEntries().has("complete_onboarding")).toBe(false);
	});
});
