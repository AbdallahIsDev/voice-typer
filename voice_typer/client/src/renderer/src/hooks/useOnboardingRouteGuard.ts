import { useEffect } from "react";

import type { NavigateOptions } from "@/hooks/useNavigation";
import { useAppStore } from "@/stores/appStore";
import type { Page } from "@/types/ipc";

/** Dependencies wired by the App entry component. */
export interface UseOnboardingRouteGuardOptions {
	/** The live route, from ``useNavigation``. */
	currentPage: Page;
	/** Shared-store history-swap action (from ``useNavigation``) —
	 * ``replace`` mirrors ``history.replaceState``: it swaps the
	 * current history entry without pushing a new one. */
	replace: (page: Page, opts?: NavigateOptions) => void;
}

export function useOnboardingRouteGuard({
	currentPage,
	replace,
}: UseOnboardingRouteGuardOptions): void {
	const onboardingCompleted = useAppStore(
		(s) => s.config?.onboarding_completed === true,
	);
	useEffect(() => {
		if (currentPage === "onboarding" && onboardingCompleted) {
			// Use `replace` instead of `navigate` so the
			// "onboarding" entry is swapped for "home" in the history
			// stack. With `navigate`, the stack would become
			// [..., "onboarding", "home"] and pressing Back would return
			// the user to the wizard they just completed, confusing.
			replace("home");
		}
	}, [currentPage, onboardingCompleted, replace]);
}
