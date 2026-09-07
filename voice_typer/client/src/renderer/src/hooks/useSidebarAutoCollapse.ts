/**
 * useSidebarAutoCollapse — owns the sidebar collapse state and the
 * narrow-viewport auto-collapse rule.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the other extracted use* hooks.
 * Behaviour is byte-identical to the original inline state + effect.
 *
 * The sidebar auto-collapses when the window narrows below the
 * ``640px`` breakpoint. Only the wide→narrow TRANSITION (and the
 * initial narrow mount) forces a collapse — once collapsed, the user's
 * manual expand (Ctrl+B or the TitleBar toggle) is respected until the
 * next wide→narrow transition. Narrow→wide transitions do NOT
 * auto-expand (the user may have intentionally collapsed the sidebar
 * on a wide window).
 *
 * The returned ``setSidebarCollapsed`` is the same state setter the
 * App shell passes to ``useGlobalKeyboardShortcuts`` (the Ctrl+B
 * toggle) and the memoized TitleBar toggle callback.
 */

import { useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/hooks/useMediaQuery";

/** State + setter for the App shell's sidebar chrome. */
export interface UseSidebarAutoCollapseResult {
	/** Whether the sidebar is currently collapsed (rail mode). */
	sidebarCollapsed: boolean;
	/** Raw setter — also the manual expand/collapse entry point. */
	setSidebarCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
}

/**
 * Own the sidebar collapse state + the narrow-viewport auto-collapse
 * effect. Call once at the top level of the App component.
 */
export function useSidebarAutoCollapse(): UseSidebarAutoCollapseResult {
	const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
	const isNarrowViewport = useMediaQuery("(max-width: 640px)");
	const prevNarrowRef = useRef<boolean | null>(null);
	useEffect(() => {
		const prev = prevNarrowRef.current;
		// `prev !== true` covers BOTH the initial mount (prev === null)
		// and the wide→narrow transition (prev === false). On the
		// narrow→wide transition and on re-renders while narrow, prev
		// === true and we no-op so the user's manual toggle wins.
		if (isNarrowViewport && prev !== true) {
			setSidebarCollapsed(true);
		}
		prevNarrowRef.current = isNarrowViewport;
	}, [isNarrowViewport]);

	return { sidebarCollapsed, setSidebarCollapsed };
}
