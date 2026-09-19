import { useEffect, useRef, useState } from "react";

import { useMediaQuery } from "@/hooks/useMediaQuery";

/** State + setter for the App shell's sidebar chrome. */
export interface UseSidebarAutoCollapseResult {
	/** Whether the sidebar is currently collapsed (rail mode). */
	sidebarCollapsed: boolean;
	/** Raw setter, also the manual expand/collapse entry point. */
	setSidebarCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
}

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
