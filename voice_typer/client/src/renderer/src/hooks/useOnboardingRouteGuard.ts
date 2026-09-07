/**
 * useOnboardingRouteGuard — protects the onboarding route from users
 * who already completed the wizard.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the ``use*Event`` / ``use*Toast``
 * hooks. Behaviour is byte-identical to the original inline effect:
 * when the current page is ``"onboarding"`` but the shared config says
 * ``onboarding_completed === true``, the user is bounced to ``"home"``
 * via ``replace`` (not ``navigate``) so the "onboarding" history entry
 * is swapped for "home" instead of being stacked under it — pressing
 * Back must NOT return the user to the wizard they just completed.
 *
 * ``onboarding_completed`` is read via a FIELD-level selector (not the
 * whole ``config`` object) so a settings change to ANY other config
 * field (theme_mode, hotkey, audio preset, etc.) doesn't re-render the
 * guard host and re-fire this effect. ``mergeConfig`` always allocates
 * a new top-level config object reference, so a single-field selector
 * is the only way to avoid re-render storms on every keystroke in
 * Settings.
 */

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

/**
 * Redirect a completed user away from the onboarding wizard. Call once
 * at the top level of the App component; the guard lives for the
 * component's lifetime.
 */
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
			// the user to the wizard they just completed — confusing.
			replace("home");
		}
	}, [currentPage, onboardingCompleted, replace]);
}
