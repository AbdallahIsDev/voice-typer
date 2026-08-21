import {
	AiBrain03Icon,
	Analytics01Icon,
	BookOpen02Icon,
	Cancel01Icon,
	File02Icon,
	HistoryIcon,
	Home04Icon,
	InformationCircleIcon,
	Mic02Icon,
	PaintBoardIcon,
	Settings03Icon,
	Shield01Icon,
	SlidersHorizontalIcon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@radix-ui/react-collapsible";
import * as Popover from "@radix-ui/react-popover";
// Collapsible is the default export (Root component); Trigger +
// Content are named exports (radix's per-primitive package layout —
// see accordion.tsx for the same pattern using the umbrella import
// `import { Accordion as AccordionPrimitive } from "radix-ui"`). We
// use the per-primitive package here so the Sidebar's import surface
// stays tight + explicit (only the primitives actually used are
// pulled in).
import {
	Fragment,
	memo,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { HotkeyTooltip } from "@/components/hotkey/HotkeyTooltip";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { Page } from "@/types/ipc";

interface NavItem {
	id: Page;
	icon: IconSvgElement;
	// Optional nested children — when present, this item renders as
	// a parent with a Collapsible submenu (see NavSubmenu). When
	// absent, the item is a leaf button (original behavior).
	children?: NavChild[];
}

interface NavChild {
	id: Page;
	// Optional explicit icon override; defaults to the parent's
	// icon language (slight visual variation per child).
	icon?: IconSvgElement;
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

//Settings submenu — the 4 child tabs (General / AI & Audio /
// Appearance / Privacy) are now nested INSIDE the Settings parent
// rather than rendered as a top-of-page SegmentedControl inside the
// Settings page. Clicking the Settings parent auto-expands the
// submenu + navigates to the default child (settingsGeneral).
// Leaving Settings entirely auto-collapses the submenu (see
// NavSubmenu deriveExpanded logic).
//
// The child icons are picked from the existing hugeicons core-free
// set — no new icon dependency added. Each child icon was chosen to
// match the section's purpose: General = horizontal sliders (tweak
// the basics), AI & Audio = brain (already used for the Models
// parent), Appearance = paint board, Privacy = shield (already used
// for the standalone Privacy page). Reusing icons already present in
// the icon set keeps the visual language coherent without introducing
// decorative-only glyphs.
const SETTINGS_CHILDREN: NavChild[] = [
	{ id: "settingsGeneral", icon: SlidersHorizontalIcon },
	{ id: "settingsAiAudio", icon: AiBrain03Icon },
	{ id: "settingsAppearance", icon: PaintBoardIcon },
	{ id: "settingsPrivacy", icon: Shield01Icon },
];

const SYSTEM_NAV_ITEMS: NavItem[] = [
	{ id: "settings", icon: Settings03Icon, children: SETTINGS_CHILDREN },
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
// vertical composite: arrow keys move across group boundaries). For
// items with children, the parent is in the flat list AND each child
// is appended after the parent — so ArrowDown enters the submenu
// when it's expanded, mirroring how screen readers traverse
// expandable menus. When the submenu is COLLAPSED, the children's
// buttons are not in the DOM (radix Collapsible.Content unmounts on
// close), so the roving-tabindex querySelectorAll('button[data-nav-item]')
// naturally skips them — no extra filter needed.
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
//
// The shortcut for "settings" (Ctrl+,) applies to the Settings
// parent — navigating to it triggers the redirect to
// "settingsGeneral" via useNavigation.navigate, so the user lands on
// a real Settings sub-page even when the shortcut fires while the
// submenu is collapsed.
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

// localStorage key for the user's manual expand/collapse preference
// on the Settings submenu. Auto-expand takes precedence when the
// current page is a Settings sub-page (see NavSubmenu deriveExpanded).
const SETTINGS_SUBMENU_LS_KEY = "vt_settings_submenu_expanded";

interface SidebarProps {
	currentPage: Page;
	onNavigate: (page: Page) => void;
	collapsed?: boolean;
}

// Helper: is `page` one of the four Settings sub-pages?
function isSettingsSubPage(page: Page): boolean {
	return (
		page === "settingsGeneral" ||
		page === "settingsAiAudio" ||
		page === "settingsAppearance" ||
		page === "settingsPrivacy"
	);
}

function SidebarInner({
	currentPage,
	onNavigate,
	collapsed = false,
}: SidebarProps) {
	//(roving tabindex): the nav is a vertical composite
	// widget. Only one item holds tabIndex=0 (the active page, or
	// the first item if the active page isn't in the nav); all
	// others hold tabIndex=-1. ArrowUp/ArrowDown/Home/End move focus
	// between items without leaving the nav.
	const navRef = useRef<HTMLElement>(null);

	const activeFlatIdx = ALL_NAV_ITEMS.findIndex((i) => i.id === currentPage);
	// Roving-tabindex fallback: when the active page is a Settings
	// child (which lives in a submenu, NOT at the top level), focus
	// the Settings parent so the user can ArrowDown into the
	// submenu's expanded children to reach the active leaf. Without
	// this fallback, the roving tabindex would jump to the first
	// nav item (home) on a Settings sub-page, breaking the "focus
	// follows active" UX.
	const rovingFallbackIdx = isSettingsSubPage(currentPage)
		? ALL_NAV_ITEMS.findIndex((i) => i.id === "settings")
		: -1;
	const rovingIdx =
		activeFlatIdx >= 0
			? activeFlatIdx
			: rovingFallbackIdx >= 0
				? rovingFallbackIdx
				: 0;

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
			{/* Navigation. ``min-h-0`` + ``overflow-y-auto`` so a short
			    window scrolls instead of clipping the bottom items. The
			    nav is the sidebar's only content — it fills the full
			    height now that the branding header and the ThemeSwitch
			    row have moved out of the sidebar. */}
			<div className="min-h-0 flex-1 overflow-y-auto p-2">
				<nav
					ref={navRef}
					aria-label={t("a11y.mainNavigation")}
					onKeyDown={handleNavKeyDown}
					className={cn("flex flex-col gap-px")}
				>
					{NAV_GROUPS.map((group, gIdx) => {
						const groupLabel = navGroupLabel(group.labelKey, group.fallback);
						return (
							<Fragment key={group.labelKey}>
								{gIdx > 0 && (
									<hr
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
										if (item.children && item.children.length > 0) {
											return (
												<NavSubmenu
													key={item.id}
													item={item}
													currentPage={currentPage}
													collapsed={collapsed}
													onNavigate={onNavigate}
													rovingIdx={
														ALL_NAV_ITEMS.findIndex((i) => i.id === item.id) ===
														rovingIdx
													}
												/>
											);
										}
										return (
											<NavLeaf
												key={item.id}
												item={item}
												currentPage={currentPage}
												collapsed={collapsed}
												onNavigate={onNavigate}
												tabIndex={
													ALL_NAV_ITEMS.findIndex((i) => i.id === item.id) ===
													rovingIdx
														? 0
														: -1
												}
											/>
										);
									})}
								</section>
							</Fragment>
						);
					})}
				</nav>
			</div>
		</aside>
	);
}

interface NavLeafProps {
	item: NavItem;
	currentPage: Page;
	collapsed: boolean;
	onNavigate: (page: Page) => void;
	tabIndex: number;
}

// A nav item with NO children — the original flat-button behavior.
// Extracted from the inline map for clarity now that NavSubmenu
// exists alongside it. The roving-tabindex contract is owned by the
// parent <nav> (single composite widget): the caller passes tabIndex
// 0 for the active leaf (or first-item fallback), -1 for the rest.
function NavLeaf({
	item,
	currentPage,
	collapsed,
	onNavigate,
	tabIndex,
}: NavLeafProps) {
	const isActive = currentPage === item.id;
	const handleNav = () => onNavigate(item.id);
	const shortcut = navShortcut(item.id);
	const navLabel = t(`nav.${item.id}`);
	const keyShortcut = NAV_KEYSHORTCUTS[item.id];
	const navButton = (
		<Button
			key={item.id}
			variant="ghost"
			data-nav-item="true"
			aria-keyshortcuts={keyShortcut}
			tabIndex={tabIndex}
			aria-current={isActive ? "page" : undefined}
			className={cn(
				"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
				"transition-all duration-200 ease-out",
				"border-s-2",
				collapsed ? "px-2" : "px-3",
				isActive
					? cn(
							"border-s-transparent bg-(--bg) hover:bg-(--bg)",
							"text-(--text-primary) font-medium",
						)
					: cn("border-s-transparent", "hover:bg-foreground/5"),
			)}
			onClick={handleNav}
		>
			<HugeiconsIcon
				icon={item.icon}
				strokeWidth={2}
				className={cn("h-4 w-4 shrink-0 transition-colors duration-200")}
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
	if (collapsed) {
		return (
			<HotkeyTooltip
				key={item.id}
				label={navLabel}
				keys={shortcut}
				side="right"
			>
				{navButton}
			</HotkeyTooltip>
		);
	}
	return <Fragment key={item.id}>{navButton}</Fragment>;
}

interface NavSubmenuProps {
	item: NavItem;
	currentPage: Page;
	collapsed: boolean;
	onNavigate: (page: Page) => void;
	// Whether the parent holds the roving tabindex=0 (active or
	// first-item fallback). Children are always tabIndex=-1 unless
	// they themselves are active (then they get 0 and the parent
	// gets -1).
	rovingIdx: boolean;
}

// A nav item WITH children — renders a Collapsible parent button +
// nested child buttons. When the sidebar is collapsed, replaces the
// inline collapsible with a Popover flyout (hovering/focusing the
// parent shows the 4 children as a flyout list).
//
// Expansion state derives from BOTH the current page (auto-expand
// when a Settings sub-page is active) AND the user's manual
// expand/collapse preference persisted to localStorage. The two
// signals are merged: if the user is on a Settings sub-page, the
// submenu is ALWAYS expanded (cannot be collapsed while active).
// When the user leaves Settings entirely, the submenu reverts to the
// last manual preference (default collapsed).
function NavSubmenu({
	item,
	currentPage,
	collapsed,
	onNavigate,
	rovingIdx,
}: NavSubmenuProps) {
	// Track the user's manual preference so it survives
	// navigation away from Settings and back. Initialized once
	// on mount; updated when the user clicks the chevron.
	const [manualExpanded, setManualExpanded] = useState<boolean>(() => {
		try {
			return localStorage.getItem(SETTINGS_SUBMENU_LS_KEY) === "true";
		} catch {
			return false;
		}
	});

	// Auto-expand takes precedence when the current page is one of
	// this submenu's children. The user CANNOT collapse the submenu
	// while a child is active — collapsing would hide the active
	// leaf, breaking "focus follows active" discoverability.
	const hasActiveChild =
		item.children?.some((c) => c.id === currentPage) ?? false;
	// The parent literal itself can be the active page in two cases:
	//   (a) tests that mount <Sidebar currentPage="settings" />
	//       directly (mirrors the pre-redesign state where "settings"
	//       was the only Settings-related Page literal);
	//   (b) a stale persisted `vt_nav_state` from an older build that
	//       resolves before useNavigation.navigate's redirect fires.
	// In both cases, treat the parent as "active" (carry aria-current
	// + the active styling) — production runtime never lands here
	// because useNavigation.navigate("settings") redirects to
	// "settingsGeneral" before render.
	const isParentActive = currentPage === item.id;
	const expanded = hasActiveChild || isParentActive || manualExpanded;

	// Persist manual preference when it changes (NOT when it's
	// overridden by hasActiveChild — that's a derived state, not a
	// preference).
	useEffect(() => {
		try {
			localStorage.setItem(SETTINGS_SUBMENU_LS_KEY, String(manualExpanded));
		} catch {
			// localStorage may be unavailable (SSR, sandboxed
			// renderer); non-fatal — the in-memory state is still
			// authoritative for the current session.
		}
	}, [manualExpanded]);

	const handleParentClick = useCallback(() => {
		// Clicking the Settings parent: if a child or the parent
		// itself is already active, toggle the manual-expanded
		// preference (so the user can collapse the visible
		// submenu to save space — the active child/parent remains
		// active, just the parent's row collapses). If neither is
		// active, navigate to the default child (settingsGeneral)
		// and expand the submenu.
		if (hasActiveChild || isParentActive) {
			setManualExpanded((v) => !v);
			return;
		}
		// Navigate to the default Settings child. The
		// useNavigation.navigate action handles the legacy
		// "settings" parent literal by redirecting it to
		// "settingsGeneral" (mirrors the onboarding-completed
		// guard at App.tsx:131-140), so we just call onNavigate
		// with the parent's id here — keeps the call site simple
		// + doesn't need to know about the redirect.
		onNavigate(item.id);
	}, [hasActiveChild, isParentActive, onNavigate, item.id]);

	const parentLabel = t(`nav.${item.id}`);
	const parentShortcut = navShortcut(item.id);
	const parentKeyShortcut = NAV_KEYSHORTCUTS[item.id];

	// The parent button is the roving-tabindex target when no
	// child is active (but the parent itself may be active, in
	// which case it still holds tabIndex=0). When a child IS
	// active, the child holds tabIndex=0 and the parent drops to -1.
	const parentTabIndex = rovingIdx && !hasActiveChild ? 0 : -1;

	// Collapsed sidebar: render a Popover flyout instead of an
	// inline Collapsible. The parent shows just the icon; hovering
	// or focusing it opens a flyout to the right containing the 4
	// child links. Clicking a child navigates + closes the flyout.
	if (collapsed) {
		return (
			<Popover.Root>
				<Popover.Trigger asChild>
					<Button
						variant="ghost"
						data-nav-item="true"
						aria-keyshortcuts={parentKeyShortcut}
						tabIndex={parentTabIndex}
						aria-expanded={
							hasActiveChild || isParentActive ? "true" : undefined
						}
						aria-haspopup="menu"
						aria-current={isParentActive ? "page" : undefined}
						className={cn(
							"w-full justify-center p-2 rounded-md",
							"transition-all duration-200 ease-out",
							"border-s-2 border-s-transparent",
							hasActiveChild || isParentActive
								? "text-(--text-primary) font-medium"
								: "text-(--text-muted) hover:bg-foreground/5 hover:text-(--text-primary)",
						)}
					>
						<HugeiconsIcon
							icon={item.icon}
							strokeWidth={2}
							className="h-4 w-4 shrink-0"
						/>
					</Button>
				</Popover.Trigger>
				<Popover.Portal>
					<Popover.Content
						side="right"
						align="center"
						sideOffset={8}
						className={cn(
							"z-50 min-w-44 rounded-md border border-border/10 bg-(--bg-subtle) p-1 shadow-lg",
							"data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
							"data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
						)}
					>
						<div
							className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-(--text-muted) opacity-70"
							aria-hidden
						>
							{parentLabel}
						</div>
						{item.children?.map((child) => {
							const childActive = currentPage === child.id;
							return (
								<Popover.Close asChild key={child.id}>
									<button
										type="button"
										data-nav-item="true"
										tabIndex={-1}
										aria-current={childActive ? "page" : undefined}
										onClick={() => onNavigate(child.id)}
										className={cn(
											"flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm",
											"transition-colors duration-200",
											childActive
												? "bg-(--bg) text-(--text-primary) font-medium"
												: "text-(--text-muted) hover:bg-foreground/5 hover:text-(--text-primary)",
										)}
									>
										<HugeiconsIcon
											icon={child.icon ?? item.icon}
											strokeWidth={2}
											className="h-4 w-4 shrink-0"
										/>
										<span className="truncate">{t(`nav.${child.id}`)}</span>
									</button>
								</Popover.Close>
							);
						})}
					</Popover.Content>
				</Popover.Portal>
			</Popover.Root>
		);
	}

	// Expanded sidebar: render a Collapsible parent button + nested
	// children. The parent shows icon + label + chevron; clicking
	// the parent navigates to the default child (or toggles
	// expanded if a child is already active). The chevron has its
	// own click handler that ONLY toggles expanded (does not
	// navigate) — gives the user explicit control over the
	// submenu's visibility.
	return (
		<Collapsible open={expanded} onOpenChange={setManualExpanded}>
			<div className="relative">
				<Button
					variant="ghost"
					data-nav-item="true"
					aria-keyshortcuts={parentKeyShortcut}
					tabIndex={parentTabIndex}
					aria-expanded={expanded ? "true" : "false"}
					aria-haspopup="menu"
					// aria-current is set on the PARENT only
					// when the parent literal itself is the
					// active page (e.g. tests / stale persisted
					// nav state). When a CHILD is active, the
					// child carries aria-current="page" + the
					// parent carries only aria-expanded="true"
					// (signaling "this section is open, look
					// inside for the active leaf").
					aria-current={isParentActive ? "page" : undefined}
					className={cn(
						"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
						"transition-all duration-200 ease-out",
						"border-s-2 border-s-transparent",
						"px-3",
						isParentActive
							? cn(
									// Parent literal is the active page —
									// apply the same active styling as a
									// leaf nav button (mirrors NavLeaf's
									// active branch).
									"bg-(--bg) hover:bg-(--bg)",
									"text-(--text-primary) font-medium",
								)
							: hasActiveChild
								? cn(
										"text-(--text-primary) font-medium",
										// When a child is active, the parent
										// does NOT take the active background
										// (--bg) — that would visually compete
										// with the child's active state. The
										// parent stays in its default container
										// background so the active child pops.
										"hover:bg-foreground/5",
									)
								: cn("hover:bg-foreground/5"),
					)}
					onClick={handleParentClick}
				>
					<HugeiconsIcon
						icon={item.icon}
						strokeWidth={2}
						className="h-4 w-4 shrink-0 transition-colors duration-200"
					/>
					<span
						className={cn(
							"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
							"opacity-100 filter-none",
						)}
					>
						{parentLabel}
					</span>
					{parentShortcut !== undefined && (
						<span
							aria-hidden="true"
							className="ms-auto flex items-center opacity-60"
						>
							<HotkeyChips keys={parentShortcut} />
						</span>
					)}
				</Button>
				{/* Chevron toggle — separate from the parent
                                    button so the user can collapse the submenu
                                    without triggering a navigation. Position
                                    absolute over the right edge of the parent
                                    button. */}
				<CollapsibleTrigger
					aria-label={
						expanded
							? t("a11y.collapseSubmenu", { label: parentLabel })
							: t("a11y.expandSubmenu", { label: parentLabel })
					}
					className={cn(
						"absolute inset-e-1 top-1/2 -translate-y-1/2",
						"flex h-6 w-6 items-center justify-center rounded",
						"text-(--text-muted) hover:bg-foreground/5 hover:text-(--text-primary)",
						"transition-transform duration-200",
						focusRing,
					)}
				>
					<HugeiconsIcon
						icon={Cancel01Icon}
						strokeWidth={2}
						className={cn(
							"h-3 w-3 transition-transform duration-200",
							expanded ? "rotate-90" : "rotate-0",
						)}
					/>
				</CollapsibleTrigger>
			</div>
			<CollapsibleContent
				className={cn(
					"data-[state=open]:animate-in data-[state=open]:slide-in-from-top-1 data-[state=open]:fade-in-0",
					"data-[state=closed]:animate-out data-[state=closed]:slide-out-to-top-1 data-[state=closed]:fade-out-0",
				)}
			>
				<div
					role="menu"
					className={cn(
						"ms-3 mt-0.5 flex flex-col gap-px border-s border-border/10 ps-2",
					)}
				>
					{item.children?.map((child) => {
						const childActive = currentPage === child.id;
						// The active child takes tabIndex=0
						// (roving target); others -1. When no
						// child is active, ALL children are
						// -1 (the parent holds 0).
						const childTabIndex = childActive ? 0 : -1;
						return (
							<Button
								key={child.id}
								variant="ghost"
								data-nav-item="true"
								tabIndex={childTabIndex}
								aria-current={childActive ? "page" : undefined}
								aria-level={2}
								className={cn(
									"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
									"transition-all duration-200 ease-out",
									"border-s-2 px-3 py-1.5",
									childActive
										? cn(
												"border-s-transparent bg-(--bg) hover:bg-(--bg)",
												"text-(--text-primary) font-medium",
											)
										: cn(
												"border-s-transparent",
												"text-(--text-muted) hover:bg-foreground/5 hover:text-(--text-primary)",
											),
								)}
								onClick={() => onNavigate(child.id)}
							>
								<HugeiconsIcon
									icon={child.icon ?? item.icon}
									strokeWidth={2}
									className="h-4 w-4 shrink-0"
								/>
								<span
									className={cn(
										"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
										"opacity-100 filter-none",
									)}
								>
									{t(`nav.${child.id}`)}
								</span>
							</Button>
						);
					})}
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

//wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed. All props
// (`currentPage`, `onNavigate`, `collapsed`) are primitives or stable
// `useCallback` refs from App.tsx (`navigate` from useNavigation) — so
// the default shallow-equal comparator (matching the TitleBar.tsx
// pattern) skips re-renders on unrelated App state changes (e.g.
// themeMode changes that only re-render the TitleBar, or recordingState
// transitions).
export const Sidebar = memo(SidebarInner);
