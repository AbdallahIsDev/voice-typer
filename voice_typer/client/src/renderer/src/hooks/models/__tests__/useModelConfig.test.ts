import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────
const { callMock, usePythonEventMock } = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: usePythonEventMock,
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string, params?: Record<string, string>) => {
		if (!params) return key;
		let result = key;
		const leftover: string[] = [];
		for (const [k, v] of Object.entries(params)) {
			const placeholder = `{${k}}`;
			if (result.includes(placeholder)) {
				result = result.replace(placeholder, String(v));
			} else {
				leftover.push(`${k}=${String(v)}`);
			}
		}
		if (leftover.length > 0) {
			result = `${result}: ${leftover.join(", ")}`;
		}
		return result;
	},
}));

// ── Helpers ──────────────────────────────────────────────────────────
import { isModelStatusFresh } from "@/hooks/models/modelStatusCache";
import { useModelConfig } from "@/hooks/models/useModelConfig";
import { __resetIpcCacheForTests } from "@/lib/ipcCache";
import { __resetPrefetchFlightsForTests } from "@/lib/snapshotCache";
import type { LausuConfig } from "@/types/config";

function makeConfig(overrides: Partial<LausuConfig> = {}): LausuConfig {
	return {
		schema_version: 1,
		hotkey: "<f2>",
		sample_rate: 16000,
		microphone: null,
		model_size: "tiny",
		language: "en",
		device: "cpu",
		beam_size: 5,
		best_of: 5,
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
		fast_startup: false,
		clipboard_save_restore: true,
		clipboard_restore_delay_ms: 0,
		asr_backend: "whisper",
		qwen_model_path: null,
		parakeet_model_path: null,
		openai_api_key: "",
		groq_api_key: "",
		deepgram_api_key: "",
		gemini_api_key: "",
		huggingface_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
		cloud_gemini_consent: false,
		...overrides,
	} as LausuConfig;
}

function makeHookArgs() {
	const markUpdated = vi.fn();
	return { call: callMock as never, markUpdated };
}

function getConfigChangedHandler():
	| ((data?: Record<string, unknown>) => (() => void) | undefined)
	| undefined {
	const c = usePythonEventMock.mock.calls.find(
		(c) => c[0] === "config_changed",
	);
	return c?.[1] as
		| ((data?: Record<string, unknown>) => (() => void) | undefined)
		| undefined;
}

beforeEach(() => {
	callMock.mockReset();
	usePythonEventMock.mockReset();
	__resetIpcCacheForTests();
	__resetPrefetchFlightsForTests();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useModelConfig, loadConfig (parallelized fetch)", () => {
	it("fires get_config + get_model_status + get_model_catalog in parallel via Promise.allSettled", async () => {
		const cfg = makeConfig({ model_size: "tiny" });
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(cfg);
			if (cmd === "get_model_status")
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
				});
			if (cmd === "get_model_catalog")
				return Promise.resolve({
					models: [
						{
							name: "tiny",
							display_name: "Small (EN)",
							download_size_mb: 466,
							required_vram_mb: 0,
							backend: "whisper",
							multilingual: false,
							supported_languages: null,
							description: "",
							repo_id: "openai/whisper-small.en",
							is_distilled: false,
							speed_rating: "fast",
							accuracy_rating: "medium",
						},
					],
				});
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		// All three IPC commands were issued.
		const commandsIssued = callMock.mock.calls.map((c) => c[0]);
		expect(commandsIssued).toContain("get_config");
		expect(commandsIssued).toContain("get_model_status");
		expect(commandsIssued).toContain("get_model_catalog");

		// Config + models + catalog state populated.
		expect(result.current.config?.model_size).toBe("tiny");
		expect(result.current.models.length).toBeGreaterThan(0);
		expect(result.current.modelCatalog.tiny).toBeDefined();
		expect(result.current.modelCatalog.tiny?.download_size_mb).toBe(466);

		// markUpdated invoked at the end of loadConfig (finally block).
		expect(args.markUpdated).toHaveBeenCalled();
	});

	it("does NOT crash when get_config rejects, and surfaces loadError for the page's Retry state", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return Promise.reject(new Error("backend down"));
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		// The hook should not throw, the allSettled wrapper catches the
		// rejection and logs it. markUpdated still fires in the finally block.
		await waitFor(() => {
			expect(args.markUpdated).toHaveBeenCalled();
		});

		// Config stays null (get_config rejected) and the failure is
		// surfaced via `loadError` so the Models page can render the
		// load-failure EmptyState with a Retry action instead of an
		// endless spinner.
		expect(result.current.config).toBeNull();
		await waitFor(() => {
			expect(result.current.loadError).toBe("backend down");
		});
	});

	it("clears loadError on a subsequent successful load", async () => {
		let failGetConfig = true;
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				return failGetConfig
					? Promise.reject(new Error("backend down"))
					: Promise.resolve(makeConfig({ model_size: "tiny" }));
			}
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.loadError).toBe("backend down");
		});
		expect(result.current.config).toBeNull();

		failGetConfig = false;
		await act(async () => {
			await result.current.loadConfig();
		});

		expect(result.current.loadError).toBeNull();
		expect(result.current.config?.model_size).toBe("tiny");
	});

	it("strips the <redacted> sentinel when seeding apiKeys from get_config", async () => {
		const cfg = makeConfig({
			openai_api_key: "<redacted>",
			groq_api_key: "real-groq-key",
			deepgram_api_key: undefined as unknown as string,
			gemini_api_key: "",
		});
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(cfg);
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.apiKeys.openai).toBe("");
		});
		expect(result.current.apiKeys.openai).toBe("");
		expect(result.current.apiKeys.groq).toBe("real-groq-key");
		// deepgram undefined → safeApiKey returns "".
		expect(result.current.apiKeys.deepgram).toBe("");
	});
});

describe("useModelConfig, config drift detection (config_changed event)", () => {
	it("merges a partial config_changed payload into the cached config + reapplies active state", async () => {
		const initial = makeConfig({ model_size: "tiny", openai_api_key: "" });
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(initial);
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		// Wait for the initial load to complete so cachedConfigRef is populated.
		await waitFor(() => {
			expect(result.current.config?.model_size).toBe("tiny");
		});

		// Dispatch a partial config_changed payload that drifts model_size
		// + sets an api key. The handler should merge into the cached config
		// ref + applyActiveState should re-map isActive on the local model list.
		const handler = getConfigChangedHandler();
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.({ model_size: "large-v3-turbo", openai_api_key: "sk-new" });
		});

		// Config reflects the merged partial (no re-fetch).
		expect(result.current.config?.model_size).toBe("large-v3-turbo");
		expect(result.current.config?.openai_api_key).toBe("sk-new");
		// Untouched fields preserved (drift detection, not a clobber).
		expect(result.current.config?.language).toBe("en");
	});

	it("does NOT apply the partial merge when no cached config exists yet (early return)", async () => {
		// Make get_config slow so the cachedConfigRef is still null at the
		// time the config_changed event fires.
		let resolveGetConfig: (v: LausuConfig) => void = () => {};
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return new Promise((resolve) => {
					resolveGetConfig = resolve as typeof resolveGetConfig;
				});
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		// Fire config_changed BEFORE get_config resolves, cachedConfigRef is null.
		const handler = getConfigChangedHandler();
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.({ model_size: "large-v3-turbo" });
		});

		// Config is still null (the merge bailed because cachedConfigRef was null).
		expect(result.current.config).toBeNull();

		// Now resolve get_config, the initial config lands.
		await act(async () => {
			resolveGetConfig(makeConfig({ model_size: "tiny" }));
		});
		await waitFor(() => {
			expect(result.current.config?.model_size).toBe("tiny");
		});
	});
});
describe("useModelConfig, refreshModelStatus helper", () => {
	it("invokes get_model_status + reconciles downloaded/depsOk on the local model list", async () => {
		const initial = makeConfig({ model_size: "tiny" });
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(initial);
			if (cmd === "get_model_status")
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
					"large-v3-turbo": { downloaded: false, deps_ok: true },
				});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		// Call refreshModelStatus again with an updated status payload.
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_model_status")
				return Promise.resolve({
					tiny: { downloaded: true, deps_ok: true },
				});
			return Promise.resolve({});
		});

		await act(async () => {
			await result.current.refreshModelStatus();
		});

		// The active model matches the backend status (no forced override).
		const small = result.current.models.find((m) => m.name === "tiny");
		expect(small?.downloaded).toBe(true);
		expect(small?.depsOk).toBe(true);
	});

	it("does NOT force the active model to downloaded when the backend reports it missing ( STALE-ACTIVE regression)", async () => {
		// Config says small.en is the active model, but the backend
		// (which stats the actual filesystem) reports it as NOT
		// preserve that truth so the card can offer a restore/clear
		// affordance instead of a dead-end disabled "Active" tick.
		const initial = makeConfig({ model_size: "tiny" });
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(initial);
			if (cmd === "get_model_status")
				return Promise.resolve({
					tiny: { downloaded: false, deps_ok: true },
					"large-v3-turbo": { downloaded: true, deps_ok: true },
				});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		// The ACTIVE model reported as missing must stay missing.
		const small = result.current.models.find((m) => m.name === "tiny");
		expect(small?.isActive).toBe(true);
		expect(small?.downloaded).toBe(false);

		// A non-active downloaded model is untouched.
		const tiny = result.current.models.find((m) => m.name === "large-v3-turbo");
		expect(tiny?.downloaded).toBe(true);

		// re-applied the forced override there too).
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_model_status")
				return Promise.resolve({
					tiny: { downloaded: false, deps_ok: true },
					"large-v3-turbo": { downloaded: true, deps_ok: true },
				});
			return Promise.resolve({});
		});

		await act(async () => {
			await result.current.refreshModelStatus();
		});

		const after = result.current.models.find((m) => m.name === "tiny");
		expect(after?.downloaded).toBe(false);
	});
});

describe("useModelConfig, removed refresh surface", () => {
	it("no longer exposes refreshing / handleManualRefresh (the refresh indicator is gone)", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return Promise.resolve(makeConfig({ model_size: "tiny" }));
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		// Initial mount load.
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		const removed = result.current as unknown as Record<string, unknown>;
		expect(removed.refreshing).toBeUndefined();
		expect(removed.handleManualRefresh).toBeUndefined();
		// markUpdated is still driven by loadConfig's finally block.
		args.markUpdated.mockClear();
		await act(async () => {
			await result.current.loadConfig();
		});
		expect(args.markUpdated).toHaveBeenCalled();
	});
});

describe("useModelConfig, updateConfig re-throws on error", () => {
	it("re-throws the underlying error so callers can branch success vs. failure", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			if (cmd === "set_config")
				return Promise.reject(new Error("backend rejected update"));
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		await expect(
			result.current.updateConfig({ model_size: "large-v3-turbo" }),
		).rejects.toThrow("backend rejected update");

		// set_config was actually called with the updates payload.
		const setConfigCalls = callMock.mock.calls.filter(
			([cmd]) => cmd === "set_config",
		);
		expect(setConfigCalls.length).toBe(1);
		expect(setConfigCalls[0]?.[1]).toEqual({ model_size: "large-v3-turbo" });
	});
});

describe("useModelConfig, shared status snapshot (C-CACHE-5)", () => {
	function stubAll(status: unknown) {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return Promise.resolve(makeConfig({ model_size: "tiny" }));
			if (cmd === "get_model_status") return Promise.resolve(status);
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});
	}

	function statusCalls() {
		return callMock.mock.calls.filter(([cmd]) => cmd === "get_model_status");
	}

	it("mount with a fresh snapshot skips the disk stat but still fetches config + catalog", async () => {
		const { writeIpcCache } = await import("@/lib/ipcCache");
		writeIpcCache("models.config", makeConfig({ model_size: "tiny" }));
		writeIpcCache("models.statusSnapshot", {
			status: {
				tiny: { downloaded: true, deps_ok: true },
				_storage: { used_bytes: 42, hub_path: "h", config_dir: "c" },
			},
			fetchedAt: Date.now(),
		});
		stubAll({ tiny: { downloaded: true, deps_ok: true } });

		const { result } = renderHook(() => useModelConfig(makeHookArgs()));
		// First paint already carries the install state (seeded, no stat).
		expect(
			result.current.models.find((m) => m.name === "tiny")?.downloaded,
		).toBe(true);
		expect(result.current.storage?.used_bytes).toBe(42);

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});
		const commands = callMock.mock.calls.map((c) => c[0]);
		expect(commands).toContain("get_config");
		expect(commands).toContain("get_model_catalog");
		expect(statusCalls()).toHaveLength(0);
	});

	it("stale snapshot re-stats on mount and rewrites the snapshot", async () => {
		const { writeIpcCache, peekIpcCache } = await import("@/lib/ipcCache");
		writeIpcCache("models.statusSnapshot", {
			status: { tiny: { downloaded: false, deps_ok: true } },
			fetchedAt: Date.now() - 31_000,
		});
		stubAll({ tiny: { downloaded: true, deps_ok: true } });

		const { result } = renderHook(() => useModelConfig(makeHookArgs()));
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});
		expect(statusCalls().length).toBeGreaterThan(0);
		const snap = peekIpcCache<{
			status: Record<string, { downloaded: boolean }>;
			fetchedAt: number;
		}>("models.statusSnapshot");
		expect(snap?.status.tiny?.downloaded).toBe(true);
		expect(Date.now() - (snap?.fetchedAt ?? 0)).toBeLessThan(30_000);
	});

	it("refreshModelStatus always re-stats even when the snapshot is fresh", async () => {
		stubAll({ tiny: { downloaded: true, deps_ok: true } });
		const { result } = renderHook(() => useModelConfig(makeHookArgs()));
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});
		// Mount wrote a fresh snapshot; an explicit refresh must still stat.
		expect(isModelStatusFresh()).toBe(true);
		callMock.mockClear();
		stubAll({ tiny: { downloaded: false, deps_ok: true } });
		await act(async () => {
			await result.current.refreshModelStatus();
		});
		expect(statusCalls()).toHaveLength(1);
		expect(
			result.current.models.find((m) => m.name === "tiny")?.downloaded,
		).toBe(false);
	});

	it("an invalid status payload is never written to the snapshot", async () => {
		stubAll({ type: "error", data: { code: "x", message: "y" } });
		const { result } = renderHook(() => useModelConfig(makeHookArgs()));
		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});
		const { peekIpcCache } = await import("@/lib/ipcCache");
		expect(peekIpcCache("models.statusSnapshot")).toBeUndefined();
	});

	it("re-races once after a dataless mount failure, then shows data (cold-start storm)", async () => {
		vi.useFakeTimers();
		try {
			let attempts = 0;
			callMock.mockImplementation((cmd: string) => {
				if (cmd === "get_config") {
					attempts += 1;
					if (attempts === 1) return Promise.reject(new Error("boot storm"));
					return Promise.resolve(makeConfig({ model_size: "tiny" }));
				}
				if (cmd === "get_model_status") return Promise.resolve({});
				if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
				return Promise.resolve({});
			});
			const { result } = renderHook(() => useModelConfig(makeHookArgs()));
			await act(async () => {
				await vi.advanceTimersByTimeAsync(0);
			});

			// First attempt failed: error screen owns the page for now.
			expect(result.current.config).toBeNull();
			expect(result.current.loadError).not.toBeNull();

			// The single delayed re-race heals it without manual Retry.
			await act(async () => {
				await vi.advanceTimersByTimeAsync(8000);
			});
			expect(
				callMock.mock.calls.filter(([cmd]) => cmd === "get_config"),
			).toHaveLength(2);
			expect(result.current.config?.model_size).toBe("tiny");
			expect(result.current.loadError).toBeNull();

			// Bounded: far-future timers add no further attempts.
			await act(async () => {
				await vi.advanceTimersByTimeAsync(120_000);
			});
			expect(
				callMock.mock.calls.filter(([cmd]) => cmd === "get_config"),
			).toHaveLength(2);
		} finally {
			vi.useRealTimers();
		}
	});
});
