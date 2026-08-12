/**
 * useOnboardingComplete — handles the wizard-finished transition.
 *
 * Extracted from App.tsx (EO-28, Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the
 * original inline ``handleOnboardingComplete`` callback:
 *
 *   1. Navigate to the home page.
 *   2. Fetch the fresh config and re-apply the theme (the user may
 *      have picked a theme during onboarding). Non-fatal on failure —
 *      the theme is re-applied on the next ``config_changed`` event or
 *      the next launch.
 *
 * Returns a stable memoized callback suitable for passing to the
 * Onboarding page's ``onComplete`` prop.
 */
import { useCallback } from "react";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc/enums";

/** Shape of the ``call`` function from usePython(). */
type CallFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

/** Shape of the theme reload from useTheme(). */
type ReloadThemeFn = () => Promise<void>;

interface UseOnboardingCompleteArgs {
	/** Navigate to a page (used to land on home after the wizard). */
	navigate: (page: Page) => void;
	/** The ``call`` from usePython() — used to re-fetch config. */
	call: CallFn;
	/** Re-apply the saved theme from config (from useTheme()). */
	reloadThemeFromConfig: ReloadThemeFn;
}

/**
 * Build the onboarding-complete handler.
 *
 * @returns A stable callback: navigate home, then refresh the theme
 *   from the saved config.
 */
export function useOnboardingComplete({
	navigate,
	call,
	reloadThemeFromConfig,
}: UseOnboardingCompleteArgs): () => Promise<void> {
	return useCallback(async () => {
		navigate("home");
		try {
			const cfg = await call<VoiceTyperConfig>("get_config");
			if (cfg?.theme_mode) {
				await reloadThemeFromConfig();
			}
		} catch (e) {
			// non-fatal — the user already finished onboarding;
			// theme will be re-applied on the next config_changed
			// event or the next app launch.
			console.warn(
				"[renderer:useOnboardingComplete] handleOnboardingComplete get_config/reload failed:",
				e,
			);
		}
	}, [navigate, call, reloadThemeFromConfig]);
}
