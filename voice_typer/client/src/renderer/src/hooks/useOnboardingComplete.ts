import type { PythonCall } from "@/hooks/usePython";
import type { LausuConfig } from "@/types/config";
import type { Page } from "@/types/ipc/enums";
import { useCallback } from "react";

/** Shape of the ``call`` function from usePython(). */

/** Shape of the theme reload from useTheme(). */
type ReloadThemeFn = () => Promise<void>;

interface UseOnboardingCompleteArgs {
	navigate: (page: Page) => void;
	call: PythonCall;
	/** Re-apply the saved theme from config (from useTheme()). */
	reloadThemeFromConfig: ReloadThemeFn;
}

export function useOnboardingComplete({
	navigate,
	call,
	reloadThemeFromConfig,
}: UseOnboardingCompleteArgs): () => Promise<void> {
	return useCallback(async () => {
		navigate("home");
		try {
			const cfg = await call<LausuConfig>("get_config");
			if (cfg?.theme_mode) {
				await reloadThemeFromConfig();
			}
		} catch (e) {
			// non-fatal, the user already finished onboarding;
			// theme will be re-applied on the next config_changed
			// event or the next app launch.
			console.warn(
				"[renderer:useOnboardingComplete] handleOnboardingComplete get_config/reload failed:",
				e,
			);
		}
	}, [navigate, call, reloadThemeFromConfig]);
}
