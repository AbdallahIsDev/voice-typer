// Hook-level tests for useCollectionImportExport, the shared
// import/export round-trip skeleton the Vocabulary and Templates pages
// will migrate onto (Wave 5).
//
// Drives the skeleton through a minimal fake domain (rows with ids,
// items with labels) and proves every parameter injection point:
//   IMPORT:  parse → empty check → dedupe-merge → persist →
//            singular/plural toasts; parse throw → failed toast;
//            persist rejection → duplicate-aware toast paths
//   EXPORT:  selected-rows vs full-list item fetch → bridge outcome
//            mapping (saved / unavailable / rejected / throw)
//   PLUMBING: hidden input ref (reset after import, click on
//            handleImportClick), default "json" export format
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
		error: (...args: unknown[]) => toastError(...args),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

// t() mock: keys pass through, params are JSON-suffixed so the
// interpolation values are visible in the assertions.
vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) =>
			params ? `${key}:${JSON.stringify(params)}` : key,
	};
});

import { useCollectionImportExport } from "@/hooks/useCollectionImportExport";

/** Fake domain row (the page's list state). */
interface TestRow {
	id: string;
	label: string;
}

/** Fake domain item (the persisted import/export shape). */
interface TestItem {
	label: string;
}

const MESSAGES = {
	importEmpty: "m.importEmpty",
	importSuccessSingular: "m.importSuccessSingular",
	importSuccessPlural: "m.importSuccessPlural",
	importFailed: "m.importFailed",
	importDuplicate: "m.importDuplicate",
	exportNotAvailable: "m.exportNotAvailable",
	exportSaved: "m.exportSaved",
	exportFailed: "m.exportFailed",
};

function makeFile(content: string): File {
	return { text: () => Promise.resolve(content) } as File;
}

interface SetupOptions {
	existing?: TestItem[];
	importedText?: string;
	isDuplicateError?: (err: unknown) => boolean;
	notifyOnExportRejected?: boolean;
	exportResult?: { success: boolean; path?: string; error?: string } | null;
	getExportItems?: (rows?: TestRow[]) => Promise<TestItem[]> | TestItem[];
}

function setup(options: SetupOptions = {}) {
	const existing: TestItem[] = options.existing ?? [{ label: "keep" }];
	const rowsRef: React.RefObject<TestRow[]> = {
		current: [{ id: "1", label: "keep" }],
	};
	const persistMerged = vi.fn().mockResolvedValue(undefined);
	// Default export outcome: a successful save. `null` (the bridge
	// unavailable sentinel) and failures must be passed EXPLICITLY.
	const exportFile = vi
		.fn()
		.mockImplementation(async () =>
			options.exportResult === undefined
				? { success: true, path: "/default.json" }
				: options.exportResult,
		);
	const parseImported = vi.fn((text: string) => JSON.parse(text) as TestItem[]);
	const rowKey = (item: TestItem) => item.label;
	const readExisting = vi.fn(() => existing);
	const getExportItems =
		options.getExportItems ??
		((rows?: TestRow[]) =>
			rows ? rows.map((r) => ({ label: r.label })) : existing);

	const { result } = renderHook(() =>
		useCollectionImportExport<TestRow, TestItem>({
			debugLabel: "Test",
			parseImported,
			rowKey,
			readExisting,
			persistMerged,
			isDuplicateError: options.isDuplicateError,
			notifyOnExportRejected: options.notifyOnExportRejected,
			getExportItems,
			exportFile,
			messages: MESSAGES,
		}),
	);
	void rowsRef;
	return {
		result,
		parseImported,
		persistMerged,
		exportFile,
	};
}

async function importText(
	handleImportFile: (f: File | undefined | null) => Promise<void>,
	content: string,
) {
	await act(async () => {
		await handleImportFile(makeFile(content));
	});
}

beforeEach(() => {
	toastSuccess.mockClear();
	toastError.mockClear();
	vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("useCollectionImportExport, import round trip", () => {
	it("merges new items (deduped by rowKey), persists the merged list, and shows the plural success toast", async () => {
		const { result, persistMerged } = setup({
			existing: [{ label: "keep" }],
		});
		await importText(
			result.current.handleImportFile,
			JSON.stringify([{ label: "keep" }, { label: "new1" }, { label: "new2" }]),
		);

		expect(persistMerged).toHaveBeenCalledTimes(1);
		expect(persistMerged).toHaveBeenCalledWith([
			{ label: "keep" },
			{ label: "new1" },
			{ label: "new2" },
		]);
		expect(toastSuccess).toHaveBeenCalledTimes(1);
		expect(toastSuccess).toHaveBeenCalledWith(
			'm.importSuccessPlural:{"count":"2"}',
		);
	});

	it("shows the singular success toast when exactly one item is added", async () => {
		const { result } = setup({ existing: [{ label: "keep" }] });
		await importText(result.current.handleImportFile, '[{"label":"new"}]');
		expect(toastSuccess).toHaveBeenCalledWith("m.importSuccessSingular");
	});

	it("shows the plural toast with count 0 when every imported item is a duplicate (documented current behavior)", async () => {
		const { result, persistMerged } = setup({ existing: [{ label: "keep" }] });
		await importText(result.current.handleImportFile, '[{"label":"keep"}]');
		// The merged list still persists (idempotent re-import).
		expect(persistMerged).toHaveBeenCalledWith([{ label: "keep" }]);
		expect(toastSuccess).toHaveBeenCalledWith(
			'm.importSuccessPlural:{"count":"0"}',
		);
	});

	it("routes an empty (but well-formed) import to the empty toast without persisting", async () => {
		const { result, persistMerged } = setup();
		await importText(result.current.handleImportFile, "[]");
		expect(toastError).toHaveBeenCalledWith("m.importEmpty");
		expect(persistMerged).not.toHaveBeenCalled();
		expect(toastSuccess).not.toHaveBeenCalled();
	});

	it("routes a parse throw to the failed toast with the error message, without persisting", async () => {
		const { result, persistMerged } = setup();
		await importText(result.current.handleImportFile, "not json");
		expect(toastError).toHaveBeenCalledTimes(1);
		expect(toastError).toHaveBeenCalledWith(
			// The mocked t() suffixes interpolated params as JSON —
			// assert the key + an interpolated error payload (the exact
			// V8 parse message varies across Node versions).
			expect.stringMatching(/^m\.importFailed:\{"error":".+not json/),
		);
		expect(persistMerged).not.toHaveBeenCalled();
	});

	it("routes a persist rejection through isDuplicateError to the targeted duplicate toast when the pair is provided", async () => {
		const isDuplicateError = (err: unknown) =>
			(err as { code?: string })?.code === "client.duplicate_entry";
		const { result, persistMerged } = setup({ isDuplicateError });
		persistMerged.mockRejectedValueOnce(
			Object.assign(new Error("duplicate"), { code: "client.duplicate_entry" }),
		);
		await importText(result.current.handleImportFile, '[{"label":"new"}]');

		expect(toastError).toHaveBeenCalledTimes(1);
		expect(toastError).toHaveBeenCalledWith("m.importDuplicate");
	});

	it("routes a persist rejection to the generic failed toast when isDuplicateError is absent (the no-drift-adapter form)", async () => {
		const { result, persistMerged } = setup();
		persistMerged.mockRejectedValueOnce(new Error("boom"));
		await importText(result.current.handleImportFile, '[{"label":"new"}]');

		expect(toastError).toHaveBeenCalledTimes(1);
		expect(toastError).toHaveBeenCalledWith('m.importFailed:{"error":"boom"}');
	});

	it("no-ops on a null/undefined file (picker canceled)", async () => {
		const { result, parseImported } = setup();
		await act(async () => {
			await result.current.handleImportFile(undefined);
		});
		expect(parseImported).not.toHaveBeenCalled();
		expect(toastError).not.toHaveBeenCalled();
	});

	it("resets the hidden input's value after the round trip so re-selecting the same file fires onChange again", async () => {
		const { result } = setup();
		const input = document.createElement("input");
		input.value = "/tmp/list.json";
		result.current.importInputRef.current = input;
		await importText(result.current.handleImportFile, '[{"label":"new"}]');
		expect(input.value).toBe("");
	});

	it("handleImportClick clicks the hidden input the ref points at", () => {
		const { result } = setup();
		const input = document.createElement("input");
		const clickSpy = vi.spyOn(input, "click");
		result.current.importInputRef.current = input;
		act(() => {
			result.current.handleImportClick();
		});
		expect(clickSpy).toHaveBeenCalledTimes(1);
	});
});

describe("useCollectionImportExport, export round trip", () => {
	it("saved outcome: derives the filename from the path and shows the exportSaved toast", async () => {
		const { result, exportFile } = setup({
			exportResult: { success: true, path: "/tmp/exports/list.json" },
		});
		await act(async () => {
			await result.current.doExport("csv");
		});
		expect(exportFile).toHaveBeenCalledTimes(1);
		// The page's items are forwarded with the picked format.
		expect(exportFile).toHaveBeenCalledWith([{ label: "keep" }], "csv");
		expect(toastSuccess).toHaveBeenCalledWith(
			'm.exportSaved:{"filename":"list.json"}',
		);
	});

	it("saved outcome: windows-style paths split on backslashes", async () => {
		const { result } = setup({
			exportResult: { success: true, path: "C:\\Users\\me\\list.json" },
		});
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastSuccess).toHaveBeenCalledWith(
			'm.exportSaved:{"filename":"list.json"}',
		);
	});

	it("saved outcome: a pathless result falls back to the 'untitled' filename", async () => {
		const { result } = setup({ exportResult: { success: true } });
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastSuccess).toHaveBeenCalledWith(
			'm.exportSaved:{"filename":"untitled"}',
		);
	});

	it("unavailable outcome (adapter returns null): shows the not-available toast", async () => {
		const { result } = setup({ exportResult: null });
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastError).toHaveBeenCalledWith("m.exportNotAvailable");
		expect(toastSuccess).not.toHaveBeenCalled();
	});

	it("rejected outcome WITHOUT notifyOnExportRejected is silent (the Vocabulary form)", async () => {
		const { result } = setup({
			exportResult: { success: false, error: "disk full" },
		});
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastError).not.toHaveBeenCalled();
		expect(toastSuccess).not.toHaveBeenCalled();
	});

	it("rejected outcome WITH notifyOnExportRejected surfaces the backend error (the Templates form)", async () => {
		const { result } = setup({
			exportResult: { success: false, error: "disk full" },
			notifyOnExportRejected: true,
		});
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastError).toHaveBeenCalledWith("disk full");
	});

	it("rejected outcome WITH notifyOnExportRejected and no error message falls back to the generic exportFailed key", async () => {
		const { result } = setup({
			exportResult: { success: false },
			notifyOnExportRejected: true,
		});
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(toastError).toHaveBeenCalledWith("m.exportFailed");
	});

	it("adapter throw: logs to console.error and shows the generic exportFailed toast", async () => {
		const { result, exportFile } = setup();
		exportFile.mockRejectedValueOnce(new Error("IPC died"));
		const consoleError = vi.mocked(console.error);
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(consoleError).toHaveBeenCalledWith(
			"[renderer:useCollectionImportExport] Test export failed:",
			expect.any(Error),
		);
		expect(toastError).toHaveBeenCalledWith("m.exportFailed");
	});

	it("defaults the export format to json when omitted (the superset signature)", async () => {
		const { result, exportFile } = setup({
			exportResult: { success: true, path: "/x.json" },
		});
		await act(async () => {
			await result.current.doExport();
		});
		expect(exportFile).toHaveBeenCalledWith([{ label: "keep" }], "json");
	});

	it("passes the selected rows through to the domain's getExportItems (bulk 'Export selected')", async () => {
		const getExportItems = vi.fn((rows?: TestRow[]) =>
			rows ? rows.map((r) => ({ label: r.label })) : [],
		);
		const { result, exportFile } = setup({
			getExportItems,
			exportResult: { success: true, path: "/sel.json" },
		});
		const selected: TestRow[] = [{ id: "1", label: "a" }];
		await act(async () => {
			await result.current.doExport("csv", selected);
		});
		expect(getExportItems).toHaveBeenCalledWith(selected);
		expect(exportFile).toHaveBeenCalledWith([{ label: "a" }], "csv");
	});

	it("full export asks the domain for the items with no rows (the page fetches from its own source)", async () => {
		const getExportItems = vi.fn(() => [{ label: "all" }]);
		const { result, exportFile } = setup({
			getExportItems,
			exportResult: { success: true, path: "/all.json" },
		});
		await act(async () => {
			await result.current.doExport("json");
		});
		expect(getExportItems).toHaveBeenCalledWith(undefined);
		expect(exportFile).toHaveBeenCalledWith([{ label: "all" }], "json");
	});
});
