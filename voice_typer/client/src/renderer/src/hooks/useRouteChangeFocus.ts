import { useEffect, useRef } from "react";

import type { Page } from "@/types/ipc";

export function useRouteChangeFocus(currentPage: Page): void {
	const skipFirstRun = useRef(true);
	// The effect must re-run on every route change to move focus to the
	// main landmark, `currentPage` is the intentional reactive trigger
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
