/**
 * useLinuxWindowButtons — resolves the effective Linux window-button
 * layout from the shared config snapshot.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the other extracted use* hooks.
 * Behaviour is byte-identical to the original inline selectors + memo.
 *
 * The layout is resolved once per config/system change and passed to
 * the (memoized) TitleBar as a single stable prop. No-op on
 * Windows/macOS (TitleBar ignores the prop there). The two source
 * fields (the user's ``linux_window_buttons`` setting + the sidecar's
 * read-only ``linux_window_buttons_system`` snapshot) are read via
 * FIELD-level selectors so unrelated config writes don't re-resolve
 * the layout.
 */

import { useMemo } from "react";
import type { ResolvedLinuxWindowButtons } from "@/lib/utils/windowButtons";
import { resolveLinuxWindowButtons } from "@/lib/utils/windowButtons";
import { useAppStore } from "@/stores/appStore";

/**
 * Resolve the Linux window-button layout from the current config.
 * Call once at the top level of the App component.
 */
export function useLinuxWindowButtons(): ResolvedLinuxWindowButtons {
	const linuxWindowButtonsConfig = useAppStore(
		(s) => s.config?.linux_window_buttons,
	);
	const linuxWindowButtonsSystem = useAppStore(
		(s) => s.config?.linux_window_buttons_system,
	);
	return useMemo(
		() =>
			resolveLinuxWindowButtons(
				linuxWindowButtonsConfig,
				linuxWindowButtonsSystem,
			),
		[linuxWindowButtonsConfig, linuxWindowButtonsSystem],
	);
}
