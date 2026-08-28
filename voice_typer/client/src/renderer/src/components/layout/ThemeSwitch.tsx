import { Moon02Icon, Sun03Icon } from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback } from "react";
import { SunMoonIcon } from "@/components/common/SunMoonIcon";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";

const THEME_CYCLE: {
	mode: VoiceTyperConfig["theme_mode"];
	icon: IconSvgElement;
	labelKey: string;
}[] = [
	{ mode: "light", icon: Sun03Icon, labelKey: "theme.light" },
	{ mode: "dark", icon: Moon02Icon, labelKey: "theme.dark" },
	// System = "follow the OS default" — a sun/moon combo icon (not the
	// old TV glyph) so the mode reads as "auto light/dark" at a glance.
	{ mode: "system", icon: SunMoonIcon, labelKey: "theme.system" },
];

// Sentinel fallback used when both `THEME_CYCLE.find(...)` and
// `THEME_CYCLE[0]` are `undefined` (theoretically impossible —
// `THEME_CYCLE` is a module-level literal with 3 entries — but TS
// still widens both reads under `noUncheckedIndexedAccess`). Typed
// as a non-optional element so the lookup expressions above can
// chain `?? THEME_CYCLE_FALLBACK` without a non-null assertion.
const THEME_CYCLE_FALLBACK: (typeof THEME_CYCLE)[number] = {
	mode: "system",
	icon: SunMoonIcon,
	labelKey: "theme.system",
};

/** Get the next mode in the cycle. Light → Dark → System → Light */
function nextMode(
	current: VoiceTyperConfig["theme_mode"],
): VoiceTyperConfig["theme_mode"] {
	const idx = THEME_CYCLE.findIndex((item) => item.mode === current);
	const next = THEME_CYCLE[(idx + 1) % THEME_CYCLE.length];
	// noUncheckedIndexedAccess: `next` is `T | undefined`; THEME_CYCLE
	// is non-empty, so the modulo index is always in bounds — guard
	// keeps the typed return happy without a non-null assertion.
	return next?.mode ?? THEME_CYCLE[0]?.mode ?? current;
}

interface ThemeSwitchProps {
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
	/**
	 * Optional className merged over the icon-only button's base
	 * styling. Lets the host (e.g. the TitleBar) tune size, rounded
	 * corners, hover wash, and text color to its own button language
	 * without forking a second theme control.
	 */
	className?: string;
}
export function ThemeSwitch({
	themeMode,
	onThemeChange,
	className,
}: ThemeSwitchProps) {
	const current =
		THEME_CYCLE.find((item) => item.mode === themeMode) ??
		THEME_CYCLE[0] ??
		THEME_CYCLE_FALLBACK;
	const label = t(current.labelKey);

	// Include the NEXT mode in the aria-label so screen-reader users
	// know what clicking will do, not just what the current state is.
	// Previously the aria-label was ``"Current theme: Dark. Click to
	// switch."`` — ambiguous about the result of the click.  Now:
	// ``"Current theme: Dark. Click to switch to System."`` etc.
	const nextLabel = t(
		(
			THEME_CYCLE.find((item) => item.mode === nextMode(themeMode)) ??
			THEME_CYCLE[0] ??
			THEME_CYCLE_FALLBACK
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
				// Icon-only by design: the visible UI carries the current
				// theme's icon and NO text label. The current→next state
				// is exposed via aria-label (SR) + title (hover tooltip).
				// Host className (e.g. the TitleBar's button language)
				// overrides size/rounding/hover/text-color via twMerge.
				"inline-flex items-center justify-center",
				"transition-colors duration-150",
				// Theme-aware hover (replaces the physical
				// bg-black/5 dark:bg-white/10 pairing so custom + dark
				// themes get a consistent hover wash).
				"hover:bg-foreground/5",
				// Visible focus indicator so keyboard users can see which
				// theme button is focused. Use the shared focusRing
				// constant (ring-3 / ring-ring/30) for parity with the
				// design-system Button.
				focusRing,
				"h-7 w-7 rounded-md",
				className,
			)}
			// The title attribute mirrors the aria-label so sighted mouse
			// users hovering the switch see the same "current → next"
			// information screen-reader users hear.
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
				icon={current.icon ?? SunMoonIcon}
				strokeWidth={2}
				className="h-4 w-4 shrink-0"
			/>
		</button>
	);
}
