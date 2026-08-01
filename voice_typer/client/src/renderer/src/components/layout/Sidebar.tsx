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
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { Fragment, useRef } from "react";
import { APP_NAME } from "@/branding";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { Logo } from "@/components/layout/Logo";
import { ThemeSwitch } from "@/components/layout/ThemeSwitch";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
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
	//About/Diagnostics page with version, config info, privacy, help.
	{ id: "about", icon: InformationCircleIcon },
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
// item's `title` tooltip. Uses formatHotkey() for platform-aware labels
// (e.g. "Ctrl+H" on Windows/Linux, "⌘H" on macOS) instead of hardcoded
// English. Pages without a shortcut return undefined (no suffix appended).
const NAV_KEYSHORTCUTS: Partial<Record<Page, string>> = {
	home: "Control+h",
	settings: "Control+,",
};

function navShortcut(page: Page): string | undefined {
	switch (page) {
		case "home":
			return formatHotkey("<ctrl>+<h>");
		case "settings":
			return formatHotkey("<ctrl>+<,>");
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

export function Sidebar({
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
			buttons[nextIdx].focus();
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
							"focus-visible:ring-2 focus-visible:ring-ring",
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

			{/* Navigation */}
			<div className="flex-1 p-2">
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
											"my-1 border-0 border-t border-border",
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
										//Always set a `title` (regardless
										// of collapsed state) so keyboard users hovering
										// focus can read the nav label, and include the
										// shortcut when one exists.
										const shortcut = navShortcut(item.id);
										const navLabel = t(`nav.${item.id}`);
										const navTitle = shortcut
											? `${navLabel} (${shortcut})`
											: navLabel;
										const keyShortcut = NAV_KEYSHORTCUTS[item.id];
										return (
											<Button
												key={item.id}
												variant="ghost"
												data-nav-item="true"
												title={navTitle}
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
													//task-6: a 2px left border is
													// always present (transparent when
													// inactive, accent when active) so
													// activating an item doesn't cause a
													// layout shift.
													"border-s-2",
													collapsed ? "px-2" : "px-3",
													isActive
														? cn(
																// task-5: soft accent background
																// (replaces the old solid --bg)
																// for a less heavy active state.
																"border-s-transparent bg-(--accent-soft) hover:bg-(--accent-soft)",
																"text-(--text-primary) font-medium",
																//use the logical
																// `start-0` (not physical `left-0`)
																// so the accent bar flips to the
																// right edge in RTL locales.
																"relative before:absolute before:inset-s-0 before:top-1/2 before:-translate-y-1/2 before:h-5 before:w-0.5 before:rounded-full before:bg-accent",
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
													className="h-4 w-4 shrink-0"
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
											</Button>
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
