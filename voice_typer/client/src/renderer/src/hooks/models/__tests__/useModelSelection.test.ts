/**
 * Unit tests for `useModelSelection`.
 *
 * Coverage :
 *   - selectModel success: persists asr_backend + model_size via updateConfig,
 *     optimistically flips isActive in the local list, calls refreshModelStatus,
 *     surfaces "usingModel" snack
 *   - selectModel model-switch ordering: depsOk guard fires first (deps required),
 *     downloaded guard fires second (not downloaded) — active model state unchanged
 *   - selectModel error: re-thrown from updateConfig surfaces "selectFailed" snack;
 *     setModels is NOT invoked
 *   - requestDeleteModel: refuses active model while it's on disk; ALLOWS
 *     deleting an active-but-missing (stale) model; stashes other targets
 *   - confirmDelete: fires delete_model IPC, updates local state, surfaces snack
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────
const { callMock } = vi.hoisted(() => ({
	callMock: vi.fn(),
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

import { useModelSelection } from "@/hooks/models/useModelSelection";
// ── Helpers ──────────────────────────────────────────────────────────
import type { ModelInfo } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

function makeModel(overrides: Partial<ModelInfo> = {}): ModelInfo {
	return {
		name: "tiny",
		size: "~466MB",
		speed: "Fast",
		backend: "whisper",
		downloaded: true,
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
		updateConfig?: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
	} = {},
) {
	const setModels =
		overrides.setModels ??
		(vi.fn() as unknown as React.Dispatch<React.SetStateAction<ModelInfo[]>>);
	const refreshModelStatus =
		overrides.refreshModelStatus ?? vi.fn().mockResolvedValue(undefined);
	const updateConfig =
		overrides.updateConfig ?? vi.fn().mockResolvedValue(undefined);
	const showSnack = vi.fn();
	return {
		call: (overrides.call ?? callMock) as unknown as <T = unknown>(
			cmd: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		showSnack,
		setModels,
		refreshModelStatus,
		updateConfig,
	};
}

beforeEach(() => {
	callMock.mockReset();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useModelSelection — selectModel success path", () => {
	it("persists asr_backend + model_size, flips isActive in local list, refreshes status, surfaces success snack", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const model = makeModel({
			name: "tiny",
			backend: "whisper",
		});

		await act(async () => {
			await result.current.selectModel(model);
		});

		// updateConfig invoked with the backend + model_size pair.
		expect(args.updateConfig).toHaveBeenCalledWith({
			asr_backend: "whisper",
			model_size: "tiny",
		});

		// setModels invoked with an updater that flips isActive on the
		// selected model and clears it on the others.
		expect(args.setModels).toHaveBeenCalledTimes(1);
		const updater = (args.setModels as unknown as ReturnType<typeof vi.fn>).mock
			.calls[0]?.[0] as (prev: ModelInfo[]) => ModelInfo[];
		const prev: ModelInfo[] = [
			makeModel({ name: "tiny" }),
			makeModel({ name: "large-v3-turbo", isActive: true }),
		];
		const next = updater(prev);
		expect(next.find((m) => m.name === "tiny")?.isActive).toBe(true);
		expect(next.find((m) => m.name === "large-v3-turbo")?.isActive).toBe(false);

		// refreshModelStatus reconciles the freshly-selected model.
		expect(args.refreshModelStatus).toHaveBeenCalledTimes(1);

		// Success snack surfaced.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.usingModel"),
			"success",
		);

		// selectingModel spinner state cleared in the finally block.
		expect(result.current.selectingModel).toBeNull();
	});
});

describe("useModelSelection — model-switch ordering (guards)", () => {
	it("refuses to select a deps-required model when depsOk is false (deps guard fires first)", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const model = makeModel({
			name: "parakeet",
			backend: "parakeet",
			downloaded: true, // downloaded so the downloaded guard would pass
			depsOk: false, // deps NOT installed — deps guard must fire
			depsInstallable: true,
		});

		await act(async () => {
			await result.current.selectModel(model);
		});

		// updateConfig MUST NOT be called — the deps guard bails before it.
		expect(args.updateConfig).not.toHaveBeenCalled();
		// setModels MUST NOT be called — no optimistic update.
		expect(args.setModels).not.toHaveBeenCalled();
		// Deps-required warning surfaced (generic {name} key).
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.depsRequiredName"),
			"warning",
		);
		// Spinner stays cleared.
		expect(result.current.selectingModel).toBeNull();
	});

	it("refuses to select a downloaded Qwen when qwen_asr deps are missing (deps guard fires, not the downloaded guard)", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		// Qwen weights ARE on disk (downloaded: true) but the optional
		// qwen_asr pip package is NOT importable (depsOk: false) — the
		// deps guard must fire. The `depsInstallable` flag now gates
		// Qwen exactly like Parakeet.
		const model = makeModel({
			name: "qwen",
			backend: "qwen",
			downloaded: true, // downloaded so the downloaded guard would pass
			depsOk: false, // qwen_asr missing — deps guard must fire
			depsInstallable: true,
		});

		await act(async () => {
			await result.current.selectModel(model);
		});

		expect(args.updateConfig).not.toHaveBeenCalled();
		expect(args.setModels).not.toHaveBeenCalled();
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.depsRequiredName"),
			"warning",
		);
		expect(result.current.selectingModel).toBeNull();
	});

	it("refuses to select an undownloaded model when depsOk is true (downloaded guard fires)", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const model = makeModel({
			name: "large-v3-turbo",
			backend: "whisper",
			downloaded: false,
			depsOk: true,
		});

		await act(async () => {
			await result.current.selectModel(model);
		});

		expect(args.updateConfig).not.toHaveBeenCalled();
		expect(args.setModels).not.toHaveBeenCalled();
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.notDownloaded"),
			"warning",
		);
	});

	it("refuses to select an undownloaded Qwen (no always-available bypass — Qwen is local-only, not auto-fetched)", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const model = makeModel({
			name: "qwen",
			backend: "qwen",
			downloaded: false,
			depsOk: true,
		});

		await act(async () => {
			await result.current.selectModel(model);
		});

		// updateConfig MUST NOT be called — the not-downloaded guard fires.
		expect(args.updateConfig).not.toHaveBeenCalled();
		expect(args.setModels).not.toHaveBeenCalled();
		// Warning snack surfaces (matches the backend's "model is not
		// downloaded yet" notification — the in-app UI must not claim
		// "Qwen model selected" for a model that isn't installed).
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.notDownloaded"),
			"warning",
		);
	});
});

describe("useModelSelection — selectModel error path", () => {
	it("surfaces selectFailed snack + does NOT update local models when updateConfig throws", async () => {
		const updateConfig = vi
			.fn()
			.mockRejectedValue(new Error("config save failed"));
		const args = makeHookArgs({ updateConfig });
		const { result } = renderHook(() => useModelSelection(args));

		const model = makeModel({ name: "tiny", backend: "whisper" });

		await act(async () => {
			await result.current.selectModel(model);
		});

		// updateConfig was attempted.
		expect(updateConfig).toHaveBeenCalledTimes(1);
		// setModels NEVER invoked — model state stays in sync with the
		// persisted backend config.
		expect(args.setModels).not.toHaveBeenCalled();
		// refreshModelStatus NEVER invoked either — we never reached it.
		expect(args.refreshModelStatus).not.toHaveBeenCalled();
		// Error snack surfaces the formatted error.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.selectFailed"),
			"error",
		);
		// Spinner cleared in the finally block.
		expect(result.current.selectingModel).toBeNull();
	});
});

describe("useModelSelection — requestDeleteModel + confirmDelete", () => {
	it("requestDeleteModel refuses the active model + surfaces cannotDeleteActive warning", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const active = makeModel({ name: "tiny", isActive: true });

		act(() => {
			result.current.requestDeleteModel(active);
		});

		// Target NOT stashed — the confirm dialog should not open.
		expect(result.current.deleteModelTarget).toBeNull();
		expect(args.showSnack).toHaveBeenCalledWith(
			"models.cannotDeleteActive",
			"warning",
		);
	});

	it("requestDeleteModel ALLOWS an active model that is missing from disk (stale selection)", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		// Active + downloaded: false → the model was removed from disk
		// out-of-band while the config still points at it. Deleting it is
		// the only way to clear the phantom "Active" state (the backend
		// switches to another model), so the confirm dialog MUST open.
		const staleActive = makeModel({
			name: "tiny",
			isActive: true,
			downloaded: false,
		});

		act(() => {
			result.current.requestDeleteModel(staleActive);
		});

		expect(result.current.deleteModelTarget).toEqual(staleActive);
		expect(args.showSnack).not.toHaveBeenCalled();
	});

	it("requestDeleteModel stashes a non-active model as deleteModelTarget", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const inactive = makeModel({ name: "large-v3-turbo", isActive: false });

		act(() => {
			result.current.requestDeleteModel(inactive);
		});

		expect(result.current.deleteModelTarget).toEqual(inactive);
		expect(args.showSnack).not.toHaveBeenCalled();
	});

	it("confirmDelete fires delete_model IPC, marks model as undownloaded + inactive, surfaces success snack", async () => {
		callMock.mockResolvedValue({ success: true });
		const setModels = vi.fn();
		const args = makeHookArgs({
			setModels: setModels as unknown as React.Dispatch<
				React.SetStateAction<ModelInfo[]>
			>,
		});
		const { result } = renderHook(() => useModelSelection(args));

		const target = makeModel({ name: "large-v3-turbo", isActive: false });
		act(() => {
			result.current.requestDeleteModel(target);
		});

		await act(async () => {
			await result.current.confirmDelete();
		});

		expect(callMock).toHaveBeenCalledWith("delete_model", {
			model: "large-v3-turbo",
		});

		// setModels updater marks the target as downloaded:false + isActive:false.
		expect(setModels).toHaveBeenCalledTimes(1);
		const updater = setModels.mock.calls[0]?.[0] as (
			prev: ModelInfo[],
		) => ModelInfo[];
		const prev: ModelInfo[] = [
			makeModel({ name: "large-v3-turbo", downloaded: true }),
		];
		const next = updater(prev);
		expect(next.find((m) => m.name === "large-v3-turbo")?.downloaded).toBe(
			false,
		);
		expect(next.find((m) => m.name === "large-v3-turbo")?.isActive).toBe(false);

		// Success snack + target cleared in the finally block.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.deleted"),
			"success",
		);
		expect(result.current.deleteModelTarget).toBeNull();
	});

	it("confirmDelete surfaces error snack when delete_model IPC throws", async () => {
		callMock.mockRejectedValue(new Error("fs permission denied"));
		const args = makeHookArgs();
		const { result } = renderHook(() => useModelSelection(args));

		const target = makeModel({ name: "large-v3-turbo" });
		act(() => {
			result.current.requestDeleteModel(target);
		});

		await act(async () => {
			await result.current.confirmDelete();
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("models.snack.deleteFailedError"),
			"error",
		);
		// Target cleared even on failure.
		expect(result.current.deleteModelTarget).toBeNull();
	});
});
