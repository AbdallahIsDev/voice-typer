/**
 * unit tests for `hooks/useModelLifecycle.ts` — the facade composing
 * the Models-page sub-hooks (Phase 4.5 spaghetti split).
 *
 * The facade wires 5 sub-hooks together:
 *   1. `useModelConfig`     → config + models + catalog + apiKeys + 4
 *                              internal helpers (refreshModelStatus,
 *                              updateConfig, setConfig, setModels)
 *   2. `useModelDownload`   → receives setModels + refreshModelStatus
 *   3. `useModelSelection`  → receives setModels + refreshModelStatus +
 *                              updateConfig
 *   4. `useCloudProviders`  → receives setConfig + config + apiKeys +
 *                              updateConfig + call
 *   5. `useModelFolder`     → receives loadConfig
 *
 * The 4 internal helpers from `useModelConfig` are destructured OUT of the
 * public return shape (they're not part of the pre-split facade contract).
 * The merged return is `{ ...configRest, ...download, ...selection,
 * ...cloud, ...folder, cloudProviders }` — `agoLabel` was removed when the
 * "Last updated / refresh" indicator was removed from the Models page.
 *
 * Coverage:
 *   1. Lifecycle ordering: sub-hooks are invoked in the order
 *      useModelConfig → useModelDownload → useModelSelection →
 *      useCloudProviders → useModelFolder (so each subsequent hook can
 *      receive helpers destructured from the prior one's return).
 *   2. Cancel-mid-download wiring: `useModelDownload` receives
 *      `setModels` + `refreshModelStatus` AS THE SAME REFERENCES returned
 *      by `useModelConfig`. When the download sub-hook's
 *      `handleCancelDownload` calls `setModels(prev => ...)`, it mutates
 *      the SAME state owned by `useModelConfig` — without this referential
 *      equality, cancel-state-reset would silently no-op.
 *   3. Post-install activation wiring: `useModelDownload` receives
 *      `refreshModelStatus` (forwarded from `useModelConfig`) so its
 *      `installDeps` action can reconcile the deps-installed state after
 *      a successful install. Without this forwarding, `installDeps`
 *      would silently skip the reconciliation step.
 *   4. The 4 internal helpers (refreshModelStatus, updateConfig,
 *      setConfig, setModels) are NOT in the public return shape.
 *   5. The return shape is the merged set of sub-hook returns + the
 *      static `cloudProviders` array (`agoLabel` removed with the
 *      refresh indicator).
 *   6. `ApiTestResult` type re-export still resolves (back-compat with
 *      CloudProvidersPanel.tsx and its tests).
 */
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// usePython / useSnackbar / useLastUpdated singletons the facade
// forwards to its sub-hooks.
import {
	lastUpdatedMock,
	pythonMock,
	snackbarMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const {
	mockCall,
	showSnack: mockShowSnack,
	markUpdated: mockMarkUpdated,
} = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@/hooks/useLastUpdated", () => lastUpdatedMock());

// ── Hoisted mock state ────────────────────────────────────────────────
// vi.mock factories are hoisted to the top of the file by vitest and run
// before any other code, so any variable they close over must itself be
// hoisted via vi.hoisted().
const {
	callOrder,
	configHookReturn,
	downloadHookReturn,
	selectionHookReturn,
	cloudHookReturn,
	folderHookReturn,
	mockCLOUD_PROVIDERS,
} = vi.hoisted(() => {
	const order: string[] = [];
	// Stable refs returned by useModelConfig — these are the values
	// the facade destructures out + forwards to the other sub-hooks.
	// Tests assert that the SAME references are passed through.
	const refreshModelStatus = vi.fn().mockResolvedValue(undefined);
	const updateConfig = vi.fn().mockResolvedValue(undefined);
	const setConfig = vi.fn();
	const setModels = vi.fn();
	return {
		callOrder: order,
		configHookReturn: {
			// public fields
			config: { asr_backend: "whisper" },
			models: [],
			modelCatalog: {},
			apiKeys: { openai: "" },
			setApiKeys: vi.fn(),
			loadConfig: vi.fn().mockResolvedValue(undefined),
			// internal fields (facade destructures these out)
			refreshModelStatus,
			updateConfig,
			setConfig,
			setModels,
		},
		downloadHookReturn: {
			downloadingModel: null,
			downloadProgress: 0,
			downloadStatus: "",
			isPaused: false,
			downloadedBytes: null,
			totalBytes: null,
			speedBps: null,
			etaSeconds: null,
			failedDownload: null,
			installingDepsModel: null,
			downloadModel: vi.fn(),
			retryDownload: vi.fn(),
			installDeps: vi.fn(),
			handleTogglePause: vi.fn(),
			handleCancelDownload: vi.fn(),
		},
		selectionHookReturn: {
			selectingModel: null,
			deleteModelTarget: null,
			setDeleteModelTarget: vi.fn(),
			selectModel: vi.fn(),
			requestDeleteModel: vi.fn(),
			confirmDelete: vi.fn(),
		},
		cloudHookReturn: {
			cloudConsents: {},
			savingApiKey: null,
			testResults: {},
			saveApiKey: vi.fn(),
			setCloudConsent: vi.fn(),
			testConnection: vi.fn(),
			clearTestResult: vi.fn(),
		},
		folderHookReturn: {
			diskInfo: null,
			modelsFolderSupported: false,
			isImporting: false,
			handleImportModel: vi.fn(),
			handleOpenModelsFolder: vi.fn(),
		},
		mockCLOUD_PROVIDERS: [
			{ id: "openai", label: "OpenAI" },
			{ id: "groq", label: "Groq" },
		],
	};
});

// ── Mocks ─────────────────────────────────────────────────────────────
vi.mock("@/hooks/models/useModelConfig", () => ({
	useModelConfig: vi.fn((args: unknown) => {
		callOrder.push("useModelConfig");
		// Capture the args so tests can assert what was forwarded.
		(useModelConfigArgs as { value: unknown }).value = args;
		return configHookReturn;
	}),
}));
const useModelConfigArgs = { value: undefined as unknown };

vi.mock("@/hooks/models/useModelDownload", () => ({
	useModelDownload: vi.fn((args: unknown) => {
		callOrder.push("useModelDownload");
		(useModelDownloadArgs as { value: unknown }).value = args;
		return downloadHookReturn;
	}),
}));
const useModelDownloadArgs = { value: undefined as unknown };

vi.mock("@/hooks/models/useModelSelection", () => ({
	useModelSelection: vi.fn((args: unknown) => {
		callOrder.push("useModelSelection");
		(useModelSelectionArgs as { value: unknown }).value = args;
		return selectionHookReturn;
	}),
}));
const useModelSelectionArgs = { value: undefined as unknown };

vi.mock("@/hooks/models/useCloudProviders", () => ({
	useCloudProviders: vi.fn((args: unknown) => {
		callOrder.push("useCloudProviders");
		(useCloudProvidersArgs as { value: unknown }).value = args;
		return cloudHookReturn;
	}),
	// The real module also exports `safeApiKey` (re-used by useModelConfig);
	// not needed here because useModelConfig is fully mocked.
}));
const useCloudProvidersArgs = { value: undefined as unknown };

vi.mock("@/hooks/models/useModelFolder", () => ({
	useModelFolder: vi.fn((args: unknown) => {
		callOrder.push("useModelFolder");
		(useModelFolderArgs as { value: unknown }).value = args;
		return folderHookReturn;
	}),
}));
const useModelFolderArgs = { value: undefined as unknown };

vi.mock("@/lib/utils/models", () => ({
	CLOUD_PROVIDERS: mockCLOUD_PROVIDERS,
}));

// ── Import AFTER mocks ────────────────────────────────────────────────
import { useModelLifecycle } from "@/hooks/useModelLifecycle";

describe("useModelLifecycle — facade composition ", () => {
	beforeEach(() => {
		callOrder.length = 0;
		useModelConfigArgs.value = undefined;
		useModelDownloadArgs.value = undefined;
		useModelSelectionArgs.value = undefined;
		useCloudProvidersArgs.value = undefined;
		useModelFolderArgs.value = undefined;
	});

	describe("lifecycle ordering", () => {
		it("sub-hooks are invoked in the correct order (config → download → selection → cloud → folder)", () => {
			renderHook(() => useModelLifecycle());

			// Order matters because each subsequent sub-hook
			// receives helpers destructured from the prior one's
			// return (e.g. useModelDownload needs setModels +
			// refreshModelStatus from useModelConfig).
			expect(callOrder).toEqual([
				"useModelConfig",
				"useModelDownload",
				"useModelSelection",
				"useCloudProviders",
				"useModelFolder",
			]);
		});

		it("usePython is called first (provides `call` consumed by useModelConfig)", () => {
			// mockCall is the spy returned by usePython(); it's
			// forwarded into useModelConfig's args. If usePython
			// weren't called before useModelConfig, the forwarded
			// `call` would be undefined.
			renderHook(() => useModelLifecycle());
			const cfgArgs = useModelConfigArgs.value as {
				call?: unknown;
				markUpdated?: unknown;
			};
			expect(cfgArgs).toBeDefined();
			// The forwarded `call` is the SAME reference returned
			// by usePython (mockCall).
			expect(cfgArgs.call).toBe(mockCall);
		});

		it("useSnackbar is called (provides `showSnack` consumed by download/selection/folder)", () => {
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				showSnack?: unknown;
			};
			expect(dlArgs.showSnack).toBe(mockShowSnack);
		});

		it("useLastUpdated is called (provides `markUpdated`)", () => {
			const { result } = renderHook(() => useModelLifecycle());
			// markUpdated is forwarded to useModelConfig.
			const cfgArgs = useModelConfigArgs.value as {
				markUpdated?: unknown;
			};
			expect(cfgArgs.markUpdated).toBe(mockMarkUpdated);
			// agoLabel was REMOVED from the public return shape when the
			// "Last updated / refresh" indicator was removed from the
			// Models page — the facade must not re-add dead surface.
			expect(
				(result.current as Record<string, unknown>).agoLabel,
			).toBeUndefined();
		});
	});

	describe("cancel-mid-download cleanup wiring", () => {
		it("useModelDownload receives `setModels` AS THE SAME REFERENCE returned by useModelConfig", () => {
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				setModels?: unknown;
				refreshModelStatus?: unknown;
			};
			// Referential equality is the contract: cancel-mid-
			// download cleanup calls `setModels(prev => ...)`,
			// which mutates the SAME state owned by useModelConfig.
			// Without this, cancel-state-reset silently no-ops.
			expect(dlArgs.setModels).toBe(configHookReturn.setModels);
		});

		it("useModelDownload receives `refreshModelStatus` AS THE SAME REFERENCE returned by useModelConfig", () => {
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				refreshModelStatus?: unknown;
			};
			expect(dlArgs.refreshModelStatus).toBe(
				configHookReturn.refreshModelStatus,
			);
		});

		it("useModelDownload receives `call` from usePython (for the cancel_model_download IPC)", () => {
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				call?: unknown;
			};
			expect(dlArgs.call).toBe(mockCall);
		});

		it("useModelDownload receives `showSnack` from useSnackbar (for the cancelled/error toast)", () => {
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				showSnack?: unknown;
			};
			expect(dlArgs.showSnack).toBe(mockShowSnack);
		});
	});

	describe("post-install activation wiring", () => {
		it("useModelDownload receives `refreshModelStatus` so installDeps can reconcile", () => {
			// installDeps in useModelDownload awaits
			// `refreshModelStatus()` after a successful install
			// to mark the depsOk flag on the just-installed model.
			// The facade forwards refreshModelStatus from
			// useModelConfig — without this, installDeps would
			// silently skip the reconciliation step.
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				refreshModelStatus?: unknown;
			};
			expect(dlArgs.refreshModelStatus).toBeDefined();
			expect(dlArgs.refreshModelStatus).toBe(
				configHookReturn.refreshModelStatus,
			);
		});

		it("useModelDownload receives `setModels` so downloadModel can mark the just-downloaded model active", () => {
			// On success, downloadModel calls
			// `setModels(prev => prev.map(m => m.name === model.name
			//   ? { ...m, downloaded: true, isActive: !anyActive }
			//   : m))` — the post-install "activation" path.
			// Verifying setModels is forwarded pins this wiring.
			renderHook(() => useModelLifecycle());
			const dlArgs = useModelDownloadArgs.value as {
				setModels?: unknown;
			};
			expect(dlArgs.setModels).toBe(configHookReturn.setModels);
		});
	});

	describe("useModelSelection forwarding", () => {
		it("receives setModels + refreshModelStatus + updateConfig + setConfig + call + showSnack", () => {
			renderHook(() => useModelLifecycle());
			const selArgs = useModelSelectionArgs.value as {
				setModels?: unknown;
				refreshModelStatus?: unknown;
				updateConfig?: unknown;
				setConfig?: unknown;
				call?: unknown;
				showSnack?: unknown;
			};
			expect(selArgs.setModels).toBe(configHookReturn.setModels);
			expect(selArgs.refreshModelStatus).toBe(
				configHookReturn.refreshModelStatus,
			);
			expect(selArgs.updateConfig).toBe(configHookReturn.updateConfig);
			// setConfig forwarding: the selection mirrors the committed
			// model into config state immediately (no-model banner flips
			// on the user action, not on the config_changed echo).
			expect(selArgs.setConfig).toBe(configHookReturn.setConfig);
			expect(selArgs.call).toBe(mockCall);
			expect(selArgs.showSnack).toBe(mockShowSnack);
		});
	});

	describe("useCloudProviders forwarding", () => {
		it("receives setConfig + config + apiKeys + updateConfig + call + showSnack", () => {
			renderHook(() => useModelLifecycle());
			const cArgs = useCloudProvidersArgs.value as {
				setConfig?: unknown;
				config?: unknown;
				apiKeys?: unknown;
				updateConfig?: unknown;
				call?: unknown;
				showSnack?: unknown;
			};
			expect(cArgs.setConfig).toBe(configHookReturn.setConfig);
			expect(cArgs.config).toBe(configHookReturn.config);
			expect(cArgs.apiKeys).toBe(configHookReturn.apiKeys);
			expect(cArgs.updateConfig).toBe(configHookReturn.updateConfig);
			expect(cArgs.call).toBe(mockCall);
			expect(cArgs.showSnack).toBe(mockShowSnack);
		});
	});

	describe("useModelFolder forwarding", () => {
		it("receives loadConfig (from useModelConfig's public return) + call + showSnack", () => {
			renderHook(() => useModelLifecycle());
			const fArgs = useModelFolderArgs.value as {
				loadConfig?: unknown;
				call?: unknown;
				showSnack?: unknown;
			};
			// `loadConfig` is NOT destructured out by the facade —
			// it's part of `configRest` and forwarded to
			// useModelFolder so a successful import re-syncs the
			// local model list.
			expect(fArgs.loadConfig).toBe(configHookReturn.loadConfig);
			expect(fArgs.call).toBe(mockCall);
			expect(fArgs.showSnack).toBe(mockShowSnack);
		});
	});

	describe("return shape", () => {
		it("merges all sub-hook returns + cloudProviders", () => {
			const { result } = renderHook(() => useModelLifecycle());
			const r = result.current as Record<string, unknown>;

			// From configRest (everything from useModelConfig
			// EXCEPT the 4 internal helpers):
			expect(r.config).toBe(configHookReturn.config);
			expect(r.models).toBe(configHookReturn.models);
			expect(r.modelCatalog).toBe(configHookReturn.modelCatalog);
			expect(r.apiKeys).toBe(configHookReturn.apiKeys);
			expect(r.setApiKeys).toBe(configHookReturn.setApiKeys);
			expect(r.loadConfig).toBe(configHookReturn.loadConfig);
			// refreshing / handleManualRefresh are REMOVED dead surface
			// (the "Last updated / refresh" indicator is gone from the
			// Models page).
			expect(r.refreshing).toBeUndefined();
			expect(r.handleManualRefresh).toBeUndefined();

			// From download:
			expect(r.downloadingModel).toBe(downloadHookReturn.downloadingModel);
			expect(r.downloadModel).toBe(downloadHookReturn.downloadModel);
			expect(r.handleCancelDownload).toBe(
				downloadHookReturn.handleCancelDownload,
			);
			expect(r.installDeps).toBe(downloadHookReturn.installDeps);

			// From selection:
			expect(r.selectingModel).toBe(selectionHookReturn.selectingModel);
			expect(r.selectModel).toBe(selectionHookReturn.selectModel);

			// From cloud:
			expect(r.saveApiKey).toBe(cloudHookReturn.saveApiKey);
			expect(r.testConnection).toBe(cloudHookReturn.testConnection);

			// From folder:
			expect(r.diskInfo).toBe(folderHookReturn.diskInfo);
			expect(r.handleImportModel).toBe(folderHookReturn.handleImportModel);

			// agoLabel is REMOVED (the refresh indicator is gone) — the
			// facade must not resurrect dead public surface.
			expect(r.agoLabel).toBeUndefined();

			// Static CLOUD_PROVIDERS re-export:
			expect(r.cloudProviders).toBe(mockCLOUD_PROVIDERS);
		});

		it("does NOT leak the 4 internal helpers into the public return", () => {
			const { result } = renderHook(() => useModelLifecycle());
			const r = result.current as Record<string, unknown>;
			// The facade destructures these out so they don't leak
			// into the pre-split return shape — Models.tsx and its
			// tests don't expect them.
			expect(r.refreshModelStatus).toBeUndefined();
			expect(r.updateConfig).toBeUndefined();
			expect(r.setConfig).toBeUndefined();
			expect(r.setModels).toBeUndefined();
		});

		it("cloudProviders is the static CLOUD_PROVIDERS array (read-only)", () => {
			const { result } = renderHook(() => useModelLifecycle());
			expect(Array.isArray(result.current.cloudProviders)).toBe(true);
			expect(result.current.cloudProviders).toBe(mockCLOUD_PROVIDERS);
		});
	});

	describe("ApiTestResult type re-export (back-compat)", () => {
		it("the type alias is re-exported from the facade module", async () => {
			// The re-export is a TypeScript type-only export — at
			// runtime there's no value to assert on, but the
			// import must resolve (no missing-export error).
			// We verify by importing the module's named exports
			// and confirming the module namespace object exists.
			const mod = await import("@/hooks/useModelLifecycle");
			expect(mod).toBeDefined();
			expect(typeof mod.useModelLifecycle).toBe("function");
		});
	});
});
