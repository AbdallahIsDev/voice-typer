/**
 * E2E persistence regression tests for the Vocabulary page — the exact
 * reproduction that exposed the fake-deletion bug:
 *
 *   1. load the page
 *   2. delete an entry (single / bulk / Clear All) — or edit it
 *   3. navigate away and back (the page unmounts and re-mounts,
 *      re-fetching `get_vocabulary`)
 *   4. the deleted entry must NOT reappear; the edited value must stick
 *
 * WHY A STATEFUL MINI-BACKEND: the existing tests stub `get_vocabulary`
 * with a STATIC object, so a save that never reaches the backend (or a
 * stale payload) still "passes" — the reload would return the same seed
 * either way. This file instead drives a faithful in-memory port of the
 * backend's `save_vocabulary_with_diff`: the user store holds a DIFF
 * against the bundled defaults plus `_deleted` tombstones, and
 * `get_vocabulary` returns the MERGED view computed fresh on every
 * call. A delete that does not persist (no save, failed save, or a
 * stale payload that still contains the row) therefore resurfaces on
 * re-mount exactly as the real bug did.
 *
 * The backend-side half of this regression lives in
 * `tests/test_vocabulary_delete_persistence.py` (real disk reads).
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	nextThemesMock,
	pythonMock,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { VocabularyData } from "@/types/ipc";

const { mockCall, toastError } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock({ routeToSonner: true }));
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

// ── Faithful mini-backend (port of save_vocabulary_with_diff) ──────────

const DICT_CATS = new Set([
	"misspellings",
	"technical_terms",
	"names",
	"products",
]);
const ALL_CATS = [
	"misspellings",
	"phrase_corrections",
	"extra_word_patterns",
	"technical_terms",
	"names",
	"products",
] as const;
type Cat = (typeof ALL_CATS)[number];
type MiniStore = Partial<
	Record<Cat, Record<string, string> | Array<[string, string]>>
> & {
	_deleted?: Record<string, unknown[]>;
};

function emptyCat(cat: Cat): Record<string, string> | Array<[string, string]> {
	return DICT_CATS.has(cat) ? {} : [];
}

/**
 * Stateful in-memory backend double. `bundled` is immutable; the user
 * store holds the diff against it (+ `_deleted` tombstones), exactly
 * like the real user vocabulary.json. `merged()` recomputes the view
 * from the store on every call — a "reload" is just a fresh `merged()`.
 */
function makeMiniBackend(bundled: VocabularyData) {
	let store: MiniStore = {};

	const merged = (): VocabularyData => {
		const out = {} as Record<string, unknown>;
		for (const cat of ALL_CATS) {
			const b = (bundled[cat] ?? emptyCat(cat)) as Record<string, string>;
			const u = (store[cat] ?? emptyCat(cat)) as Record<string, string>;
			const toms = (store._deleted?.[cat] ?? []) as unknown[];
			if (DICT_CATS.has(cat)) {
				const m: Record<string, string> = {};
				for (const [k, v] of Object.entries(b)) m[k] = v;
				for (const [k, v] of Object.entries(u)) m[k] = v;
				for (const k of toms) if (typeof k === "string") delete m[k];
				out[cat] = m;
			} else {
				const list: Array<[string, string]> = [];
				for (const p of b as unknown as Array<[string, string]>) list.push(p);
				for (const p of u as unknown as Array<[string, string]>) list.push(p);
				// Tombstones are stored as raw [wrong, correct] pairs (like
				// the real backend's ``_deleted``), matched by JSON
				// equality. NEVER pre-stringify the tombstone entries —
				// stringifying an already-stringified pair would never
				// match (the exact bug this test file guards against).
				const tombPairs = new Set(toms.map((p) => JSON.stringify(p)));
				out[cat] = list.filter((p) => !tombPairs.has(JSON.stringify(p)));
			}
		}
		return out as VocabularyData;
	};

	const save = (payload: VocabularyData) => {
		// 1. user_only diff against the bundled defaults
		const userOnly = {} as Partial<
			Record<Cat, Record<string, string> | Array<[string, string]>>
		>;
		for (const cat of ALL_CATS) {
			const incoming = (payload[cat] ?? emptyCat(cat)) as Record<
				string,
				string
			>;
			const b = (bundled[cat] ?? emptyCat(cat)) as Record<string, string>;
			if (DICT_CATS.has(cat)) {
				const diff: Record<string, string> = {};
				for (const [k, v] of Object.entries(incoming)) {
					if (b[k] !== v) diff[k] = v;
				}
				if (Object.keys(diff).length > 0) userOnly[cat] = diff;
			} else {
				const bs = new Set(
					(b as unknown as Array<[string, string]>).map((p) =>
						JSON.stringify(p),
					),
				);
				const diff = (incoming as unknown as Array<[string, string]>).filter(
					(p) => !bs.has(JSON.stringify(p)),
				);
				if (diff.length > 0) userOnly[cat] = diff;
			}
		}
		// 2. deletion tombstones: present in the CURRENT merged view but
		//    absent from the incoming payload
		const prevDeleted = (store._deleted ?? {}) as Record<string, unknown[]>;
		const deleted: Record<string, unknown[]> = {};
		for (const cat of ALL_CATS) {
			const incoming = (payload[cat] ?? emptyCat(cat)) as Record<
				string,
				string
			>;
			const mergedCat = merged()[cat] as Record<string, string>;
			const prev = prevDeleted[cat] ?? [];
			if (DICT_CATS.has(cat)) {
				// Dict cats: tombstones are plain string keys.
				const removed = new Set<string>();
				const incKeys = new Set(Object.keys(incoming));
				for (const k of Object.keys(mergedCat))
					if (!incKeys.has(k)) removed.add(k);
				for (const k of prev) {
					if (typeof k === "string" && !incKeys.has(k)) removed.add(k);
				}
				if (removed.size > 0) deleted[cat] = [...removed].sort();
			} else {
				// List cats: tombstones are RAW [wrong, correct] pairs
				// (the real backend's ``_deleted`` holds pairs too) — NOT
				// their JSON strings. ``merged`` re-stringifies on every
				// read, so storing pre-stringified pairs would double-
				// encode and never match (the exact bug this test file
				// guards against). Map json→raw pair to dedupe by equality
				// while preserving the raw shape.
				const incPairs = new Set(
					(incoming as unknown as Array<[string, string]>).map((p) =>
						JSON.stringify(p),
					),
				);
				const removedPairs = new Map<string, [string, string]>();
				for (const p of mergedCat as unknown as Array<[string, string]>) {
					const key = JSON.stringify(p);
					if (!incPairs.has(key)) removedPairs.set(key, p);
				}
				for (const p of prev as unknown as Array<[string, string]>) {
					const key = JSON.stringify(p);
					if (!incPairs.has(key)) removedPairs.set(key, p);
				}
				if (removedPairs.size > 0) {
					deleted[cat] = [...removedPairs.values()].sort((a, b) =>
						JSON.stringify(a).localeCompare(JSON.stringify(b)),
					);
				}
			}
		}
		store = { ...userOnly } as MiniStore;
		if (Object.keys(deleted).length > 0) store._deleted = deleted;
		return { success: true };
	};

	return { merged, save };
}

// ── Wiring the page to the mini-backend ────────────────────────────────

const renderPage = () =>
	render(
		<TooltipProvider delayDuration={200}>
			<VocabularyPage />
		</TooltipProvider>,
	);

let backend: ReturnType<typeof makeMiniBackend>;
const BUNDLED: VocabularyData = {
	misspellings: { recieve: "receive" },
	phrase_corrections: [["to 2 ", "to "]],
	technical_terms: {},
	names: {},
	products: {},
	extra_word_patterns: [],
};

/**
 * Seed the store the way the renderer would after an add: the FULL
 * merged payload (bundled + new user entries) — the renderer always
 * sends the complete list, never a partial one.
 */
function seedUser(
	extra: Partial<
		Record<string, Record<string, string> | Array<[string, string]>>
	>,
) {
	const full = backend.merged();
	for (const [cat, entries] of Object.entries(extra)) {
		const current = (full[cat as Cat] ?? emptyCat(cat as Cat)) as Record<
			string,
			string
		>;
		(full as Record<string, unknown>)[cat] = {
			...(current as object),
			...(entries as object),
		};
	}
	backend.save(full);
}

function wireMock() {
	backend = makeMiniBackend(BUNDLED);
	mockCall.mockImplementation((type: unknown, data?: unknown) => {
		const cmd =
			typeof type === "string"
				? type
				: ((type as { type?: string })?.type ?? "");
		if (cmd === "get_vocabulary") return Promise.resolve(backend.merged());
		if (cmd === "save_vocabulary") {
			// the renderer passes the full payload as the second arg
			return Promise.resolve(backend.save((data ?? {}) as VocabularyData));
		}
		if (cmd === "get_correction_usage") return Promise.resolve({ entries: {} });
		return Promise.resolve({});
	});
}

// lazy import (the page pulls in the whole hook stack)
import VocabularyPage from "../../Vocabulary";

describe("Vocabulary page — delete/edit persistence across reload (fake-deletion regression)", () => {
	beforeEach(() => {
		wireMock();
	});

	afterEach(() => {
		cleanup();
		vi.clearAllMocks();
	});

	const deleteRow = async (ariaLabel: string) => {
		const btn = screen.getByRole("button", { name: ariaLabel });
		fireEvent.click(btn);
		// the delete is optimistic + persisted async — wait for the row
		// to leave the DOM AND the save round-trip to settle (the save
		// is what makes the change survive a reload; without it the row
		// comes back on remount)
		await waitFor(() =>
			expect(screen.queryByRole("button", { name: ariaLabel })).toBeNull(),
		);
		await waitFor(() => {
			const saves = mockCall.mock.calls.filter(
				(args: unknown[]) => args[0] === "save_vocabulary",
			);
			expect(saves.length).toBeGreaterThan(0);
		});
	};

	const remount = () => {
		cleanup();
		renderPage();
	};

	it("single delete of a USER entry persists after navigate-away-and-back", async () => {
		// seed a user entry so the delete targets a user-added row
		seedUser({ misspellings: { myword: "myfix" } });
		renderPage();
		await screen.findByText("myword");

		await deleteRow("Delete: myword");

		// navigate away and back — a fresh mount re-fetches from the
		// (now updated) store
		remount();
		await screen.findByText("recieve");
		expect(screen.queryByText("myword")).toBeNull();
		// the untouched bundled entry is still there
		expect(screen.getByText("receive")).toBeTruthy();
	});

	it("single delete of a BUNDLED default entry persists after reload (tombstone path)", async () => {
		renderPage();
		await screen.findByText("recieve");

		await deleteRow("Delete: recieve");

		remount();
		// the bundled phrase row (untouched by the delete) is still there
		await screen.findByText(/to 2/);
		expect(screen.queryByText("recieve")).toBeNull();
		expect(screen.queryByText("receive")).toBeNull();
	});

	it("bulk delete persists after reload", async () => {
		seedUser({ misspellings: { aaa: "AAA", bbb: "BBB" } });
		renderPage();
		await screen.findByText("aaa");

		// select both user rows via their checkboxes
		const selectAria = (name: string) => `Select ${name}`;
		fireEvent.click(screen.getByRole("checkbox", { name: selectAria("aaa") }));
		fireEvent.click(screen.getByRole("checkbox", { name: selectAria("bbb") }));
		fireEvent.click(screen.getByRole("button", { name: /Delete selected/ }));

		await waitFor(() => expect(screen.queryByText("aaa")).toBeNull());

		remount();
		await screen.findByText("recieve");
		expect(screen.queryByText("aaa")).toBeNull();
		expect(screen.queryByText("bbb")).toBeNull();
	});

	it("Clear All persists after reload — bundled defaults stay cleared", async () => {
		renderPage();
		await screen.findByText("recieve");

		fireEvent.click(screen.getByLabelText("Clear all vocabulary entries"));
		await waitFor(() =>
			expect(screen.getByText("Clear All Vocabulary")).toBeTruthy(),
		);
		const confirm = screen
			.getByRole("alertdialog")
			.querySelector("button:last-of-type");
		expect(confirm).toBeTruthy();
		fireEvent.click(confirm as HTMLElement);

		await waitFor(() => expect(screen.queryByText("recieve")).toBeNull());
		await waitFor(() => {
			const saves = mockCall.mock.calls.filter(
				(args: unknown[]) => args[0] === "save_vocabulary",
			);
			expect(saves.length).toBeGreaterThan(0);
		});
		// the CLEARED store is genuinely empty (read from the persistence
		// layer, not React state)
		expect(backend.merged()).toEqual({
			misspellings: {},
			phrase_corrections: [],
			extra_word_patterns: [],
			technical_terms: {},
			names: {},
			products: {},
		});

		remount();
		// the page shows the empty state — nothing comes back, bundled
		// defaults included
		await waitFor(() => expect(screen.queryByText("recieve")).toBeNull());
		expect(screen.queryByText(/to 2/)).toBeNull();
		expect(screen.getByText(/No corrections yet/)).toBeTruthy();
	});

	it("edit persists after reload (updated correction sticks)", async () => {
		renderPage();
		await screen.findByText("recieve");

		fireEvent.click(screen.getByRole("button", { name: "Edit: recieve" }));
		const replacement = screen.getByRole("textbox", {
			name: /What gets typed/i,
		});
		fireEvent.change(replacement, { target: { value: "recieve" } });
		fireEvent.click(screen.getByRole("button", { name: "Save" }));

		await waitFor(() => expect(screen.queryByText("receive")).toBeNull());

		remount();
		// Both cells now read "recieve" (original → corrected), so use
		// the row's Edit button (aria-label contains the original) as
		// the presence probe, then confirm the OLD correction value is
		// gone.
		await screen.findByRole("button", { name: "Edit: recieve" });
		expect(screen.getAllByText("recieve").length).toBeGreaterThanOrEqual(2);
		// the correction value is now "recieve" (saved) — the row
		// renders original → correction, so the old "receive" is gone
		expect(screen.queryByText("receive")).toBeNull();
	});

	it("a failed save surfaces an error toast and does NOT pretend success", async () => {
		// make save_vocabulary reject — the UI must restore the row and
		// show an error, never a false success
		mockCall.mockImplementation((type: unknown) => {
			const cmd =
				typeof type === "string"
					? type
					: ((type as { type?: string })?.type ?? "");
			if (cmd === "get_vocabulary") return Promise.resolve(backend.merged());
			if (cmd === "save_vocabulary")
				return Promise.reject(new Error("disk full"));
			if (cmd === "get_correction_usage")
				return Promise.resolve({ entries: {} });
			return Promise.resolve({});
		});
		renderPage();
		await screen.findByText("recieve");

		await deleteRow("Delete: recieve");
		// the failure path restores the pre-delete list
		await waitFor(() => expect(screen.getByText("recieve")).toBeTruthy());
		expect(toastError).toHaveBeenCalled();
	});
});
