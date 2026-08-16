/**
 * Unit tests for `useModelConfig`.
 *
 * Coverage :
 *   - loadConfig parallelized IPC: get_config + get_model_status + get_model_catalog
 *     fired via Promise.allSettled, with config applied first
 *   - config drift detection: the `config_changed` event merges partial payload
 *     into the cached config ref + reapplies active-state
 *   - refreshModelStatus helper: get_model_status IPC + downloaded/depsOk
 *     reconciliation ( STALE-ACTIVE: the backend status is authoritative —
 *     an active model reported as NOT downloaded stays not-downloaded)
 *   - handleManualRefresh: flips `refreshing` flag around loadConfig
 *   - updateConfig: re-throws on set_config failure (callers can branch)
 *
 * Strategy: renderHook + a captured usePythonEvent subscriber + a mock `call`.
 */
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
import { useModelConfig } from "@/hooks/models/useModelConfig";
import type { VoiceTyperConfig } from "@/types/config";

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
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
		huggingface_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
		...overrides,
	} as VoiceTyperConfig;
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
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useModelConfig — loadConfig (parallelized fetch)", () => {
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

	it("does NOT crash when get_config rejects (Promise.allSettled isolates failures)", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return Promise.reject(new Error("backend down"));
			if (cmd === "get_model_status") return Promise.resolve({});
			if (cmd === "get_model_catalog") return Promise.resolve({ models: [] });
			return Promise.resolve({});
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelConfig(args));

		// The hook should not throw — the allSettled wrapper catches the
		// rejection and logs it. markUpdated still fires in the finally block.
		await waitFor(() => {
			expect(args.markUpdated).toHaveBeenCalled();
		});

		// Config stays null (get_config rejected).
		expect(result.current.config).toBeNull();
	});

	it("strips the <redacted> sentinel when seeding apiKeys from get_config", async () => {
		const cfg = makeConfig({
			openai_api_key: "<redacted>",
			groq_api_key: "real-groq-key",
			deepgram_api_key: undefined as unknown as string,
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

describe("useModelConfig — config drift detection (config_changed event)", () => {
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
		// Untouched fields preserved (drift detection — not a clobber).
		expect(result.current.config?.language).toBe("en");
	});

	it("does NOT apply the partial merge when no cached config exists yet (early return)", async () => {
		// Make get_config slow so the cachedConfigRef is still null at the
		// time the config_changed event fires.
		let resolveGetConfig: (v: VoiceTyperConfig) => void = () => {};
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

		// Fire config_changed BEFORE get_config resolves — cachedConfigRef is null.
		const handler = getConfigChangedHandler();
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.({ model_size: "large-v3-turbo" });
		});

		// Config is still null (the merge bailed because cachedConfigRef was null).
		expect(result.current.config).toBeNull();

		// Now resolve get_config — the initial config lands.
		await act(async () => {
			resolveGetConfig(makeConfig({ model_size: "tiny" }));
		});
		await waitFor(() => {
			expect(result.current.config?.model_size).toBe("tiny");
		});
	});
});
describe("useModelConfig — refreshModelStatus helper", () => {
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
		// downloaded — the model was removed out-of-band. The hook must
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

		// refreshModelStatus must also preserve the truth (the old code
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

describe("useModelConfig — handleManualRefresh", () => {
	it("flips refreshing=true around loadConfig + calls markUpdated", async () => {
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

		args.markUpdated.mockClear();

		await act(async () => {
			await result.current.handleManualRefresh();
		});

		// refreshing should be back to false after the refresh completes.
		expect(result.current.refreshing).toBe(false);
		// markUpdated called by the inner loadConfig.
		expect(args.markUpdated).toHaveBeenCalled();
	});
});

describe("useModelConfig — updateConfig re-throws on error", () => {
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
