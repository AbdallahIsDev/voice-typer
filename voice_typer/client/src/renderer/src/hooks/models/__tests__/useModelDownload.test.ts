/**
 * Unit tests for `useModelDownload`.
 *
 * Coverage :
 *   - download_progress event updates progress / status / byte counters / speed / ETA / isPaused
 *   - downloadModel success path: marks model downloaded, surfaces success snack, clears state
 *   - downloadModel failure path: success=false records failedDownload, fires sonner toast with Retry
 *   - downloadModel thrown-error path: records failedDownload with formatted error message
 *   - handleCancelDownload: invokes cancel_model_download IPC, clears state regardless of IPC outcome
 *   - retryDownload: clears failedDownload then re-invokes downloadModel
 *
 * Strategy: renderHook with a mocked `call` IPC fn + a captured `usePythonEvent`
 * subscriber. Sonner is mocked so we can assert on the Retry toast.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks (hoisted so vi.mock factories can reference them) ───────────
const { callMock, usePythonEventMock, toastMock } = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
	toastMock: {
		error: vi.fn(),
		success: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
}));

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: usePythonEventMock,
}));

vi.mock("sonner", () => ({
	toast: toastMock,
}));

vi.mock("@/i18n/i18n", () => ({
	// Minimal mock: returns the key with `{placeholder}` substitutions
	// applied. When params are provided but the key has no matching
	// placeholder (e.g. the key is "models.snack.downloadFailed" and the
	// translation file is not loaded), the params are appended as
	// `: key=value` pairs so the test can verify error message
	// propagation through `formatErrorMessage`.
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

import { useModelDownload } from "@/hooks/models/useModelDownload";
// ── Helpers ──────────────────────────────────────────────────────────
import type { ModelInfo } from "@/lib/utils/models";

function makeModel(overrides: Partial<ModelInfo> = {}): ModelInfo {
	return {
		name: "tiny",
		size: "~466MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: false,
		depsOk: true,
		isActive: false,
		...overrides,
	};
}

function makeHookArgs(
	overrides: {
		call?: typeof callMock;
		setModels?: React.Dispatch<React.SetStateAction<ModelInfo[]>>;
		refreshModelStatus?: () => Promise<void>;
	} = {},
) {
	const setModels =
		overrides.setModels ??
		(vi.fn((updater: (prev: ModelInfo[]) => ModelInfo[]) =>
			updater([]),
		) as unknown as React.Dispatch<React.SetStateAction<ModelInfo[]>>);
	const refreshModelStatus =
		overrides.refreshModelStatus ?? vi.fn().mockResolvedValue(undefined);
	const showSnack = vi.fn();
	return {
		call: (overrides.call ?? callMock) as unknown as <T = unknown>(
			cmd: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		showSnack,
		setModels,
		refreshModelStatus,
	};
}

/** Pull the `download_progress` handler captured by the usePythonEvent mock. */
function getDownloadProgressHandler():
	| ((data?: Record<string, unknown>) => (() => void) | undefined)
	| undefined {
	const call = usePythonEventMock.mock.calls.find(
		(c) => c[0] === "download_progress",
	);
	return call?.[1] as
		| ((data?: Record<string, unknown>) => (() => void) | undefined)
		| undefined;
}

beforeEach(() => {
	callMock.mockReset();
	usePythonEventMock.mockReset();
	toastMock.error.mockClear();
	toastMock.success.mockClear();
	toastMock.warning.mockClear();
	toastMock.info.mockClear();
	toastMock.dismiss.mockClear();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useModelDownload — download_progress event subscription", () => {
	it("updates progress + status when the download_progress event fires", () => {
		const { result } = renderHook(() => useModelDownload(makeHookArgs()));

		const handler = getDownloadProgressHandler();
		expect(handler).toBeDefined();

		act(() => {
			handler?.({
				progress: 42,
				status: "downloading",
				downloaded_bytes: 100,
				total_bytes: 240,
				speed_bytes_per_sec: 50,
				eta_seconds: 3,
			});
		});

		expect(result.current.downloadProgress).toBe(42);
		expect(result.current.downloadStatus).toBe("downloading");
		expect(result.current.downloadedBytes).toBe(100);
		expect(result.current.totalBytes).toBe(240);
		expect(result.current.speedBps).toBe(50);
		expect(result.current.etaSeconds).toBe(3);
	});

	it("reflects paused=true + resumed=true transitions on isPaused", () => {
		const { result } = renderHook(() => useModelDownload(makeHookArgs()));
		const handler = getDownloadProgressHandler();

		act(() => {
			handler?.({ paused: true });
		});
		expect(result.current.isPaused).toBe(true);

		// `resumed: true` flips isPaused back to false even without
		// an explicit `paused: false` field.
		act(() => {
			handler?.({ resumed: true });
		});
		expect(result.current.isPaused).toBe(false);
	});

	it("clears speedBps / etaSeconds when the corresponding fields are null", () => {
		const { result } = renderHook(() => useModelDownload(makeHookArgs()));
		const handler = getDownloadProgressHandler();

		// Seed with non-null values.
		act(() => {
			handler?.({ speed_bytes_per_sec: 100, eta_seconds: 5 });
		});
		expect(result.current.speedBps).toBe(100);
		expect(result.current.etaSeconds).toBe(5);

		// Null clears them — guards against stale speed/ETA clinging
		// to a finished download.
		act(() => {
			handler?.({ speed_bytes_per_sec: null, eta_seconds: null });
		});
		expect(result.current.speedBps).toBeNull();
		expect(result.current.etaSeconds).toBeNull();
	});
});

describe("useModelDownload — downloadModel success path", () => {
	it("marks the model as downloaded + active-if-none, surfaces success snack, clears downloading state", async () => {
		callMock.mockResolvedValue({ success: true, message: "ok" });
		const setModels = vi.fn();
		const args = makeHookArgs({ setModels: setModels as never });

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "tiny" });

		await act(async () => {
			await result.current.downloadModel(model);
		});

		// setModels invoked with updater that flags the just-downloaded
		// model as downloaded:true and (because no other model was
		// active) isActive:true.
		expect(setModels).toHaveBeenCalledTimes(1);
		const updater = setModels.mock.calls[0]?.[0] as (
			prev: ModelInfo[],
		) => ModelInfo[];
		const prev: ModelInfo[] = [
			makeModel({ name: "tiny" }),
			makeModel({ name: "large-v3-turbo" }),
		];
		const next = updater(prev);
		const small = next.find((m) => m.name === "tiny");
		expect(small?.downloaded).toBe(true);
		expect(small?.isActive).toBe(true);

		// Success snack surfaced.
		expect(args.showSnack).toHaveBeenCalledWith("ok", "success");

		// Bar unmounts + failure cleared on success.
		expect(result.current.downloadingModel).toBeNull();
		expect(result.current.failedDownload).toBeNull();
	});
});

describe("useModelDownload — downloadModel failure path (success:false)", () => {
	it("records failedDownload + fires sonner toast with Retry action, keeps downloadingModel set", async () => {
		callMock.mockResolvedValue({ success: false, error: "disk full" });
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "tiny" });

		await act(async () => {
			await result.current.downloadModel(model);
		});

		// downloadingModel stays set so the bar stays mounted.
		expect(result.current.downloadingModel).toBe("tiny");
		expect(result.current.failedDownload).toEqual({
			modelName: "tiny",
			error: "disk full",
		});

		// Sonner toast.error fired with a Retry action button.
		expect(toastMock.error).toHaveBeenCalledTimes(1);
		const [, opts] = toastMock.error.mock.calls[0] ?? [];
		expect(opts).toMatchObject({
			duration: 8000,
			action: expect.objectContaining({ label: expect.any(String) }),
		});
	});
});

describe("useModelDownload — downloadModel thrown-error path", () => {
	it("records failedDownload with formatted error message + fires sonner toast", async () => {
		callMock.mockRejectedValue(new Error("network down"));
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "tiny" });

		await act(async () => {
			await result.current.downloadModel(model);
		});

		expect(result.current.downloadingModel).toBe("tiny");
		expect(result.current.failedDownload).not.toBeNull();
		expect(result.current.failedDownload?.modelName).toBe("tiny");
		// The formatted message should include "network down" (formatErrorMessage
		// returns the Error.message on Error instances).
		expect(result.current.failedDownload?.error).toContain("network down");
		expect(toastMock.error).toHaveBeenCalledTimes(1);
	});
});

describe("useModelDownload — handleCancelDownload", () => {
	it("invokes cancel_model_download IPC + clears all local state on success", async () => {
		callMock.mockResolvedValue({ success: true });
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));

		// Seed some in-flight state.
		act(() => {
			const handler = getDownloadProgressHandler();
			handler?.({ progress: 50, status: "downloading" });
		});

		await act(async () => {
			await result.current.handleCancelDownload();
		});

		expect(callMock).toHaveBeenCalledWith("cancel_model_download");
		// Cancel snack fires regardless of branch.
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.cancelled",
			"warning",
		);
		// All local state cleared.
		expect(result.current.downloadingModel).toBeNull();
		expect(result.current.failedDownload).toBeNull();
		expect(result.current.downloadProgress).toBe(0);
		expect(result.current.downloadStatus).toBe("");
	});

	it("still clears local state when the cancel IPC throws (user-intent wins)", async () => {
		callMock.mockRejectedValue(new Error("cancel IPC failed"));
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));

		await act(async () => {
			await result.current.handleCancelDownload();
		});

		// Error snack surfaced.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("cancel IPC failed"),
			"error",
		);
		// State still cleared in the finally block.
		expect(result.current.downloadingModel).toBeNull();
		expect(result.current.failedDownload).toBeNull();
	});
});

describe("useModelDownload — retryDownload", () => {
	it("clears failedDownload then re-invokes downloadModel", async () => {
		// First call (initial downloadModel) fails; second call (retry)
		// succeeds so we can observe the failure → success transition.
		callMock
			.mockResolvedValueOnce({ success: false, error: "transient" })
			.mockResolvedValueOnce({ success: true });

		const args = makeHookArgs();
		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "tiny" });

		await act(async () => {
			await result.current.downloadModel(model);
		});
		expect(result.current.failedDownload).not.toBeNull();

		await act(async () => {
			await result.current.retryDownload(model);
		});

		// Retry clears the failure before invoking downloadModel, and
		// the second downloadModel call succeeds so the bar unmounts.
		expect(result.current.failedDownload).toBeNull();
		expect(result.current.downloadingModel).toBeNull();
		// Two download_model IPC calls total (initial + retry).
		const downloadCalls = callMock.mock.calls.filter(
			([cmd]) => cmd === "download_model",
		);
		expect(downloadCalls.length).toBe(2);
	});
});

describe("useModelDownload — installDeps (regression sanity)", () => {
	it("calls install_parakeet_deps IPC + surfaces success snack when backend reports success", async () => {
		callMock.mockResolvedValue({ success: true });
		const refreshModelStatus = vi.fn().mockResolvedValue(undefined);
		const args = makeHookArgs({ refreshModelStatus });

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "parakeet", backend: "parakeet" });

		await act(async () => {
			await result.current.installDeps(model);
		});

		expect(callMock).toHaveBeenCalledWith("install_parakeet_deps", {
			model: "parakeet",
		});
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.depsInstalled",
			"success",
		);
		expect(refreshModelStatus).toHaveBeenCalledTimes(1);
		expect(result.current.installingDepsModel).toBeNull();
	});

	it("falls back to the manual-install hint when the IPC is unavailable", async () => {
		callMock.mockRejectedValue(new Error("command not registered"));
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "parakeet", backend: "parakeet" });

		await act(async () => {
			await result.current.installDeps(model);
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.depsRequiredName"),
			"warning",
		);
		expect(result.current.installingDepsModel).toBeNull();
	});

	it("falls back to the manual-install hint for Qwen too (generic {name} message, not Parakeet-specific)", async () => {
		callMock.mockRejectedValue(new Error("command not registered"));
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "qwen", backend: "qwen" });

		await act(async () => {
			await result.current.installDeps(model);
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.depsRequiredName"),
			"warning",
		);
		expect(args.showSnack.mock.calls[0]?.[0]).toContain("qwen");
		expect(result.current.installingDepsModel).toBeNull();
	});
});
