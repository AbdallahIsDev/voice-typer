// Hook-level tests for useRowSelection — the shared selection state
// machine the Vocabulary and Templates pages run on (via their thin
// feature wrappers). The hook previously had NO direct tests (only
// page-level coverage); these pin the state-machine contract for the
// Wave-5 migration:
//   - click-select toggle / select-many (select-all) / clear
//   - selectedCount + selectedRows derived through getRowId
//   - bulk delete: instant removal + selection clear + persist + the
//     6s Undo toast that restores every deleted row at its ORIGINAL
//     position
//   - failure rollback (persist throws → rows restored + error snack)
//
// Ref semantics modeled after the pages: setRows is the React state
// setter; the rowsRef mirrors state POST-COMMIT (the pages sync it in
// a render effect), so the harness only advances the ref when a test
// explicitly commits.
//
// Mocks: sonner toast, the showUndoableToast wrapper (captured so the
// undo callback can be invoked), and identity-with-params t().
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

let capturedUndo: (() => void) | undefined;
vi.mock("@/hooks/useSnackbar", () => ({
	showUndoableToast: vi.fn((_message: string, onUndo: () => void) => {
		capturedUndo = onUndo;
	}),
}));

vi.mock("@/i18n/i18n", async () => {
	const actual = await import("@/i18n/i18n");
	return {
		...actual,
		t: (key: string, params?: Record<string, unknown>) =>
			params ? `${key}:${JSON.stringify(params)}` : key,
	};
});

import { useRowSelection } from "@/hooks/useRowSelection";

interface TestRow {
	id: string;
	label: string;
}

type Selection = ReturnType<typeof useRowSelection<TestRow>>;

const MESSAGES = {
	bulkDeleteToast: "m.bulkDeleteToast",
	rowRestored: "m.rowRestored",
	restoreFailed: "m.restoreFailed",
	deleteFailed: "m.deleteFailed",
};

function rows(...labels: string[]): TestRow[] {
	return labels.map((label) => ({ id: `id-${label}`, label }));
}

function setup(initial: TestRow[] = rows("a", "b", "c")) {
	const rowsRef: React.RefObject<TestRow[]> = { current: initial };
	const persist = vi.fn().mockResolvedValue(undefined);
	const showSnack = vi.fn();
	const setRows = vi.fn();

	const { result, rerender } = renderHook(
		(props: { rows: TestRow[] }) =>
			useRowSelection<TestRow>({
				rows: props.rows,
				setRows,
				rowsRef,
				persist,
				showSnack,
				getRowId: (row: TestRow) => row.id,
				messages: MESSAGES,
			}),
		{ initialProps: { rows: initial } },
	);

	/** Emulate the page's post-commit ref sync. */
	const commit = (next: TestRow[]) => {
		rowsRef.current = next;
	};

	return { result, rerender, rowsRef, persist, showSnack, setRows, commit };
}

function select(result: { current: Selection }, ...ids: string[]) {
	act(() => {
		for (const id of ids) result.current.toggleSelect(id);
	});
}

beforeEach(() => {
	toastSuccess.mockClear();
	toastError.mockClear();
	capturedUndo = undefined;
	vi.clearAllMocks();
	vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
	vi.restoreAllMocks();
});

describe("useRowSelection — selection state machine", () => {
	it("toggleSelect adds an id, then removes it on the second toggle", () => {
		const { result } = setup();
		select(result, "id-a");
		expect(result.current.selectedIds.has("id-a")).toBe(true);
		expect(result.current.selectedCount).toBe(1);

		select(result, "id-a");
		expect(result.current.selectedIds.has("id-a")).toBe(false);
		expect(result.current.selectedCount).toBe(0);
	});

	it("toggleSelect accumulates a multi-selection one click at a time", () => {
		const { result } = setup();
		select(result, "id-a", "id-b", "id-c");
		expect(result.current.selectedCount).toBe(3);
		expect([...result.current.selectedIds]).toEqual(["id-a", "id-b", "id-c"]);
	});

	it("setSelectMany(ids, true) selects a whole visible page (the header's select-all path)", () => {
		const { result } = setup();
		act(() => {
			result.current.setSelectMany(["id-a", "id-b"], true);
		});
		expect(result.current.selectedCount).toBe(2);
	});

	it("setSelectMany(ids, false) deselects a subset without clearing the rest", () => {
		const { result } = setup();
		select(result, "id-a", "id-b", "id-c");
		act(() => {
			result.current.setSelectMany(["id-a", "id-c"], false);
		});
		expect([...result.current.selectedIds]).toEqual(["id-b"]);
	});

	it("clearSelection empties the selection", () => {
		const { result } = setup();
		select(result, "id-a", "id-b");
		act(() => {
			result.current.clearSelection();
		});
		expect(result.current.selectedCount).toBe(0);
	});

	it("selectedRows derives the row objects through getRowId and follows rows prop changes", () => {
		const { result, rerender } = setup();
		select(result, "id-a", "id-c");
		expect(result.current.selectedRows.map((r) => r.label)).toEqual(["a", "c"]);

		// The list re-renders with fresh row objects (e.g. re-sorted or
		// reloaded) — selectedRows follows the NEW objects while the
		// selection ids stay stable.
		const fresh = rows("b", "a", "c");
		rerender({ rows: fresh });
		expect(result.current.selectedRows.map((r) => r.label)).toEqual(["a", "c"]);
	});

	it("selectedRows drops rows that no longer exist while the id stays selected (stale ids are the page's display concern)", () => {
		const { result, rerender } = setup();
		select(result, "id-a");
		rerender({ rows: rows("b", "c") });
		expect(result.current.selectedCount).toBe(1);
		expect(result.current.selectedRows).toEqual([]);
	});
});

describe("useRowSelection — bulk delete with undo", () => {
	it("no-ops when nothing is selected (no persist, no state write, no toast)", async () => {
		const { result, persist, setRows } = setup();
		await act(async () => {
			await result.current.bulkDeleteSelected();
		});
		expect(persist).not.toHaveBeenCalled();
		expect(setRows).not.toHaveBeenCalled();
		const { showUndoableToast } = await import("@/hooks/useSnackbar");
		expect(vi.mocked(showUndoableToast)).not.toHaveBeenCalled();
	});

	it("removes the selected rows, clears the selection, persists the update, and shows the undo toast with the count", async () => {
		const { result, persist, setRows } = setup();
		select(result, "id-a", "id-c");
		await act(async () => {
			await result.current.bulkDeleteSelected();
		});

		expect(setRows).toHaveBeenCalledWith(rows("b"));
		expect(persist).toHaveBeenCalledWith(rows("b"));
		expect(result.current.selectedCount).toBe(0);
		// The undoable toast received the count-interpolated message.
		const { showUndoableToast } = await import("@/hooks/useSnackbar");
		expect(vi.mocked(showUndoableToast)).toHaveBeenCalledWith(
			'm.bulkDeleteToast:{"count":"2"}',
			expect.any(Function),
			expect.objectContaining({ undoLabel: "common.undo" }),
		);
	});

	it("undo restores every deleted row at its ORIGINAL position", async () => {
		const { result, persist, setRows, rowsRef, commit } = setup();
		select(result, "id-a", "id-c");
		await act(async () => {
			await result.current.bulkDeleteSelected();
		});
		// The page re-rendered and synced its ref to the post-delete list.
		commit(rows("b"));
		expect(rowsRef.current.map((r) => r.label)).toEqual(["b"]);

		await act(async () => {
			await capturedUndo?.();
		});

		// a and c return to their original indexes (0 and 2) — asserted
		// through the state writes the undo performs (the page's rowsRef
		// mirror is the page's concern, not the hook's).
		expect(setRows).toHaveBeenLastCalledWith(rows("a", "b", "c"));
		expect(persist).toHaveBeenLastCalledWith(rows("a", "b", "c"));
		expect(toastSuccess).toHaveBeenCalledWith("m.rowRestored");
	});

	it("undo still inserts rows (clamped) when intervening rows were removed underneath the toast", async () => {
		// Delete a + c from [a, b, c]; while the toast is up a concurrent
		// write removes b too — the restore must clamp its insert
		// positions instead of splicing past the end.
		const { result, setRows, commit } = setup();
		select(result, "id-a", "id-c");
		await act(async () => {
			await result.current.bulkDeleteSelected();
		});
		commit([]);

		await act(async () => {
			await capturedUndo?.();
		});
		expect(setRows).toHaveBeenLastCalledWith(rows("a", "c"));
		expect(toastSuccess).toHaveBeenCalledWith("m.rowRestored");
	});

	it("undo failure shows the restoreFailed toast and does not update rows", async () => {
		const { result, persist, setRows } = setup();
		select(result, "id-a");
		await act(async () => {
			await result.current.bulkDeleteSelected();
		});
		persist.mockRejectedValueOnce(new Error("restore failed"));

		await act(async () => {
			await capturedUndo?.();
		});
		expect(toastError).toHaveBeenCalledWith("m.restoreFailed");
		// setRows was NOT called again by the failed undo (the only call
		// remains the original delete).
		expect(setRows).toHaveBeenCalledTimes(1);
	});

	it("persist failure rolls the rows back and shows the error snack", async () => {
		const { result, persist, setRows, showSnack } = setup();
		select(result, "id-b");
		persist.mockRejectedValueOnce(new Error("save failed"));

		await act(async () => {
			await result.current.bulkDeleteSelected();
		});

		// The delete path called setRows(updated) first, then the persist
		// throw restored the pre-delete list (rowsRef still holds it —
		// the page's ref sync hasn't committed the delete yet).
		expect(setRows).toHaveBeenNthCalledWith(1, rows("a", "c"));
		expect(setRows).toHaveBeenNthCalledWith(2, rows("a", "b", "c"));
		expect(showSnack).toHaveBeenCalledWith("m.deleteFailed", "error");
	});
});
