import {
	AiBrain03Icon,
	Analytics01Icon,
	BookOpen02Icon,
	File02Icon,
	HistoryIcon,
	Home04Icon,
	InformationCircleIcon,
	Mic02Icon,
	Settings03Icon,
	Shield01Icon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { Fragment, memo, useRef } from "react";
import { APP_NAME } from "@/branding";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { HotkeyTooltip } from "@/components/hotkey/HotkeyTooltip";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { Logo } from "@/components/layout/Logo";
import { ThemeSwitch } from "@/components/layout/ThemeSwitch";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

interface NavItem {
	id: Page;
	icon: IconSvgElement;
}

//3-group nav hierarchy (Main / Power features / System).
// Splitting the flat NAV_ITEMS list into semantically meaningful
// groups gives users a faster mental model of the app: day-to-day
// usage (Main), advanced/power features (Power), and system
// configuration (System). Each group is rendered as its own
// <section aria-label=...> with an <hr> divider between groups.
const MAIN_NAV_ITEMS: NavItem[] = [
	{ id: "home", icon: Home04Icon },
	{ id: "history", icon: HistoryIcon },
	{ id: "analytics", icon: Analytics01Icon },
];

const POWER_NAV_ITEMS: NavItem[] = [
	{ id: "templates", icon: File02Icon },
	{ id: "vocabulary", icon: BookOpen02Icon },
	{ id: "models", icon: AiBrain03Icon },
	{ id: "microphone", icon: Mic02Icon },
];

const SYSTEM_NAV_ITEMS: NavItem[] = [
	{ id: "settings", icon: Settings03Icon },
	// About — product identity (what the app is, version, platforms).
	{ id: "about", icon: InformationCircleIcon },
	// Privacy — how audio and data are handled (disclosure, not controls).
	{ id: "privacy", icon: Shield01Icon },
];

interface NavGroup {
	// i18n key (falls back to `fallback` literal when not yet translated).
	labelKey: string;
	// English literal used when `labelKey` is missing from the active
	// locale AND from English. This keeps the UI readable in English
	// until the i18n translations catch up — and gives
	// `screen.getByText("Main")` a stable string to assert on.
	fallback: string;
	items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
	{ labelKey: "nav.group.main", fallback: "Main", items: MAIN_NAV_ITEMS },
	{
		labelKey: "nav.group.power",
		fallback: "Power features",
		items: POWER_NAV_ITEMS,
	},
	{ labelKey: "nav.group.system", fallback: "System", items: SYSTEM_NAV_ITEMS },
];

// Flattened list used for roving tabindex (the nav is a single
// vertical composite: arrow keys move across group boundaries).
const ALL_NAV_ITEMS: NavItem[] = [
	...MAIN_NAV_ITEMS,
	...POWER_NAV_ITEMS,
	...SYSTEM_NAV_ITEMS,
];

//, : per-page keyboard shortcuts surfaced in the nav
// item's tooltip (aria-keyshortcuts + Kbd chips via HotkeyTooltip).
// Uses formatHotkey() for platform-aware labels (e.g. "Ctrl+H" on
// Windows/Linux, "⌘H" on macOS) instead of hardcoded English. Pages
// without a shortcut return undefined (no chips rendered). The
// bindings come from the SHORTCUTS catalog (single source of truth)
// — same strings TitleBar and the Help overlay render.
const NAV_KEYSHORTCUTS: Partial<Record<Page, string>> = {
	home: SHORTCUTS.goHome.ariaKeyshortcuts,
	settings: SHORTCUTS.openSettings.ariaKeyshortcuts,
};

function navShortcut(page: Page): string | undefined {
	switch (page) {
		case "home":
			return formatHotkey(SHORTCUTS.goHome.pynput ?? SHORTCUTS.goHome.keys);
		case "settings":
			return formatHotkey(
				SHORTCUTS.openSettings.pynput ?? SHORTCUTS.openSettings.keys,
			);
	}
	return undefined;
}

/**
 * Resolve a group label via `t()` with a fallback to the English
 * literal. `t()` returns the raw key when neither the current locale
 * nor English has the key — we detect that case and fall back so the
 * UI shows a readable label and tests have a stable string to assert
 * on.
 */
function navGroupLabel(labelKey: string, fallback: string): string {
	const translated = t(labelKey);
	return translated === labelKey ? fallback : translated;
}

interface SidebarProps {
	currentPage: Page;
	onNavigate: (page: Page) => void;
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
	collapsed?: boolean;
}

function SidebarInner({
	currentPage,
	onNavigate,
	themeMode,
	onThemeChange,
	collapsed = false,
}: SidebarProps) {
	//(roving tabindex): the nav is a vertical composite
	// widget. Only one item holds tabIndex=0 (the active page, or
	// the first item if the active page isn't in the nav); all
	// others hold tabIndex=-1. ArrowUp/ArrowDown/Home/End move focus
	// between items without leaving the nav.
	const navRef = useRef<HTMLElement>(null);

	const activeFlatIdx = ALL_NAV_ITEMS.findIndex((i) => i.id === currentPage);
	const rovingIdx = activeFlatIdx >= 0 ? activeFlatIdx : 0;

	const handleNavKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
		const nav = navRef.current;
		if (!nav) return;
		// Only buttons marked as nav items participate in roving
		// (defensive: the nav currently contains only nav-item
		// buttons, but the marker keeps the selector tight if a
		// future group header ever becomes a button).
		const buttons = Array.from(
			nav.querySelectorAll<HTMLButtonElement>("button[data-nav-item='true']"),
		);
		if (buttons.length === 0) return;
		const currentIdx = buttons.indexOf(
			document.activeElement as HTMLButtonElement,
		);
		let nextIdx = currentIdx;
		switch (e.key) {
			case "ArrowDown":
				e.preventDefault();
				nextIdx = currentIdx < 0 ? 0 : (currentIdx + 1) % buttons.length;
				break;
			case "ArrowUp":
				e.preventDefault();
				nextIdx =
					currentIdx < 0
						? buttons.length - 1
						: (currentIdx - 1 + buttons.length) % buttons.length;
				break;
			case "Home":
				e.preventDefault();
				nextIdx = 0;
				break;
			case "End":
				e.preventDefault();
				nextIdx = buttons.length - 1;
				break;
			default:
				return;
		}
		if (nextIdx >= 0 && nextIdx < buttons.length) {
			// noUncheckedIndexedAccess: buttons[nextIdx] is
			// `HTMLButtonElement | undefined`; the bounds check proves
			// it exists, but TS still widens the read. Explicit guard.
			const target = buttons[nextIdx];
			if (target !== undefined) target.focus();
		}
	};

	return (
		<aside
			className={cn(
				"flex shrink-0 flex-col",
				"overflow-hidden",
				"transition-[width] duration-200 ease-out",
				collapsed ? "w-12" : "w-55",
			)}
		>
			{/* Logo + Title */}
			<div
				className={cn(
					"flex shrink-0 items-center gap-2.5",
					"transition-[padding] duration-200 ease-out",
					collapsed ? "px-3 py-4" : "px-5 py-4",
				)}
			>
				{/** : when the sidebar is collapsed the visible
				 * <span>{APP_NAME}</span> is hidden (max-w-0), so the
				 * Logo's SVG is the only on-screen label. Wrapping it
				 * in a <button aria-label={APP_NAME}> gives AT users a
				 * single, focusable, named affordance. The Logo is
				 * rendered with `decorative` so the inner SVG is
				 * aria-hidden and the button's aria-label is the
				 * single source of truth (no double announcement).
				 * Expanded mode keeps the original self-labeled Logo.
				 */}
				{collapsed ? (
					<button
						type="button"
						aria-label={APP_NAME}
						title={APP_NAME}
						// Clicking the collapsed logo navigates home — gives the
						// focusable affordance a real action (matching the
						// common "click logo → go home" convention). Without
						// this, the button was focusable but did nothing on
						// Enter/Space, leaving AT users confused about its
						// purpose.
						onClick={() => onNavigate("home")}
						className={cn(
							"flex items-center justify-center p-0",
							"bg-transparent border-0 outline-none",
							// Use the shared focusRing (ring-3 / ring-ring) so the logo button
							// focus indicator matches the design-system Button (not ring-2).
							focusRing,
							"cursor-pointer",
						)}
					>
						<Logo size={24} decorative className="shrink-0" />
					</button>
				) : (
					<Logo size={24} className="shrink-0" />
				)}
				<span
					className={cn(
						"overflow-hidden whitespace-nowrap text-base font-medium tracking-normal text-(--text-primary)",
						"transition-all duration-200 ease-out",
						collapsed
							? "max-w-0 opacity-0 filter-[blur(4px)]"
							: "max-w-32 opacity-100 filter-none",
					)}
				>
					{APP_NAME}
				</span>
			</div>

			{/* Navigation. ``min-h-0`` + ``overflow-y-auto`` so a short
			    window (10 nav items + 3 group headers + theme switch)
			    scrolls instead of clipping the bottom items — the
			    ThemeSwitch row below stays pinned. */}
			<div className="min-h-0 flex-1 overflow-y-auto p-2">
				<nav
					ref={navRef}
					aria-label={t("a11y.mainNavigation")}
					//(roving tabindex): the nav is a vertical
					// composite widget.
					onKeyDown={handleNavKeyDown}
					className={cn("flex flex-col gap-px")}
				>
					{NAV_GROUPS.map((group, gIdx) => {
						const groupLabel = navGroupLabel(group.labelKey, group.fallback);
						return (
							<Fragment key={group.labelKey}>
								{gIdx > 0 && (
									<hr
										//visible divider between nav groups.
										// `aria-hidden` because the group <section
										// aria-label> already conveys the boundary to
										// ATs — a decorative hr shouldn't be announced.
										aria-hidden
										className={cn(
											"my-1 border-0 border-t border-border/10",
											collapsed ? "mx-2" : "mx-3",
										)}
									/>
								)}
								<section aria-label={groupLabel}>
									{!collapsed && (
										<div
											className={cn(
												"px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wider",
												"text-(--text-muted) opacity-70",
											)}
										>
											{groupLabel}
										</div>
									)}
									{group.items.map((item) => {
										const isActive = currentPage === item.id;
										const flatIdx = ALL_NAV_ITEMS.findIndex(
											(i) => i.id === item.id,
										);
										//roving tabindex — only the active
										// item (or first item as fallback) is in the
										// tab order; arrow keys move between items.
										const tabIndex = flatIdx === rovingIdx ? 0 : -1;
										const handleNav = () => onNavigate(item.id);
										//Always show a tooltip (regardless
										// of collapsed state) so hover/focus can read the
										// nav label, with the shortcut as Kbd chips when one
										// exists (HotkeyTooltip). The accessible name stays
										// the label text — aria-keyshortcuts carries the
										// shortcut for AT users.
										const shortcut = navShortcut(item.id);
										const navLabel = t(`nav.${item.id}`);
										const keyShortcut = NAV_KEYSHORTCUTS[item.id];
										// Expanded: the label is already visible, so the
										// tooltip is redundant — the shortcut (if any) is
										// rendered INLINE at the far right of the row
										// instead. aria-hidden: the accessible name stays
										// the label text (aria-keyshortcuts carries the
										// shortcut for AT users). Collapsed: keep the
										// right-side tooltip (label + shortcut chips) — the
										// icon alone isn't self-explanatory.
										const navButton = (
											<Button
												key={item.id}
												variant="ghost"
												data-nav-item="true"
												//aria-keyshortcuts is undefined
												// for items without a shortcut (omits the
												// attribute entirely).
												aria-keyshortcuts={keyShortcut}
												tabIndex={tabIndex}
												//aria-current="page" tells
												// screen readers which nav item represents
												// the current page.
												aria-current={isActive ? "page" : undefined}
												className={cn(
													"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
													"transition-all duration-200 ease-out",
													//task-6: a 2px inline-start border is
													// always present and transparent (both
													// active and inactive) so activating an
													// item doesn't cause a layout shift. No
													// accent bar is drawn on the active item
													// (the old before:bg-accent dash was
													// removed — see UX report).
													"border-s-2",
													collapsed ? "px-2" : "px-3",
													isActive
														? cn(
																// Active item blends with the page
																// content area: same background as the
																// page window (--bg), no accent tint and
																// no accent icon colour.
																"border-s-transparent bg-(--bg) hover:bg-(--bg)",
																"text-(--text-primary) font-medium",
															)
														: cn(
																"border-s-transparent",
																// task-9: theme-aware hover
																// (replaces physical black/white
																// pairing so custom + dark themes
																// get a consistent wash).
																"hover:bg-foreground/5",
															),
												)}
												onClick={handleNav}
											>
												<HugeiconsIcon
													icon={item.icon}
													strokeWidth={2}
													// Icon inherits the button's text colour in
													// every state — no accent tint when active,
													// so the active icon looks like any other
													// nav icon.
													className={cn(
														"h-4 w-4 shrink-0 transition-colors duration-200",
													)}
												/>
												<span
													className={cn(
														"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
														"transition-all duration-200 ease-out",
														collapsed
															? "max-w-0 opacity-0 filter-[blur(4px)]"
															: "max-w-40 opacity-100 filter-none",
													)}
												>
													{t(`nav.${item.id}`)}
												</span>
												{/* Inline shortcut hint (expanded only) — real text
													    (Kbd chips), right-aligned on the row. */}
												{!collapsed && shortcut !== undefined && (
													<span
														aria-hidden="true"
														className="ms-auto flex items-center opacity-60"
													>
														<HotkeyChips keys={shortcut} />
													</span>
												)}
											</Button>
										);
										return collapsed ? (
											<HotkeyTooltip
												key={item.id}
												label={navLabel}
												keys={shortcut}
												side="right"
											>
												{navButton}
											</HotkeyTooltip>
										) : (
											<Fragment key={item.id}>{navButton}</Fragment>
										);
									})}
								</section>
							</Fragment>
						);
					})}
				</nav>
			</div>

			{/* Theme Toggle */}
			<div className="flex justify-start items-center p-2">
				<ThemeSwitch
					themeMode={themeMode}
					onThemeChange={onThemeChange}
					collapsed={collapsed}
				/>
			</div>
		</aside>
	);
}

//wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed. All props
// (`currentPage`, `onNavigate`, `themeMode`, `onThemeChange`,
// `collapsed`) are primitives or stable `useCallback` refs from
// App.tsx — `navigate` (useNavigation) and `handleThemeChange`
// (useTheme) are both `useCallback`-wrapped — so the default
// shallow-equal comparator (matching the TitleBar.tsx:324 pattern)
// skips re-renders on unrelated App state changes (e.g. sidebar
// collapse toggles that don't affect Sidebar's own props, or
// recordingState transitions).
export const Sidebar = memo(SidebarInner);
