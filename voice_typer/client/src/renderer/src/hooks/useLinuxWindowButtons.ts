import { useMemo } from "react";
import type { ResolvedLinuxWindowButtons } from "@/lib/utils/windowButtons";
import { resolveLinuxWindowButtons } from "@/lib/utils/windowButtons";
import { useAppStore } from "@/stores/appStore";

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
