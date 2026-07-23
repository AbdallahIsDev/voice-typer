import { useCallback, useEffect, useRef, useState } from "react";
import { isKnownPage } from "@/router/routes";
import type { Page } from "@/types/ipc";

// NEW-UX-041: persist the current page + nav history to localStorage
// so the user returns to where they left off after closing/reopening
// the app.  Previously the app always started at 'home' and the
// user's navigation history was lost.
const STORAGE_KEY_NAV = "vt_nav_state";

// PVT-fix-14: cap the in-memory nav history to avoid unbounded growth
// if a user navigates hundreds of times in a single session. 50 is
// generous (browsers cap tab history at ~50 for similar reasons) and
// the slice preserves back/forward semantics: when the cap is hit the
// oldest entry is dropped, the current pointer is kept pointing at the
// same logical entry (its index shifts down by 1).
const MAX_NAV_HISTORY = 50;

// EC-FIX-13: the runtime page registry — the set of known page names
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
			// PVT-fix-15: validate `parsed.page` against the known Page
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

/**
 * Navigation hook: manages current page, browser-style history stack
 * (back/forward), and persists state to localStorage so the user
 * returns to where they left off after closing/reopening the app.
 *
 * Also wires up mouse forward/back buttons (X1/X2) to act like a
 * browser's back/forward navigation.
 */
export function useNavigation() {
	// PERF-FIX: previously `loadNavState()` was called on every render,
	// parsing localStorage + JSON.parse each time even though only the
	// first call's result is used (passed to useState/useRef initializers
	// which ignore subsequent values). Wrapped in a useState initializer
	// so React calls it exactly once and caches the result.
	const [initialNav] = useState(loadNavState);
	const [currentPage, setCurrentPage] = useState<Page>(initialNav.page);
	const navHistory = useRef<Page[]>(initialNav.history);
	const navIndex = useRef(initialNav.index);

	const saveNavState = useCallback(
		(page: Page, history: Page[], index: number) => {
			try {
				localStorage.setItem(
					STORAGE_KEY_NAV,
					JSON.stringify({ page, history, index }),
				);
			} catch (e) {
				// localStorage may be unavailable (SSR, sandboxed
				// renderer) or quota may be exceeded. Non-fatal — the
				// in-memory nav state is still authoritative for the
				// current session; we just lose cross-session persistence.
				console.warn("[useNavigation] saveNavState failed:", e);
			}
		},
		[],
	);

	const navigate = useCallback(
		(page: Page) => {
			// PVT-fix-13: no-op when navigating to the page we're already
			// on. Previously this still pushed a duplicate entry onto the
			// history stack and re-saved localStorage, polluting the
			// back/forward chain (Ctrl+Click on a sidebar entry the user
			// was already on would add a no-op step they had to back
			// through). The early return preserves the existing history
			// and index exactly.
			if (page === currentPage) return;

			let nextHistory = [
				...navHistory.current.slice(0, navIndex.current + 1),
				page,
			];
			let nextIndex = navIndex.current + 1;

			// PVT-fix-14: cap the history so a long-lived session
			// doesn't accumulate hundreds of entries (each one is
			// serialized into localStorage on every navigate). Drop
			// from the front; shift the index so the current pointer
			// still points at the same logical entry.
			if (nextHistory.length > MAX_NAV_HISTORY) {
				const overflow = nextHistory.length - MAX_NAV_HISTORY;
				nextHistory = nextHistory.slice(overflow);
				nextIndex = Math.max(0, nextIndex - overflow);
			}

			navHistory.current = nextHistory;
			navIndex.current = nextIndex;
			setCurrentPage(page);
			saveNavState(page, navHistory.current, navIndex.current);
		},
		[currentPage, saveNavState],
	);

	/**
	 * PVT-fix-12: replace the current history entry with `page` without
	 * pushing a new entry onto the stack. Mirrors `history.replaceState`
	 * in the browser API. Use this for route guards that should NOT
	 * appear in the back/forward history (e.g. the onboarding-completed
	 * guard bouncing a user from `onboarding` → `home` shouldn't leave
	 * a "home" entry sitting on top of the original "onboarding" entry,
	 * otherwise the user could press Back and land back in the wizard
	 * they just finished).
	 *
	 * If `page === currentPage`, this is a no-op (same rationale as
	 * `navigate`).
	 */
	const replace = useCallback(
		(page: Page) => {
			if (page === currentPage) return;
			navHistory.current[navIndex.current] = page;
			setCurrentPage(page);
			saveNavState(page, navHistory.current, navIndex.current);
		},
		[currentPage, saveNavState],
	);

	const goBack = useCallback(() => {
		if (navIndex.current > 0) {
			navIndex.current--;
			const page = navHistory.current[navIndex.current];
			setCurrentPage(page);
			saveNavState(page, navHistory.current, navIndex.current);
		}
	}, [saveNavState]);

	const goForward = useCallback(() => {
		if (navIndex.current < navHistory.current.length - 1) {
			navIndex.current++;
			const page = navHistory.current[navIndex.current];
			setCurrentPage(page);
			saveNavState(page, navHistory.current, navIndex.current);
		}
	}, [saveNavState]);

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

	return {
		currentPage,
		navigate,
		replace,
		goBack,
		goForward,
		canGoBack: navIndex.current > 0,
		canGoForward: navIndex.current < navHistory.current.length - 1,
	};
}
