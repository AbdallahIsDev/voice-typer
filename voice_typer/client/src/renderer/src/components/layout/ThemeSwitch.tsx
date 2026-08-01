import {
	ModernTvIcon,
	Moon02Icon,
	Sun01Icon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback } from "react";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";

const THEME_CYCLE: {
	mode: VoiceTyperConfig["theme_mode"];
	icon: IconSvgElement;
	labelKey: string;
}[] = [
	{ mode: "light", icon: Sun01Icon, labelKey: "theme.light" },
	{ mode: "dark", icon: Moon02Icon, labelKey: "theme.dark" },
	{ mode: "system", icon: ModernTvIcon, labelKey: "theme.system" },
];

/** Get the next mode in the cycle. Light → Dark → System → Light */
function nextMode(
	current: VoiceTyperConfig["theme_mode"],
): VoiceTyperConfig["theme_mode"] {
	const idx = THEME_CYCLE.findIndex((item) => item.mode === current);
	return THEME_CYCLE[(idx + 1) % THEME_CYCLE.length].mode;
}

interface ThemeSwitchProps {
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
	collapsed?: boolean;
}
export function ThemeSwitch({
	themeMode,
	onThemeChange,
	collapsed = false,
}: ThemeSwitchProps) {
	const current =
		THEME_CYCLE.find((item) => item.mode === themeMode) ?? THEME_CYCLE[0];
	const label = t(current.labelKey);

	// Include the NEXT mode in the aria-label so screen-reader users
	// so screen-reader users know what clicking will do, not just
	// what the current state is.  Previously the aria-label was
	// ``"Current theme: Dark. Click to switch."`` — ambiguous about
	// the result of the click.  Now: ``"Current theme: Dark. Click
	// to switch to System."`` etc.
	const nextLabel = t(
		(
			THEME_CYCLE.find((item) => item.mode === nextMode(themeMode)) ??
			THEME_CYCLE[0]
		).labelKey,
	);

	const handleClick = useCallback(() => {
		onThemeChange(nextMode(themeMode));
	}, [themeMode, onThemeChange]);

	return (
		<button
			type="button"
			onClick={handleClick}
			className={cn(
				"inline-flex items-center justify-center gap-2 rounded-md transition-[padding] duration-200 ease-out",
				// Theme-aware hover (replaces the physical
				// bg-black/5 dark:bg-white/10 pairing so custom + dark
				// themes get a consistent hover wash).
				"hover:bg-foreground/5",
				// Visible focus indicator so keyboard users can see which
				// theme-switch button is focused. Use the shared focusRing
				// constant (ring-3 / ring-ring/30) for parity with the design-
				// system Button instead of the thinner ring-2 / ring-ring/30.
				focusRing,
				collapsed ? "h-7 w-7 justify-center gap-0" : "h-7 px-2.5 gap-2",
			)}
			// The title attribute mirrors the aria-label so sighted mouse
			// users hovering the switch see the same "current → next"
			// information screen-reader users hear. Previously the title
			// only showed the current mode, leaving sighted users with
			// less information than SR users — three clicks to cycle
			// from System to Light was non-obvious because the tooltip
			// didn't preview the next state.
			title={t("theme.switchAriaLabel", {
				mode: label,
				next: nextLabel,
			})}
			aria-label={t("theme.switchAriaLabel", {
				mode: label,
				next: nextLabel,
			})}
		>
			<HugeiconsIcon
				icon={current.icon}
				strokeWidth={2}
				className="h-4 w-4 shrink-0"
			/>
			<span
				className={cn(
					"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
					"transition-[max-width,opacity,filter] duration-200 ease-out",
					collapsed
						? "max-w-0 opacity-0 filter-[blur(4px)]"
						: "max-w-16 opacity-100",
				)}
			>
				{label}
			</span>
		</button>
	);
}
