/**
 * Tests for the Vocabulary page — D2-FIX (b-review Finding 4).
 *
 * Scenario under test: clicking the trash icon on a vocabulary entry fires
 * `instantDeleteEntry`, which:
 *   1. Filters the entry out of the local `entries` state and calls
 *      `save_vocabulary` to persist the change.
 *   2. Shows an undoable toast (6 s window) so the user can click Undo.
 *
 * The undo callback previously closed over the render-time `entries`
 * snapshot, which STILL INCLUDED the deleted entry (because
 * `instantDeleteEntry` reads `entries` to compute `updated` via `.filter`,
 * but never replaces `entries` in the closure).  When the user clicked
 * Undo (up to 6 s later), `restored = [...entries]` contained `entry` at
 * its original index, `restored.indexOf(entry)` returned that index, and
 * `restored.splice(idx, 0, entry)` (deleteCount=0) INSERTED A SECOND COPY
 * at that index — the entry reappeared TWICE after Undo.  The closure was
 * also stale with respect to any other vocabulary edits made between the
 * delete and the Undo click — those edits were silently lost.
 *
 * The D2 fix reads the LATEST entries via a ref (`entriesRef.current`,
 * kept in sync by a `useEffect`) inside the undo callback, filters out
 * the deleted entry defensively, and splices it back at its captured
 * original index — guaranteeing exactly ONE copy is restored regardless
 * of concurrent edits.
 *
 * The test seeds 3 vocabulary entries, deletes one, captures the undo
 * callback from the `toast.warning` call, invokes it, and asserts that
 * the deleted entry reappears EXACTLY ONCE (not twice) and the list
 * returns to 3 entries.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) => {
	const wrapped = (node: React.ReactElement) => (
		<TooltipProvider delayDuration={200}>{node}</TooltipProvider>
	);
	const utils = render(wrapped(ui));
	return {
		...utils,
		rerender: (node: React.ReactElement) => utils.rerender(wrapped(node)),
	};
};

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Hoist the mock call handler so it's available inside vi.mock factories.
const { mockCall } = vi.hoisted(() => ({
	mockCall: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
}));

// Stub the hugeicons runtime wrapper so the trash/edit icons render
// without pulling in the full @hugeicons/react renderer.
vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// sonner: capture toast.warning / toast.success calls so the test can
// invoke the Undo callback (passed as `action.onClick` in the warning
// toast's options).  Each mock is a fresh vi.fn() per test run.
const toastWarning = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => toastSuccess(...args),
		error: (...args: unknown[]) => toastError(...args),
		warning: (...args: unknown[]) => toastWarning(...args),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

import type { VocabularyData } from "@/types/ipc";

/** 3 vocabulary entries across two categories. */
const seedData: VocabularyData = {
	// misspellings is a {trigger → correction} object → 2 entries
	misspellings: {
		recieve: "receive",
		teh: "the",
	},
	// phrase_corrections is an array of [trigger, correction] tuples → 1 entry
	phrase_corrections: [["i am going to", "I'm going to"]],
};

/** Count `save_vocabulary` IPC calls captured by mockCall. */
function saveCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "save_vocabulary",
	).length;
}

describe("Vocabulary page — D2-FIX undo duplicates", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary") return Promise.resolve(seedData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		toastWarning.mockClear();
		toastSuccess.mockClear();
		toastError.mockClear();
		localStorage.clear();
		// Reset the module registry so Vocabulary's module-level
		// state (if any) is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("Undo restores exactly ONE copy of the deleted entry (not two)", async () => {
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		// Wait for the 3 seeded entries to render.  Each entry's
		// `original` field is displayed in red (text-destructive).
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.getByText("i am going to")).toBeTruthy();

		// Sanity: the list has exactly 3 entries before the delete.
		expect(screen.getAllByText(/recieve|teh|i am going to/).length).toBe(3);

		// Click the trash icon on the "recieve" entry.  The button's
		// aria-label is "Delete: recieve" (from the
		// vocabulary.deleteAria template with {name} interpolated).
		fireEvent.click(screen.getByLabelText("Delete: recieve"));

		// The delete path is async: it calls `save_vocabulary` then
		// shows the undoable toast.  Wait for the IPC call.
		await waitFor(() => {
			expect(saveCallCount()).toBe(1);
		});

		// The undoable toast is rendered via `toast.warning(message,
		// { action: { label, onClick: onUndo } })`.  Wait for that
		// call so we can extract the onUndo callback.
		await waitFor(() => {
			expect(toastWarning).toHaveBeenCalledTimes(1);
		});

		// After the delete, "recieve" should be GONE from the list,
		// and the remaining 2 entries ("teh" and "i am going to")
		// should still be present.
		await waitFor(() => {
			expect(screen.queryByText("recieve")).toBeNull();
		});
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.getByText("i am going to")).toBeTruthy();

		// Extract the Undo callback from the captured toast.warning
		// call args and invoke it (simulates the user clicking the
		// toast's Undo button).
		const warningArgs = toastWarning.mock.calls[0];
		const opts = warningArgs?.[1] as
			| { action?: { onClick?: () => void } }
			| undefined;
		const undoFn = opts?.action?.onClick;
		expect(typeof undoFn).toBe("function");
		// `undoFn?.()` calls the function if defined; the prior
		// expect() guarantees it IS defined, so this is equivalent
		// to `undoFn()` without the non-null assertion.
		undoFn?.();

		// Wait for the restore path to complete: `save_vocabulary`
		// is called again (with the restored list) and a success
		// toast is shown.
		await waitFor(() => {
			expect(saveCallCount()).toBe(2);
		});
		await waitFor(() => {
			expect(toastSuccess).toHaveBeenCalledWith("Entry restored");
		});

		// D2-FIX assertion: "recieve" is restored EXACTLY ONCE.
		// Before the fix, the stale-closure bug caused
		// `splice(idx, 0, entry)` (deleteCount=0) to INSERT a second
		// copy while leaving the original in place, so the entry
		// reappeared TWICE after Undo.  After the fix, the undo
		// callback filters the entry out of the latest list BEFORE
		// splicing, guaranteeing exactly ONE copy.
		const restoredRecieveElements = screen.getAllByText("recieve");
		expect(restoredRecieveElements.length).toBe(1);

		// The list is back to 3 entries (recieve, teh,
		// "i am going to"), not 4 (duplicated recieve) or 2
		// (failed restore).
		expect(screen.getByText("recieve")).toBeTruthy();
		expect(screen.getByText("teh")).toBeTruthy();
		expect(screen.getByText("i am going to")).toBeTruthy();
		expect(screen.getAllByText(/recieve|teh|i am going to/).length).toBe(3);

		// No error toast should have fired during the restore.
		expect(toastError).not.toHaveBeenCalled();
	});

	it("Undo preserves concurrent edits made between delete and undo click", async () => {
		// D2-FIX bonus: the closure was previously stale with
		// respect to any other vocabulary edits made between the
		// delete and the Undo click — those edits were silently
		// lost because the restore replaced the current list with
		// the stale pre-delete snapshot.  After the fix, the undo
		// callback reads the LATEST entries via `entriesRef.current`
		// and only re-inserts the deleted entry, preserving any
		// concurrent edits.
		//
		// We simulate a "concurrent edit" by deleting a SECOND
		// entry ("teh") between the first delete ("recieve") and
		// the Undo click.  Before the fix, the first Undo's stale
		// closure held the pre-delete-#1 snapshot (containing BOTH
		// recieve AND teh), so the restore would resurrect BOTH —
		// silently reverting the user's concurrent "delete teh"
		// edit.  After the fix, the undo reads the LATEST list
		// (which has neither recieve nor teh, just "i am going to")
		// and inserts ONLY "recieve" back — "teh" stays deleted.
		mockCall.mockImplementation((arg: unknown) => {
			const type =
				typeof arg === "string"
					? arg
					: ((arg as { type?: string })?.type ?? "");
			if (type === "get_vocabulary") return Promise.resolve(seedData);
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		const { rerender } = renderWithProviders(<VocabularyPage />);

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Delete "recieve".
		fireEvent.click(screen.getByLabelText("Delete: recieve"));
		await waitFor(() => {
			expect(toastWarning).toHaveBeenCalledTimes(1);
		});
		await waitFor(() => {
			expect(screen.queryByText("recieve")).toBeNull();
		});

		// Simulate a concurrent edit: delete "teh" too (so the
		// current state has only "i am going to" left).  This
		// happens BEFORE the user clicks Undo on "recieve".
		fireEvent.click(screen.getByLabelText("Delete: teh"));
		await waitFor(() => {
			expect(screen.queryByText("teh")).toBeNull();
		});
		// Two delete toasts have fired now.
		expect(toastWarning).toHaveBeenCalledTimes(2);

		// Now invoke the FIRST Undo (for "recieve").  Before the
		// fix, the closure held the pre-delete-#1 snapshot
		// (containing BOTH recieve AND teh), so the restore would
		// resurrect BOTH "recieve" AND "teh" — silently reverting
		// the user's concurrent "delete teh" edit.  After the fix,
		// the undo reads the LATEST list (which has neither recieve
		// nor teh, just "i am going to") and inserts ONLY "recieve"
		// back — "teh" stays deleted.
		const firstWarningArgs = toastWarning.mock.calls[0];
		const firstOpts = firstWarningArgs?.[1] as
			| { action?: { onClick?: () => void } }
			| undefined;
		const undoFn = firstOpts?.action?.onClick;
		expect(typeof undoFn).toBe("function");
		undoFn?.();

		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// "recieve" is restored exactly once.
		expect(screen.getAllByText("recieve").length).toBe(1);
		// "teh" must STAY deleted — the concurrent edit is preserved.
		expect(screen.queryByText("teh")).toBeNull();
		// "i am going to" is still present.
		expect(screen.getByText("i am going to")).toBeTruthy();

		// Sanity: the list now has 2 entries (recieve + "i am going to").
		expect(screen.getAllByText(/recieve|teh|i am going to/).length).toBe(2);

		// Trigger a rerender to make sure the state is stable.
		rerender(<VocabularyPage />);
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});
		expect(screen.queryByText("teh")).toBeNull();
	});
});
