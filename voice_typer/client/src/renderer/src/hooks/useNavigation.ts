import { useCallback, useEffect, useRef, useState } from "react";
import type { Page } from "@/types/ipc";

// NEW-UX-041: persist the current page + nav history to localStorage
// so the user returns to where they left off after closing/reopening
// the app.  Previously the app always started at 'home' and the
// user's navigation history was lost.
const STORAGE_KEY_NAV = "vt_nav_state";

interface NavState {
	page: Page;
	history: Page[];
	index: number;
}

function loadNavState(): NavState {
	try {
		const raw = localStorage.getItem(STORAGE_KEY_NAV);
		if (raw) {
			const parsed = JSON.parse(raw) as {
				page?: Page;
				history?: Page[];
				index?: number;
			};
			if (
				parsed.page &&
				Array.isArray(parsed.history) &&
				typeof parsed.index === "number"
			) {
				return {
					page: parsed.page,
					history: parsed.history,
					index: parsed.index,
				};
			}
		}
	} catch {}
	return { page: "home", history: ["home"], index: 0 };
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
	const initialNav = loadNavState();
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
			} catch {}
		},
		[],
	);

	const navigate = useCallback(
		(page: Page) => {
			navHistory.current = [
				...navHistory.current.slice(0, navIndex.current + 1),
				page,
			];
			navIndex.current++;
			setCurrentPage(page);
			saveNavState(page, navHistory.current, navIndex.current);
		},
		[saveNavState],
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
		goBack,
		goForward,
		canGoBack: navIndex.current > 0,
		canGoForward: navIndex.current < navHistory.current.length - 1,
	};
}
