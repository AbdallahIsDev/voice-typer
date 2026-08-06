/**
 * Unit tests for `useCloudProviders`.
 *
 * Coverage :
 *   - cloud provider config validation: saveApiKey bails on empty key, on
 *     unchanged key (no IPC round-trip)
 *   - secret redaction: saveApiKey reads the persisted value through
 *     `safeApiKey`, which strips the `<redacted>` sentinel so a redacted
 *     persisted value does NOT match a freshly-typed empty-string input
 *   - testConnection: sets pending → success on backend OK
 *   - testConnection: sets pending → failure on backend !OK
 *   - testConnection: sets info status when no API key is present
 *   - clearTestResult: removes only the targeted provider's entry
 *   - setCloudConsent: persists the consent flag + optimistically updates
 *     the local config snapshot
 *   - module-level `safeApiKey` helper: strips `<redacted>` sentinel,
 *     preserves real keys, normalizes null/undefined to ""
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────
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
import {
	safeApiKey,
	useCloudProviders,
} from "@/hooks/models/useCloudProviders";
import type { VoiceTyperConfig } from "@/types/config";

const callMock = vi.fn();
const showSnackMock = vi.fn();
const updateConfigMock = vi.fn().mockResolvedValue(undefined);
const setConfigMock = vi.fn();

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		hotkey: "<f2>",
		sample_rate: 16000,
		microphone: null,
		model_size: "small.en",
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

function makeHookArgs(
	apiKeys: Record<string, string>,
	config: VoiceTyperConfig | null,
) {
	return {
		showSnack: showSnackMock,
		setConfig: setConfigMock as never,
		config,
		apiKeys,
		updateConfig: updateConfigMock,
		call: callMock as unknown as <T = unknown>(
			cmd: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
	};
}

beforeEach(() => {
	callMock.mockReset();
	showSnackMock.mockReset();
	updateConfigMock.mockReset().mockResolvedValue(undefined);
	setConfigMock.mockReset();
});

afterEach(() => {
	vi.clearAllMocks();
});

// ── safeApiKey helper (module-level, re-exported) ─────────────────────
describe("safeApiKey — secret redaction sentinel", () => {
	it("returns the value unchanged when it is a real key", () => {
		expect(safeApiKey("sk-real-key")).toBe("sk-real-key");
	});

	it("strips the <redacted> sentinel — the renderer never displays the marker", () => {
		// The backend substitutes `<redacted>` for saved API keys in
		// get_config responses. safeApiKey converts that sentinel to ""
		// so the renderer shows an empty input field instead of the
		// literal string "<redacted>".
		expect(safeApiKey("<redacted>")).toBe("");
	});

	it("normalizes null / undefined to empty string", () => {
		expect(safeApiKey(null)).toBe("");
		expect(safeApiKey(undefined)).toBe("");
	});

	it("preserves empty string (no false positive on the sentinel check)", () => {
		expect(safeApiKey("")).toBe("");
	});
});

// ── saveApiKey validation guards ──────────────────────────────────────
describe("useCloudProviders — saveApiKey validation guards", () => {
	it("bails out with an info snack when the key is empty (prevents clobbering stored key with '')", async () => {
		const args = makeHookArgs({ openai: "" }, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.saveApiKey("openai");
		});

		expect(showSnackMock).toHaveBeenCalledWith(
			"models.snack.apiKeyEmpty",
			"info",
		);
		// updateConfig NOT called — no IPC round-trip.
		expect(updateConfigMock).not.toHaveBeenCalled();
	});

	it("bails out with an info snack when the key matches the persisted value (no-op)", async () => {
		const cfg = makeConfig({ openai_api_key: "sk-same-key" });
		const args = makeHookArgs({ openai: "sk-same-key" }, cfg);
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.saveApiKey("openai");
		});

		expect(showSnackMock).toHaveBeenCalledWith(
			"models.snack.apiKeyUnchanged",
			"info",
		);
		expect(updateConfigMock).not.toHaveBeenCalled();
	});

	it("persists the key via updateConfig when the key is non-empty + differs from persisted", async () => {
		const cfg = makeConfig({ openai_api_key: "sk-old-key" });
		const args = makeHookArgs({ openai: "sk-new-key" }, cfg);
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.saveApiKey("openai");
		});

		expect(updateConfigMock).toHaveBeenCalledWith({
			openai_api_key: "sk-new-key",
		});
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.apiKeySaved"),
			"success",
		);
	});

	it("treats a redacted persisted value as empty (safeApiKey strips the sentinel) — so a re-typed key always saves", async () => {
		// This is the regression guard for the secret-redaction flow:
		// after navigating away and back, the persisted key shows up as
		// `<redacted>` in the config. safeApiKey converts that to "",
		// so the "unchanged" guard does NOT match a freshly-typed key
		// against the literal string `<redacted>` — the save proceeds.
		const cfg = makeConfig({ openai_api_key: "<redacted>" });
		const args = makeHookArgs({ openai: "sk-real-key" }, cfg);
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.saveApiKey("openai");
		});

		expect(updateConfigMock).toHaveBeenCalledWith({
			openai_api_key: "sk-real-key",
		});
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.apiKeySaved"),
			"success",
		);
	});
});

// ── testConnection lifecycle ──────────────────────────────────────────
describe("useCloudProviders — testConnection lifecycle", () => {
	it("sets info status when no API key is present", async () => {
		const args = makeHookArgs({}, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.testConnection("openai");
		});

		expect(result.current.testResults.openai).toEqual({
			message: "models.test.needApiKey",
			status: "info",
		});
		// Backend IPC never invoked.
		expect(callMock).not.toHaveBeenCalled();
	});

	it("sets pending → success on backend ok=true", async () => {
		callMock.mockResolvedValue({ ok: true, status: 200, message: "OK" });
		const args = makeHookArgs({ openai: "sk-real-key" }, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.testConnection("openai");
		});

		expect(result.current.testResults.openai).toEqual({
			message: "models.test.connectionSuccessful",
			status: "success",
		});
		expect(callMock).toHaveBeenCalledWith("test_cloud_connection", {
			provider: "openai",
		});
	});

	it("sets pending → failure on backend ok=false", async () => {
		callMock.mockResolvedValue({
			ok: false,
			status: 401,
			message: "Invalid API key",
		});
		const args = makeHookArgs({ openai: "sk-bad-key" }, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.testConnection("openai");
		});

		expect(result.current.testResults.openai).toMatchObject({
			status: "failure",
		});
		expect(result.current.testResults.openai?.message).toContain(
			"models.test.connectionFailed",
		);
	});

	it("sets pending → failure on IPC throw", async () => {
		callMock.mockRejectedValue(new Error("network unreachable"));
		const args = makeHookArgs({ openai: "sk-real-key" }, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.testConnection("openai");
		});

		expect(result.current.testResults.openai).toMatchObject({
			status: "failure",
		});
		expect(result.current.testResults.openai?.message).toContain(
			"network unreachable",
		);
	});
});

// ── clearTestResult ───────────────────────────────────────────────────
describe("useCloudProviders — clearTestResult", () => {
	it("removes only the targeted provider's entry (preserves other providers)", async () => {
		callMock.mockResolvedValue({ ok: true, status: 200, message: "OK" });
		const args = makeHookArgs(
			{ openai: "sk-real-key", groq: "gsk-real-key" },
			makeConfig(),
		);
		const { result } = renderHook(() => useCloudProviders(args));

		// Populate both providers' test results.
		await act(async () => {
			await result.current.testConnection("openai");
			await result.current.testConnection("groq");
		});
		expect(Object.keys(result.current.testResults).sort()).toEqual([
			"groq",
			"openai",
		]);

		// Clear only openai.
		act(() => {
			result.current.clearTestResult("openai");
		});

		expect(result.current.testResults.openai).toBeUndefined();
		// groq result preserved.
		expect(result.current.testResults.groq).toBeDefined();
	});

	it("is a no-op when the provider has no test result", () => {
		const args = makeHookArgs({}, makeConfig());
		const { result } = renderHook(() => useCloudProviders(args));

		const before = result.current.testResults;
		act(() => {
			result.current.clearTestResult("deepgram");
		});
		// Reference-equal (no new object created) — the early return
		// guards against a spurious setState.
		expect(result.current.testResults).toBe(before);
	});
});

// ── setCloudConsent ───────────────────────────────────────────────────
describe("useCloudProviders — setCloudConsent", () => {
	it("persists the consent flag + optimistically updates the local config snapshot (grant)", async () => {
		const args = makeHookArgs({}, makeConfig({ cloud_openai_consent: false }));
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.setCloudConsent("openai", true);
		});

		expect(updateConfigMock).toHaveBeenCalledWith({
			cloud_openai_consent: true,
		});
		// Optimistic local update via setConfig.
		expect(setConfigMock).toHaveBeenCalledTimes(1);
		// Success snack for grant.
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.consentGranted"),
			"success",
		);
	});

	it("surfaces a warning snack on revoke (not success)", async () => {
		const args = makeHookArgs({}, makeConfig({ cloud_groq_consent: true }));
		const { result } = renderHook(() => useCloudProviders(args));

		await act(async () => {
			await result.current.setCloudConsent("groq", false);
		});

		expect(updateConfigMock).toHaveBeenCalledWith({
			cloud_groq_consent: false,
		});
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.consentRevoked"),
			"warning",
		);
	});

	it("handleGrantConsent grants HuggingFace consent (thin wrapper)", async () => {
		const args = makeHookArgs({}, makeConfig({ huggingface_consent: false }));
		const { result } = renderHook(() => useCloudProviders(args));

		act(() => {
			result.current.handleGrantConsent();
		});
		// Wait for the underlying async setHuggingFaceConsent to resolve.
		await act(async () => {
			await Promise.resolve();
		});

		expect(updateConfigMock).toHaveBeenCalledWith({
			huggingface_consent: true,
		});
	});
});
