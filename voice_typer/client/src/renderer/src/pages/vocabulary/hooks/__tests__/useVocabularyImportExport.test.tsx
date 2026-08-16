// Hook-level tests for useVocabularyImportExport:
//   - import happy path (dedupe + persist + success toast)
//   - import blocked by the BACKEND duplicate check
//     (client.duplicate_entry) → targeted toast, nothing persisted
//   - doExport(format, entries) passes exactly the given rows to the
//     export bridge
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

vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) => {
			// Resolve the few keys the hook uses.
			if (key === "vocabulary.importDuplicate")
				return "Import blocked — a correction already exists";
			if (key === "vocabulary.importSuccessSingular") return "Imported 1 entry";
			if (key === "vocabulary.importSuccessPlural")
				return `Imported ${params?.count} entries`;
			if (key === "vocabulary.importEmpty")
				return "File contains no vocabulary entries";
			if (key === "vocabulary.importFailed")
				return `Import failed: ${params?.error}`;
			if (key === "vocabulary.exportNotAvailable")
				return "Export not available — please restart the app";
			if (key === "vocabulary.exportFailed") return "Export failed";
			if (key === "vocabulary.exportSaved")
				return `${params?.filename} saved successfully`;
			return key;
		},
	};
});

import type { VocabRow } from "../../lib/transform";
import { useVocabularyImportExport } from "../useVocabularyImportExport";

function makeRow(
	original: string,
	correction: string,
	category: VocabRow["category"],
	id = original,
): VocabRow {
	return { _id: id, original, correction, category };
}

function setup() {
	const call = vi.fn();
	const persistVocabulary = vi.fn().mockResolvedValue(undefined);
	const setEntries = vi.fn();
	const entriesRef: React.RefObject<VocabRow[]> = {
		current: [makeRow("recieve", "receive", "misspellings")],
	};
	const { result } = renderHook(() =>
		useVocabularyImportExport({
			call,
			entriesRef,
			persistVocabulary,
			setEntries,
		}),
	);
	return { call, persistVocabulary, setEntries, result };
}

function importFile(
	handleImportFile: (f: File | undefined | null) => Promise<void>,
	content: string,
) {
	return act(async () => {
		await handleImportFile({
			text: () => Promise.resolve(content),
		} as File);
	});
}

beforeEach(() => {
	toastSuccess.mockClear();
	toastError.mockClear();
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("useVocabularyImportExport", () => {
	it("imports new entries and persists the merged list", async () => {
		const { persistVocabulary, result } = setup();
		await importFile(
			result.current.handleImportFile,
			JSON.stringify([{ original: "teh", correction: "the" }]),
		);
		expect(persistVocabulary).toHaveBeenCalledTimes(1);
		const merged = persistVocabulary.mock.calls[0]?.[0] as VocabRow[];
		expect(merged.map((e) => e.original)).toEqual(["recieve", "teh"]);
		expect(toastSuccess).toHaveBeenCalledWith("Imported 1 entry");
	});

	it("surfaces the backend duplicate rejection with the targeted toast and does not persist", async () => {
		const { persistVocabulary, setEntries, result } = setup();
		// The backend write path (save_vocabulary_with_diff) rejects
		// with client.duplicate_entry — the import must surface the
		// targeted message and NOT persist the merged list.
		const err = new Error(
			"duplicate correction: 'recieve' (2 entries)",
		) as Error & {
			code?: string;
		};
		err.code = "client.duplicate_entry";
		persistVocabulary.mockRejectedValueOnce(err);

		await importFile(
			result.current.handleImportFile,
			JSON.stringify([{ original: "recieve", correction: "receive" }]),
		);

		expect(toastError).toHaveBeenCalledWith(
			"Import blocked — a correction already exists",
		);
		expect(persistVocabulary).toHaveBeenCalledTimes(1);
		// The failed save must NOT update the entries state.
		expect(setEntries).not.toHaveBeenCalled();
	});

	it("passes exactly the selected rows to the export bridge", async () => {
		const exportVocabulary = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/x.json" });
		Object.defineProperty(window, "window_", {
			writable: true,
			configurable: true,
			value: { exportVocabulary },
		});
		const { result } = setup();
		const selected = [
			makeRow("recieve", "receive", "misspellings"),
			makeRow("teh", "the", "misspellings"),
		];
		await act(async () => {
			await result.current.doExport("json", selected);
		});
		expect(exportVocabulary).toHaveBeenCalledWith(
			{
				entries: [
					{
						original: "recieve",
						correction: "receive",
						category: "misspellings",
					},
					{ original: "teh", correction: "the", category: "misspellings" },
				],
			},
			"json",
		);
	});
});
