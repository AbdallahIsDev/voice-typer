/**
 * Unit tests for `useModelFolder`.
 *
 * Coverage :
 *   - diskInfo + modelsFolderSupported stay at their constant null/false values
 *     (the optional probes were removed — see the hook's docstring)
 *   - handleImportModel bails out (info-level snackbar) when window.window_
 *     is unavailable (e.g. outside Electron) — covers the "outside-Electron"
 *     permission/error path
 *   - handleImportModel surfaces a warning snack when no models are found
 *     in the picked folder (success=true, found.length=0)
 *   - handleImportModel surfaces an error snack when the backend reports
 *     failedAll (success=true, found.length>0, imported.length=0)
 *   - handleImportModel propagates a thrown IPC error via the failed snack
 *   - handleImportModel flips isImporting=true during the IPC round-trip
 *     and clears it in the finally block
 *   - handleOpenModelsFolder is a no-op (preserved for backwards-compat)
 *
 * Strategy: renderHook with a mocked `call` IPC fn + a stubbed
 * `window.window_.openModelImportDialog`.
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
import { useModelFolder } from "@/hooks/models/useModelFolder";

const callMock = vi.fn();
const showSnackMock = vi.fn();
const loadConfigMock = vi.fn().mockResolvedValue(undefined);

function makeHookArgs() {
	return {
		call: callMock as unknown as <T = unknown>(
			cmd: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		showSnack: showSnackMock,
		loadConfig: loadConfigMock,
	};
}

/** Stub `window.window_.openModelImportDialog` — the Electron folder picker. */
function setOpenModelImportDialog(
	impl: () => Promise<{ canceled: boolean; path: string | null }>,
) {
	const w = window as unknown as {
		window_?: {
			openModelImportDialog?: () => Promise<{
				canceled: boolean;
				path: string | null;
			}>;
		};
	};
	w.window_ = { openModelImportDialog: impl };
}

function clearWindowBridge() {
	const w = window as unknown as { window_?: unknown };
	delete w.window_;
}

beforeEach(() => {
	callMock.mockReset();
	showSnackMock.mockReset();
	loadConfigMock.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
	vi.clearAllMocks();
	clearWindowBridge();
});

describe("useModelFolder — initial state (phantom probes removed)", () => {
	it("exposes diskInfo=null + modelsFolderSupported=false (constants — phantom probes removed)", () => {
		const { result } = renderHook(() => useModelFolder(makeHookArgs()));
		expect(result.current.diskInfo).toBeNull();
		expect(result.current.modelsFolderSupported).toBe(false);
	});

	it("isImporting defaults to false", () => {
		const { result } = renderHook(() => useModelFolder(makeHookArgs()));
		expect(result.current.isImporting).toBe(false);
	});
});

describe("useModelFolder — handleImportModel error paths", () => {
	it("bails out with a warning snack when window.window_ is unavailable (outside Electron)", async () => {
		// No window.window_ stub installed — simulates running outside
		// Electron (e.g. in a browser dev shell or a test environment
		// without the preload bridge).
		clearWindowBridge();

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		expect(showSnackMock).toHaveBeenCalledWith(
			"a11y.importNotAvailableOutsideElectron",
			"warning",
		);
		// IPC never invoked (we bailed before reaching the IPC call).
		expect(callMock).not.toHaveBeenCalled();
		// isImporting stays false (we bailed before the setIsImporting(true)).
		expect(result.current.isImporting).toBe(false);
	});

	it("bails out silently when the folder picker is canceled (no snack, no IPC)", async () => {
		setOpenModelImportDialog(async () => ({ canceled: true, path: null }));

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		expect(showSnackMock).not.toHaveBeenCalled();
		expect(callMock).not.toHaveBeenCalled();
		expect(result.current.isImporting).toBe(false);
	});

	it("surfaces a warning snack when the backend finds 0 models in the picked folder", async () => {
		setOpenModelImportDialog(async () => ({
			canceled: false,
			path: "/home/user/empty",
		}));
		callMock.mockResolvedValue({
			success: true,
			imported: [],
			found: [],
			errors: [],
		});

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		expect(callMock).toHaveBeenCalledWith("import_model", {
			dir_path: "/home/user/empty",
		});
		expect(showSnackMock).toHaveBeenCalledWith(
			"models.import.noModelsFound",
			"warning",
		);
		// loadConfig NOT called when no models were imported.
		expect(loadConfigMock).not.toHaveBeenCalled();
		expect(result.current.isImporting).toBe(false);
	});

	it("surfaces an error snack when the backend reports failedAll (found>0, imported=0)", async () => {
		setOpenModelImportDialog(async () => ({
			canceled: false,
			path: "/home/user/bad",
		}));
		callMock.mockResolvedValue({
			success: true,
			imported: [],
			found: [{ name: "broken-model" }] as never,
			errors: [{ model: "broken-model", error: "checksum mismatch" }],
		});

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		expect(showSnackMock).toHaveBeenCalledWith(
			"models.import.failedAll",
			"error",
		);
		// loadConfig NOT called when nothing was imported.
		expect(loadConfigMock).not.toHaveBeenCalled();
		expect(result.current.isImporting).toBe(false);
	});

	it("surfaces an error snack when the import_model IPC throws", async () => {
		setOpenModelImportDialog(async () => ({
			canceled: false,
			path: "/home/user/perm-denied",
		}));
		callMock.mockRejectedValue(new Error("EACCES permission denied"));

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		// Error snack surfaced with the formatted error message.
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("EACCES permission denied"),
			"error",
		);
		// isImporting cleared in the finally block — even on error.
		expect(result.current.isImporting).toBe(false);
	});
});

describe("useModelFolder — handleImportModel success path", () => {
	it("calls loadConfig + surfaces success snack when ≥1 model is imported", async () => {
		setOpenModelImportDialog(async () => ({
			canceled: false,
			path: "/home/user/good",
		}));
		callMock.mockResolvedValue({
			success: true,
			imported: ["large-v3-turbo", "tiny"],
			found: ["large-v3-turbo", "tiny"],
			errors: [],
		});

		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await act(async () => {
			await result.current.handleImportModel();
		});

		expect(loadConfigMock).toHaveBeenCalledTimes(1);
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("models.import.success"),
			"success",
		);
		expect(result.current.isImporting).toBe(false);
	});
});

describe("useModelFolder — handleOpenModelsFolder (no-op)", () => {
	it("resolves without invoking any IPC (no-op preserved for backwards-compat)", async () => {
		const { result } = renderHook(() => useModelFolder(makeHookArgs()));

		await expect(
			result.current.handleOpenModelsFolder(),
		).resolves.toBeUndefined();
		expect(callMock).not.toHaveBeenCalled();
	});
});
