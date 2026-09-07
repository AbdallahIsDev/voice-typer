/**
 * useRouteChangeFocus — moves keyboard focus to the ``<main>`` landmark
 * on every route change (a11y / focus management).
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the other extracted use* hooks.
 * Behaviour is byte-identical to the original inline effect.
 *
 * Screen-reader + keyboard users aren't stranded on the previously
 * focused nav item after a route transition: focus lands on
 * ``<main id="main-content">`` (the skip link + ``tabIndex={-1}``
 * plumbing lives in the App shell). ``skipFirstRun`` suppresses the
 * focus call on the initial mount — the user hasn't navigated yet, so
 * stealing focus from whatever they were doing would be rude (e.g. if
 * they opened the app and immediately focused the URL bar or a
 * bookmark).
 */

import { useEffect, useRef } from "react";

import type { Page } from "@/types/ipc";

/**
 * Focus the main content landmark after each route change. Call once
 * at the top level of the App component.
 *
 * @param currentPage The live route, from ``useNavigation``.
 */
export function useRouteChangeFocus(currentPage: Page): void {
	const skipFirstRun = useRef(true);
	// The effect must re-run on every route change to move focus to the
	// main landmark — `currentPage` is the intentional reactive trigger
	// and is deliberately NOT read in the body.
	// biome-ignore lint/correctness/useExhaustiveDependencies: currentPage is the reactive trigger, not a body value
	useEffect(() => {
		if (skipFirstRun.current) {
			skipFirstRun.current = false;
			return;
		}
		document.getElementById("main-content")?.focus();
	}, [currentPage]);
}
