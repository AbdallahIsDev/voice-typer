/**
 * RW-1 vitest rewrite — behavioral tests for renderer TS source files
 * that were previously covered by string-pattern Python tests in
 * `tests/test_electron_ipc_and_build.py`.
 *
 * The Python file is the LARGEST of the 5 RW-1 files (90 tests). Most
 * of its tests assert on Python source, `package.json`,
 * `electron-builder.yml`, `.github/workflows/build.yml`, or
 * `voice-typer.spec` — those are KEEP (build infrastructure, not
 * renderer behavior). The tests below cover the PORT candidates that
 * read renderer TS/TSX source files and asserted on string patterns.
 *
 * PORT candidates covered here (renderer TS source — behaviorally
 * testable in vitest/jsdom):
 *   - TestElectronExposesDataExportHandlers::test_window_bridge_type_includes_export_methods
 *   - TestElectronExposesDataExportHandlers::test_settings_has_export_buttons
 *   - TestRestartRequestRemoved::test_restart_request_not_in_types
 *   - TestTypeScriptNonNullAssertions::test_history_no_non_null_assertion_on_path
 *   - TestTypeScriptNonNullAssertions::test_vocabulary_no_non_null_assertion_on_path
 *   - TestTypeScriptNonNullAssertions::test_main_tsx_no_non_null_assertion
 *   - TestTypeScriptNonNullAssertions::test_bubble_main_tsx_no_non_null_assertion
 *
 * KEEP in Python (Electron main/preload — can't run in jsdom vitest;
 * the vitest config only includes `src/renderer/src/**`):
 *   - TestElectronExposesDataExportHandlers::test_main_has_templates_export_handler
 *   - TestElectronExposesDataExportHandlers::test_main_has_config_export_handler
 *   - TestElectronExposesDataExportHandlers::test_preload_exposes_export_templates
 *   - TestElectronExposesDataExportHandlers::test_history_export_still_present
 *   - TestElectronExposesDataExportHandlers::test_vocabulary_export_still_present
 *   - TestAllowlistCorrectness (all 9 tests — they read `src/main/index.ts`
 *     and/or `voice_typer/server/ipc_server.py`; the former can't run in
 *     jsdom and the latter is Python source).
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file. They are NOT deleted — they remain
 * as a fallback until CI verifies the vitest versions pass on all
 * platforms.
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PythonRequest, WindowBridge } from "@/types/ipc";

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

describe("RW-1: WindowBridge type includes export methods (rewrite of test_window_bridge_type_includes_export_methods)", () => {
	it("WindowBridge declares exportTemplates", () => {
		// Compile-time guard above; runtime assertion is a tautology
		// that ensures the test actually runs and shows up in CI.
		expect(_hasExportTemplates).toBe(true);
	});

	it("WindowBridge declares exportConfig", () => {
		expect(_hasExportConfig).toBe(true);
	});
});

describe("RW-1: RestartRequest dead-type removal (rewrite of test_restart_request_not_in_types)", () => {
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

describe("RW-1: PrivacySettingsSection export buttons (rewrite of test_settings_has_export_buttons)", () => {
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
		render(
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

		render(
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

		render(
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

describe("RW-1: History export null-safe path handling (rewrite of test_history_no_non_null_assertion_on_path)", () => {
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
		render(<HistoryPage />);

		// Wait for the record to load so the Export button is enabled.
		await waitFor(() => {
			expect(screen.getByText("hello")).toBeTruthy();
		});

		// Open the export format menu and click "Export as JSON".
		const exportBtn = screen.getByRole("button", { name: /^Export$/i });
		fireEvent.click(exportBtn);
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

describe("RW-1: Vocabulary export null-safe path handling (rewrite of test_vocabulary_no_non_null_assertion_on_path)", () => {
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
		render(<VocabularyPage />);

		// Wait for the seeded entry to render so the Export button is enabled.
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		const exportBtn = screen.getByRole("button", { name: /^Export$/i });
		fireEvent.click(exportBtn);
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

describe("RW-1: main.tsx null-check (rewrite of test_main_tsx_no_non_null_assertion)", () => {
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

describe("RW-1: bubble-main.tsx null-check (rewrite of test_bubble_main_tsx_no_non_null_assertion)", () => {
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
