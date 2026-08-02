/**
 * Navigation hook + shared store.
 *
 * SHARED-STATE FIX: navigation state lives in a module-level Zustand
 * store, NOT per-instance `useState`. Previously each `useNavigation()`
 * call site (App.tsx, Home, Settings, History, Dashboard,
 * AudioSettingsSection) created its own independent `currentPage` +
 * history — so a page that navigated itself (e.g. the Settings
 * "Re-run setup wizard" button, Home's "View all" / "Open mic
 * settings" links, Dashboard's "Start dictation") updated ONLY its own
 * local state and App.tsx's router never re-rendered. Every such
 * in-page navigation was a dead button in production.
 *
 * With a single shared store, `navigate()` from any component updates
 * the same `currentPage` App.tsx reads, so in-page navigation works and
 * App's route guard / document.title / focus effects react to it.
 *
 * Persistence is preserved: the store is initialised from localStorage
 * once at module load and every transition is written back, so the user
 * returns to where they left off after closing/reopening the app.
 *
 * `_resetNavigationForTest` is the test seam (same pattern as
 * `_resetSoundManagerForTests` / `_resetFileSizeCacheForTest`): it
 * re-reads localStorage into the store so a test can seed a persisted
 * page and then reset the shared state deterministically.
 */

import { useEffect } from "react";
import { create } from "zustand";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

// Persist the current page + nav history to localStorage
// so the user returns to where they left off after closing/reopening
// the app.  Previously the app always started at 'home' and the
// user's navigation history was lost.
const STORAGE_KEY_NAV = "vt_nav_state";

// Cap the in-memory nav history to avoid unbounded growth
// if a user navigates hundreds of times in a single session. 50 is
// generous (browsers cap tab history at ~50 for similar reasons) and
// the slice preserves back/forward semantics: when the cap is hit the
// oldest entry is dropped, the current pointer is kept pointing at the
// same logical entry (its index shifts down by 1).
const MAX_NAV_HISTORY = 50;

// The runtime page registry — the set of known page names
// and the `isKnownPage` type guard — now lives in `router/routes.ts`
// (single source of truth, mirrored from the `Page` union in
// `types/ipc.ts`). Previously this file maintained its own
// `KNOWN_PAGES` Set, which was one of four parallel copies of the
// page registry and had to be hand-kept in sync with the type union.
// See `router/routes.ts` for the rationale.

interface NavState {
	page: Page;
	history: Page[];
	index: number;
}

function defaultNavState(): NavState {
	return { page: "home", history: ["home"], index: 0 };
}

function loadNavState(): NavState {
	try {
		const raw = localStorage.getItem(STORAGE_KEY_NAV);
		if (raw) {
			const parsed = JSON.parse(raw) as {
				page?: unknown;
				history?: unknown;
				index?: unknown;
			};
			// Validate `parsed.page` against the known Page
			// union so a corrupted localStorage payload (e.g. a user
			// hand-edited devtools, or a stale schema from an older
			// build) cannot inject an unknown page value into React
			// state. Previously any truthy `parsed.page` was accepted
			// and cast to `Page` — surfacing as a "Page not found"
			// loop on every render.
			if (
				isKnownPage(parsed.page) &&
				Array.isArray(parsed.history) &&
				parsed.history.length > 0 &&
				parsed.history.every(isKnownPage) &&
				typeof parsed.index === "number" &&
				parsed.index >= 0 &&
				parsed.index < parsed.history.length
			) {
				return {
					page: parsed.page,
					history: parsed.history as Page[],
					index: parsed.index,
				};
			}
		}
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed renderer)
		// or the stored payload may be malformed JSON. Non-fatal —
		// fall through to the default nav state so the app still boots.
		console.warn("[useNavigation] loadNavState failed, using default:", e);
	}
	return defaultNavState();
}

function saveNavState(state: NavState): void {
	try {
		localStorage.setItem(STORAGE_KEY_NAV, JSON.stringify(state));
	} catch (e) {
		// localStorage may be unavailable (SSR, sandboxed
		// renderer) or quota may be exceeded. Non-fatal — the
		// in-memory nav state is still authoritative for the
		// current session; we just lose cross-session persistence.
		console.warn("[useNavigation] saveNavState failed:", e);
	}
}

interface NavStore extends NavState {
	navigate: (page: Page) => void;
	replace: (page: Page) => void;
	goBack: () => void;
	goForward: () => void;
}

const useNavStore = create<NavStore>()((set, get) => {
	/** Apply a new nav state + persist it to localStorage. */
	const apply = (next: NavState): void => {
		set(next);
		saveNavState(next);
	};

	return {
		...loadNavState(),
		navigate: (page) => {
			const { page: current, history, index } = get();
			// No-op when navigating to the page we're already
			// on. Previously this still pushed a duplicate entry onto
			// the history stack and re-saved localStorage, polluting
			// the back/forward chain (Ctrl+Click on a sidebar entry
			// the user was already on would add a no-op step they had
			// to back through). The early return preserves the
			// existing history and index exactly.
			if (page === current) return;

			let nextHistory = [...history.slice(0, index + 1), page];
			let nextIndex = index + 1;

			// Cap the history so a long-lived session
			// doesn't accumulate hundreds of entries (each one is
			// serialized into localStorage on every navigate). Drop
			// from the front; shift the index so the current pointer
			// still points at the same logical entry.
			if (nextHistory.length > MAX_NAV_HISTORY) {
				const overflow = nextHistory.length - MAX_NAV_HISTORY;
				nextHistory = nextHistory.slice(overflow);
				nextIndex = Math.max(0, nextIndex - overflow);
			}

			apply({ page, history: nextHistory, index: nextIndex });
		},
		/**
		 * Replace the current history entry with `page` without
		 * pushing a new entry onto the stack. Mirrors
		 * `history.replaceState` in the browser API. Use this for
		 * route guards that should NOT appear in the back/forward
		 * history (e.g. the onboarding-completed guard bouncing a
		 * user from `onboarding` → `home` shouldn't leave a "home"
		 * entry sitting on top of the original "onboarding" entry,
		 * otherwise the user could press Back and land back in the
		 * wizard they just finished).
		 *
		 * If `page === currentPage`, this is a no-op (same rationale
		 * as `navigate`).
		 */
		replace: (page) => {
			const { page: current, history, index } = get();
			if (page === current) return;
			const nextHistory = [...history];
			nextHistory[index] = page;
			apply({ page, history: nextHistory, index });
		},
		goBack: () => {
			const { history, index } = get();
			if (index <= 0) return;
			const target = history[index - 1];
			// noUncheckedIndexedAccess: `target` is `Page | undefined`.
			// The index is bounded by the guard above and the history
			// is append-only, but TS still widens the read; explicit
			// guard keeps the setter / save-call signatures happy.
			if (target === undefined) return;
			apply({ page: target, history, index: index - 1 });
		},
		goForward: () => {
			const { history, index } = get();
			if (index >= history.length - 1) return;
			const target = history[index + 1];
			if (target === undefined) return;
			apply({ page: target, history, index: index + 1 });
		},
	};
});

/**
 * Test seam — re-read localStorage into the shared store.
 *
 * The store is a module-level singleton, so state survives across tests
 * in the same file unless reset. Tests that seed `vt_nav_state` and
 * then mount a component must call this AFTER seeding so the store
 * picks up the persisted page. @internal
 */
export function _resetNavigationForTest(): void {
	useNavStore.setState(loadNavState());
}

/**
 * Navigation hook: subscribes to the shared navigation store (current
 * page + browser-style history stack), persists state to localStorage,
 * and wires mouse forward/back buttons (X1/X2) + Alt+Arrow to act like
 * a browser's back/forward navigation.
 *
 * Every call site shares the SAME store, so a `navigate()` call from
 * any page re-renders App.tsx's router (see the module docstring).
 */
export function useNavigation() {
	const currentPage = useNavStore((s) => s.page);
	const history = useNavStore((s) => s.history);
	const index = useNavStore((s) => s.index);
	const navigate = useNavStore((s) => s.navigate);
	const replace = useNavStore((s) => s.replace);
	const goBack = useNavStore((s) => s.goBack);
	const goForward = useNavStore((s) => s.goForward);

	// Mouse forward/back buttons (X1/X2) navigate like a browser
	useEffect(() => {
		const handler = (e: MouseEvent) => {
			if (e.button === 3) {
				e.preventDefault();
				goBack();
			} else if (e.button === 4) {
				e.preventDefault();
				goForward();
			}
		};
		document.addEventListener("mouseup", handler);
		return () => document.removeEventListener("mouseup", handler);
	}, [goBack, goForward]);

	// Keyboard equivalent for back/forward navigation: Alt+ArrowLeft
	// goes back, Alt+ArrowRight goes forward — matching the behaviour
	// of every major browser so users with a keyboard-only workflow
	// get the same affordance the mouse X1/X2 buttons already provide.
	//
	// The handler is suppressed when focus is inside an editable
	// element (`<input>`, `<textarea>`, `<select>`, or
	// `contentEditable`) so a user editing a text field with arrow keys
	// doesn't get yanked to another page just because they happened to
	// hold Alt. The browser's own Alt+Arrow text-editing shortcuts
	// (e.g. Alt+Left/Right to move the caret by word on macOS) keep
	// working because we return before calling `preventDefault()` in
	// that branch.
	useEffect(() => {
		const handler = (e: KeyboardEvent) => {
			if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
			if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;

			const target = e.target as HTMLElement | null;
			const tag = target?.tagName?.toLowerCase() ?? "";
			const typing =
				tag === "input" ||
				tag === "textarea" ||
				tag === "select" ||
				target?.isContentEditable === true;
			if (typing) return;

			e.preventDefault();
			if (e.key === "ArrowLeft") {
				goBack();
			} else {
				goForward();
			}
		};
		document.addEventListener("keydown", handler);
		return () => document.removeEventListener("keydown", handler);
	}, [goBack, goForward]);

	return {
		currentPage,
		navigate,
		replace,
		goBack,
		goForward,
		canGoBack: index > 0,
		canGoForward: index < history.length - 1,
	};
}
