/**
 * Unit tests for `useModelDownload`.
 *
 * Coverage :
 *   - download_progress event updates progress / status / byte counters / speed / ETA / isPaused
 *   - downloadModel success path: marks model downloaded, surfaces success snack, clears state
 *   - downloadModel failure path: success=false records failedDownload, fires sonner toast with Retry
 *   - downloadModel thrown-error path: records failedDownload with formatted error message
 *   - downloadModel QUEUED path: the backend's queued outcome surfaces an info
 *     snack, does NOT mark the model downloaded, and leaves the ACTIVE
 *     download's bar state intact (a concurrent request never steals the
 *     single progress-bar slot).
 *   - downloadModel already-active path: a re-click of the ACTIVE model warns
 *     and keeps the live bar mounted.
 *   - handleCancelDownload: invokes cancel_model_download IPC, clears state regardless of IPC outcome
 *   - handleCancelDownload(modelName): the queued-model cancel forwards the
 *     model name in the IPC payload; a queue removal leaves the active
 *     transfer's state intact (only the active-cancel paths clear state).
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
		reconcileAfterDownload?: () => Promise<void>;
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
		reconcileAfterDownload:
			overrides.reconcileAfterDownload ?? vi.fn().mockResolvedValue(undefined),
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

		// A bare partial event (no status/paused/resumed marker) means
		// "not re-measured" — previous values are PRESERVED, not cleared.
		act(() => {
			handler?.({ speed_bytes_per_sec: null, eta_seconds: null });
		});
		expect(result.current.speedBps).toBe(100);
		expect(result.current.etaSeconds).toBe(5);

		// A transition event clears them — the old measurement window
		// is over. Guards against stale speed/ETA clinging to a
		// finished download.
		act(() => {
			handler?.({ status: "downloading", speed_bytes_per_sec: null });
		});
		expect(result.current.speedBps).toBeNull();
		expect(result.current.etaSeconds).toBeNull();
	});
});

describe("useModelDownload — downloadModel success path", () => {
	it("marks the model as downloaded (NOT active — backend truth is re-fetched), surfaces success snack, clears downloading state", async () => {
		callMock.mockResolvedValue({ success: true, message: "ok" });
		const setModels = vi.fn();
		const reconcileAfterDownload = vi.fn().mockResolvedValue(undefined);
		const args = makeHookArgs({
			setModels: setModels as never,
			reconcileAfterDownload,
		});

		const { result } = renderHook(() => useModelDownload(args));
		const model = makeModel({ name: "tiny" });

		await act(async () => {
			await result.current.downloadModel(model);
		});

		// setModels invoked with updater that flags the just-downloaded
		// model as downloaded:true and isActive:FALSE — the backend does
		// not auto-activate, so an optimistic Active badge here showed a
		// phantom "Active" state while dictation still used the previous
		// model. reconcileAfterDownload re-fetches config/status so the
		// badge reflects backend truth.
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
		expect(small?.isActive).toBe(false);
		expect(reconcileAfterDownload).toHaveBeenCalledTimes(1);

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

		// The failure toast now flows through the canonical snackbar
		// system: showSnack(msg, "error", { action }) — the duration
		// comes from the error-type default (8000ms).
		expect(args.showSnack).toHaveBeenCalledWith(
			"disk full",
			"error",
			expect.objectContaining({
				action: expect.objectContaining({ label: expect.any(String) }),
			}),
		);
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
		// The failure toast flows through showSnack with a Retry action.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("network down"),
			"error",
			expect.objectContaining({ action: expect.any(Object) }),
		);
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

describe("useModelDownload — downloadModel queued path (backend FIFO queue)", () => {
	it("queued outcome surfaces an info snack, does NOT mark downloaded, leaves the active bar intact", async () => {
		// Model A ("tiny") is actively downloading (its IPC promise never
		// resolves in this test); model B ("base") is clicked while A
		// transfers → the backend answers {queued: true}.
		callMock.mockImplementation(
			(cmd: string, data?: Record<string, unknown>) => {
				if (cmd === "download_model" && data?.model === "tiny") {
					return new Promise(() => {});
				}
				if (cmd === "download_model" && data?.model === "base") {
					return Promise.resolve({
						success: true,
						queued: true,
						model: "base",
						queue_position: 1,
						message:
							"Queued — it starts automatically when the current download finishes.",
					});
				}
				return Promise.resolve({});
			},
		);
		const setModels = vi.fn();
		const reconcileAfterDownload = vi.fn().mockResolvedValue(undefined);
		const args = makeHookArgs({
			setModels: setModels as never,
			reconcileAfterDownload,
		});

		const { result } = renderHook(() => useModelDownload(args));

		// Start A's download (never resolves), let its claim flush.
		act(() => {
			void result.current.downloadModel(makeModel({ name: "tiny" }));
		});
		await act(async () => {});
		expect(result.current.downloadingModel).toBe("tiny");
		// Seed the ACTIVE download's live progress so the "bar stays
		// intact" assertion is meaningful.
		act(() => {
			getDownloadProgressHandler()?.({
				model: "tiny",
				progress: 50,
				status: "downloading",
			});
		});
		expect(result.current.downloadProgress).toBe(50);

		// Click B while A transfers.
		await act(async () => {
			await result.current.downloadModel(makeModel({ name: "base" }));
		});

		// NOT a success: no downloaded-marking, no reconcile, no failure.
		expect(setModels).not.toHaveBeenCalled();
		expect(reconcileAfterDownload).not.toHaveBeenCalled();
		expect(result.current.failedDownload).toBeNull();
		// The queued message surfaces as an INFO-type snack.
		expect(args.showSnack).toHaveBeenCalledWith(
			"Queued — it starts automatically when the current download finishes.",
			"info",
		);
		// The ACTIVE download keeps the single progress-bar slot (and its
		// live progress) — the queued request never claimed it.
		expect(result.current.downloadingModel).toBe("tiny");
		expect(result.current.downloadProgress).toBe(50);
	});

	it("queued outcome with no backend message falls back to the localized key", async () => {
		callMock.mockResolvedValue({ success: true, queued: true, model: "tiny" });
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		await act(async () => {
			await result.current.downloadModel(makeModel({ name: "tiny" }));
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.downloadQueued: name=tiny",
			"info",
		);
		// No promise claimed the bar (slot was free, queued released it).
		expect(result.current.downloadingModel).toBeNull();
	});

	it("re-click of the ACTIVE model (already-active outcome) warns and keeps the live bar", async () => {
		// First click: never resolves (the transfer runs). Second click of
		// the SAME model: the backend answers download_already_active.
		let callCount = 0;
		callMock.mockImplementation(() => {
			callCount += 1;
			if (callCount === 1) return new Promise(() => {});
			return Promise.resolve({
				success: true,
				model: "tiny",
				download_already_active: true,
				message: "Download of tiny is already in progress.",
			});
		});
		const setModels = vi.fn();
		const args = makeHookArgs({ setModels: setModels as never });

		const { result } = renderHook(() => useModelDownload(args));
		act(() => {
			void result.current.downloadModel(makeModel({ name: "tiny" }));
		});
		await act(async () => {});
		expect(result.current.downloadingModel).toBe("tiny");

		await act(async () => {
			await result.current.downloadModel(makeModel({ name: "tiny" }));
		});

		// The live transfer's bar survives the duplicate click.
		expect(result.current.downloadingModel).toBe("tiny");
		expect(setModels).not.toHaveBeenCalled();
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.downloadAlreadyActiveName: name=tiny",
			"warning",
		);
	});
});

describe("useModelDownload — handleCancelDownload(modelName) (queued-model cancel)", () => {
	it("forwards the model name in the IPC payload and leaves the active state intact on queue removal", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "download_model") {
				// The ACTIVE transfer keeps running (its promise never resolves).
				return new Promise(() => {});
			}
			return Promise.resolve({
				cancelled: true,
				model: "base",
				removed_from_queue: true,
			});
		});
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		act(() => {
			void result.current.downloadModel(makeModel({ name: "tiny" }));
		});
		await act(async () => {});
		// Seed the ACTIVE download (its state must survive the queued
		// cancel).
		act(() => {
			const handler = getDownloadProgressHandler();
			handler?.({ model: "tiny", progress: 50, status: "downloading" });
		});
		expect(result.current.downloadingModel).toBe("tiny");
		expect(result.current.downloadProgress).toBe(50);

		await act(async () => {
			await result.current.handleCancelDownload("base");
		});

		expect(callMock).toHaveBeenCalledWith("cancel_model_download", {
			model: "base",
		});
		// Queue-removal snack (info), NOT the active-cancel warning.
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.queuedCancelled: name=base",
			"info",
		);
		// The active transfer's bar + progress survive.
		expect(result.current.downloadingModel).toBe("tiny");
		expect(result.current.downloadProgress).toBe(50);
		expect(result.current.failedDownload).toBeNull();
	});

	it("named cancel of the ACTIVE model (race) runs the legacy active-cancel semantics", async () => {
		// The queued model auto-started between render and click → the named
		// cancel hits the ACTIVE transfer.
		callMock.mockResolvedValue({ cancelled: true });
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		await act(async () => {
			await result.current.handleCancelDownload("tiny");
		});

		expect(callMock).toHaveBeenCalledWith("cancel_model_download", {
			model: "tiny",
		});
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.cancelled",
			"warning",
		);
		// Active-cancel clears local state.
		expect(result.current.downloadingModel).toBeNull();
		expect(result.current.downloadProgress).toBe(0);
	});

	it("named cancel of a model that is neither queued nor active is a benign no-op (state intact)", async () => {
		callMock.mockResolvedValue({ cancelled: false });
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		act(() => {
			const handler = getDownloadProgressHandler();
			handler?.({ model: "tiny", progress: 50, status: "downloading" });
		});
		act(() => {
			void result.current.downloadModel(makeModel({ name: "tiny" }));
		});
		await act(async () => {});
		expect(result.current.downloadingModel).toBe("tiny");

		await act(async () => {
			await result.current.handleCancelDownload("base");
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			"models.snack.cancelNoopName: name=base",
			"info",
		);
		// No active-cancel state clear.
		expect(result.current.downloadingModel).toBe("tiny");
	});

	it("named cancel IPC failure surfaces the error and leaves the active state intact", async () => {
		callMock.mockRejectedValue(new Error("cancel IPC failed"));
		const args = makeHookArgs();

		const { result } = renderHook(() => useModelDownload(args));
		act(() => {
			void result.current.downloadModel(makeModel({ name: "tiny" }));
		});
		await act(async () => {});
		expect(result.current.downloadingModel).toBe("tiny");

		await act(async () => {
			await result.current.handleCancelDownload("base");
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("cancel IPC failed"),
			"error",
		);
		// The active transfer's state survives a queued-cancel IPC failure.
		expect(result.current.downloadingModel).toBe("tiny");
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
