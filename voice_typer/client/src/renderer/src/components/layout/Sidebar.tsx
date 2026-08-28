import {
	AiBrain03Icon,
	Analytics01Icon,
	ArrowRight01Icon,
	BookOpen02Icon,
	File02Icon,
	HistoryIcon,
	Home04Icon,
	Mic02Icon,
	PaintBoardIcon,
	Settings03Icon,
	Shield01Icon,
	ShieldUserIcon,
	SlidersHorizontalIcon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { Collapsible, CollapsibleContent } from "@radix-ui/react-collapsible";
import * as Popover from "@radix-ui/react-popover";
// Collapsible is the default export (Root component); Content is a
// named export (radix's per-primitive package layout — see
// accordion.tsx for the same pattern using the umbrella import
// `import { Accordion as AccordionPrimitive } from "radix-ui"`). We
// use the per-primitive package here so the Sidebar's import surface
// stays tight + explicit (only the primitives actually used are
// pulled in).
import { memo, useEffect, useRef, useState } from "react";
import { HotkeyTooltip } from "@/components/hotkey/HotkeyTooltip";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
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

// 2-group nav hierarchy. Splitting the flat NAV_ITEMS list into
// semantically meaningful groups gives users a faster mental model of
// the app: the default page + content tools (header-less top group),
// and low-priority system/device/info destinations (System). Each
// group renders as its own
// <section aria-label=...> (no <hr> dividers between groups). The
// System group is PINNED TO THE BOTTOM of the sidebar via flex auto
// margin (see NavGroup.pinnedToBottom) so the importance hierarchy —
// frequent destinations on top, system/device/info at the bottom — is
// encoded by the layout itself, in both sidebar states.
//
// TWO groups, deliberately:
//   1. Top group — NO visible header (hideLabel): the default page set
//      speaks for itself. Day-to-day destinations (Home / History /
//      Analytics) first, then the content tools (Models / Templates /
//      Vocabulary).
//   2. System group (visible heading) — app + device configuration and
//      information: Settings, Microphone (input-device configuration
//      belongs beside app settings), About & Privacy.
const MAIN_NAV_ITEMS: NavItem[] = [
	{ id: "home", icon: Home04Icon },
	{ id: "history", icon: HistoryIcon },
	{ id: "analytics", icon: Analytics01Icon },
	{ id: "models", icon: AiBrain03Icon },
	{ id: "templates", icon: File02Icon },
	{ id: "vocabulary", icon: BookOpen02Icon },
];

// Settings submenu — the 4 child tabs (General / AI & Audio /
// Appearance / Privacy) are nested INSIDE the Settings parent rather
// than rendered as a top-of-page SegmentedControl inside the Settings
// page. The submenu's open state is SYNCHRONIZED WITH NAVIGATION: it
// is open exactly while a Settings sub-page is the current page, and
// it closes as soon as the user navigates anywhere else (see
// NavSubmenu). Clicking the parent navigates to the default child
// (settingsGeneral) via the useNavigation redirect.
//
// The child icons are picked from the existing hugeicons core-free
// set — no new icon dependency added. Each child icon matches its
// section's purpose: General = horizontal sliders (tweak the basics),
// AI & Audio = brain (already used for the Models parent), Appearance
// = paint board, Privacy = shield. Reusing icons already present in
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
	// Microphone — input-device configuration (selection, quality,
	// test). It is device setup rather than a day-to-day destination,
	// so it lives in the System cluster directly under Settings.
	{ id: "microphone", icon: Mic02Icon },
	// About & Privacy — ONE combined destination (the former About and
	// Privacy pages merged): product identity (what the app is,
	// version, platforms) plus the data-handling disclosure (how audio
	// and data are processed and stored). The shield-user glyph
	// communicates personal-data protection without any warning/error
	// semantics.
	{ id: "aboutAndPrivacy", icon: ShieldUserIcon },
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
	// When true, the group's visible heading label is NOT rendered
	// (the `<section aria-label=...>` is kept, so screen-reader nav
	// context is preserved). Used for the first/"Main" group, whose
	// heading is redundant above the default page set.
	hideLabel?: boolean;
	// When true, the group is pinned to the bottom of the sidebar via
	// `mt-auto` (flex auto margin) — the System/low-priority cluster
	// anchors to the rail's end edge in BOTH states without spacer
	// elements or fixed heights, so the hierarchy stays stable across
	// window sizes (on a short window the auto margin collapses to 0
	// and the nav simply scrolls).
	pinnedToBottom?: boolean;
}

const NAV_GROUPS: NavGroup[] = [
	{
		labelKey: "nav.group.main",
		fallback: "Main",
		items: MAIN_NAV_ITEMS,
		hideLabel: true,
	},
	{
		labelKey: "nav.group.system",
		fallback: "System",
		items: SYSTEM_NAV_ITEMS,
		pinnedToBottom: true,
	},
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
const ALL_NAV_ITEMS: NavItem[] = [...MAIN_NAV_ITEMS, ...SYSTEM_NAV_ITEMS];

// Per-page keyboard shortcuts surfaced ONLY for accessibility + the
// collapsed-sidebar tooltip: the expanded nav items render NO visible
// shortcut chips (clean, consistent with pages that don't display
// shortcuts), but the shortcuts remain registered and functional.
// `aria-keyshortcuts` exposes them to AT, and the collapsed icon-only
// items show them as Kbd chips via HotkeyTooltip. Uses formatHotkey()
// for platform-aware labels (e.g. "Ctrl+H" on Windows/Linux, "⌘H" on
// macOS) instead of hardcoded English. Pages without a shortcut
// return undefined (no chips rendered). The bindings come from the
// SHORTCUTS catalog (single source of truth) — same strings TitleBar
// and the Help overlay render.
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

// Shared label motion — the STATE classes of the sidebar's ONE text
// exit/enter model: opacity + inline-start X translate (RTL-mirrored)
// + explicit blur endpoints. X-axis only — never any Y component.
// The element owns its transition-property list (item labels include
// max-width; group-header containers animate max-height separately),
// so this helper deliberately declares NO transition classes.
function navLabelMotion(collapsed: boolean): string {
	return collapsed
		? "-translate-x-3 opacity-0 blur-[4px] pointer-events-none rtl:translate-x-3"
		: "translate-x-0 opacity-100 blur-[0px]";
}

// Shared label visibility transition for nav item text (leaf labels +
// the Settings parent label). ONE animation model for the whole
// sidebar: the label's layout box (max-width) collapses in sync with
// the aside's 200ms ease-out width transition while the text itself
// exits toward the inline-start icon column through the shared
// navLabelMotion state classes (translate + fade + blur). Icons are
// flex siblings BEFORE the span, so this animation can never shift
// the icon column.
//
// For this transition to actually RUN, the element carrying it must
// NOT be remounted when `collapsed` flips (CSS transitions only
// animate computed-style changes on PERSISTENT DOM nodes — a freshly
// mounted node jumps straight to its end state). That is why every
// nav button renders through the SAME wrapper element type in both
// states: leaves are always wrapped in HotkeyTooltip (content
// suppressed when expanded via its `disabled` prop) and the Settings
// parent always sits inside one Popover.Root (see NavSubmenu).
function navTextClasses(collapsed: boolean): string {
	return cn(
		"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
		"transition-[max-width,opacity,translate,filter] duration-200 ease-out",
		collapsed ? "max-w-0" : "max-w-40",
		navLabelMotion(collapsed),
	);
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
	// (roving tabindex): the nav is a vertical composite
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
				// Rail geometry pins the icon column: every top-level nav
				// button starts its icon at 16px from this edge (container
				// p-2 + button px-2; the buttons carry the Button base's
				// uniform 1px transparent border) in BOTH states. w-12
				// (48px) centers the 16px icon inside the collapsed rail
				// (16 + 16/2 = 24 = 48/2) without moving it — the icon
				// column never re-centers on collapse.
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
					className={cn(
						// min-h-full lets the pinned System group's auto margin
						// reach the rail's bottom edge when there is spare
						// height, while still growing (and scrolling) on short
						// windows.
						"flex min-h-full flex-col",
						// Deliberate vertical rhythm per state. Within a group,
						// items breathe with gap-1 (the section's own flex gap);
						// between groups the nav gap is the separator: wider
						// (gap-5) when the group headings are visible expanded,
						// tighter (gap-2) in the collapsed icon rail where the
						// (zero-height, still-mounted) headings contribute their
						// surrounding flex gaps — net cluster separation 16px
						// vs the 4px item rhythm, so clusters read as designed
						// groups instead of a vertically stretched sidebar.
						collapsed ? "gap-2" : "gap-5",
					)}
				>
					{NAV_GROUPS.map((group) => {
						const groupLabel = navGroupLabel(group.labelKey, group.fallback);
						return (
							<section
								key={group.labelKey}
								aria-label={groupLabel}
								className={cn(
									"flex flex-col gap-1",
									// Low-priority System cluster anchors to the
									// bottom edge through flex auto margin — layout
									// structure, not spacer dividers or fixed
									// heights.
									group.pinnedToBottom && "mt-auto",
								)}
							>
								{!group.hideLabel && (
									<div
										aria-hidden={collapsed || undefined}
										className={cn(
											"px-3.5",
											// Vertical SPACE collapse only. The label text itself
											// exits via the shared horizontal motion (inner span),
											// so the shrinking container never visibly half-clips
											// glyphs: the text has dissolved toward the icon column
											// before the collapse cuts into it.
											"overflow-hidden transition-[max-height] duration-200 ease-out",
											collapsed ? "max-h-0" : "max-h-4",
										)}
									>
										<span
											className={cn(
												// block: CSS transforms do not apply to inline elements.
												"block whitespace-nowrap text-xs font-semibold capitalize tracking-wider text-(--text-muted)",
												// The text fade runs slightly FASTER (150ms) than the
												// container's 200ms space collapse — deliberate exit
												// choreography so the label is gone before the
												// vertical clip could bite. The shared principles
												// allow per-layout timing.
												"block transition-[opacity,translate,filter] duration-150 ease-out",
												collapsed
													? navLabelMotion(true)
													: cn(navLabelMotion(false), "opacity-70"),
											)}
										>
											{groupLabel}
										</span>
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

// A nav item with NO children. The roving-tabindex contract is owned
// by the parent <nav> (single composite widget): the caller passes
// tabIndex 0 for the active leaf (or first-item fallback), -1 for the
// rest.
//
// The button is ALWAYS wrapped in the same HotkeyTooltip element — in
// the expanded state the tooltip CONTENT is suppressed via
// `disabled` — because swapping the wrapper type between states would
// remount the button and skip its label transition (CSS transitions
// do not run on freshly mounted nodes).
function NavLeaf({
	item,
	currentPage,
	collapsed,
	onNavigate,
	tabIndex,
}: NavLeafProps) {
	const isActive = currentPage === item.id;
	const navLabel = t(`nav.${item.id}`);
	return (
		<HotkeyTooltip
			label={navLabel}
			keys={navShortcut(item.id)}
			side="right"
			disabled={!collapsed}
		>
			<Button
				variant="ghost"
				data-nav-item="true"
				aria-keyshortcuts={NAV_KEYSHORTCUTS[item.id]}
				tabIndex={tabIndex}
				aria-current={isActive ? "page" : undefined}
				className={cn(
					"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
					// Clip the row so the animating label never paints outside
					// the button while the rail width transitions.
					"overflow-hidden",
					"transition-[background-color,border-color,color] duration-200 ease-out",
					// Single icon column: px-2 in BOTH states anchors the icon
					// at the same x-position (container p-2 + this padding =
					// 16px from the aside edge) whether expanded or collapsed
					// — the icon never shifts when the rail collapses.
					"px-2",
					isActive
						? cn(
								// Active page = the standard card treatment: the
								// app's card surface (--bg) + the shared card
								// border token at the same ~10% opacity every
								// card in the app uses. No custom border color.
								"border-border/5 bg-(--bg) hover:bg-(--bg)",
								"text-(--text-primary) font-medium",
							)
						: cn(
								"text-(--text-muted)",
								"hover:bg-foreground/5 hover:text-(--text-primary)",
							),
				)}
				onClick={() => onNavigate(item.id)}
			>
				<HugeiconsIcon
					icon={item.icon}
					strokeWidth={2}
					className={cn("h-4 w-4 shrink-0 transition-colors duration-200")}
				/>
				<span className={navTextClasses(collapsed)}>{navLabel}</span>
			</Button>
		</HotkeyTooltip>
	);
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

// A nav item WITH children — the Settings parent + its nested child
// buttons.
//
// ONE authoritative expansion state: a `submenuOpen` React state,
// INITIALIZED from the current page and kept IN SYNC with navigation
// by an effect. Navigation is the sync signal, with deterministic
// transitions:
//   - clicking the parent toggles the submenu directly (open ->
//     closed even on a Settings sub-page; closed -> open + navigate
//     to the default child);
//   - ENTERING the Settings section from anywhere (parent click,
//     Ctrl+, , tray/back-forward navigation) reveals it;
//   - LEAVING the Settings section closes it.
// No persisted preference, no timeouts — navigating back to Settings
// re-opens the submenu automatically.
//
// NO BRANCH SWAP between sidebar states: the SAME inline Collapsible
// renders expanded AND collapsed (`open={submenuOpen && !collapsed}`),
// so collapsing the sidebar ANIMATES the open submenu closed
// (collapsibleUp, 200ms) in step with the aside width transition
// instead of unmounting it at t=0; expanding animates it back open.
// Only the collapsed-rail flyout is additive
// ({collapsed && <Popover.Portal>…</Popover.Portal>}), and the chevron
// indicator renders UNCONDITIONALLY in both states, fading out with
// the rail rather than popping out of the DOM.
//
// The whole subtree still renders through ONE stable element
// structure in both sidebar states: a single Popover.Root wraps
// everything; the parent button sits in a Popover.Anchor (positional
// reference only — no aria leakage, never opens the popover by
// itself). Keeping the root type stable means the parent button never
// remounts on toggle, so its label participates in the shared
// collapse/expand text transition instead of snapping.
function NavSubmenu({
	item,
	currentPage,
	collapsed,
	onNavigate,
	rovingIdx,
}: NavSubmenuProps) {
	// The parent literal itself can be the active page in two cases:
	//   (a) tests that mount <Sidebar currentPage="settings" />
	//       directly (mirrors the pre-redesign state where "settings"
	//       was the only Settings-related Page literal);
	//   (b) a stale persisted `vt_nav_state` from an older build that
	//       resolves before useNavigation.navigate's redirect fires.
	// In both cases, treat the parent as "active" (carry aria-current
	// + the calm active styling) — production runtime never lands here
	// because useNavigation.navigate("settings") redirects to
	// "settingsGeneral" before render.
	const hasActiveChild =
		item.children?.some((c) => c.id === currentPage) ?? false;
	const isParentActive = currentPage === item.id;
	// ONE composite flag for "the current page belongs to the Settings
	// section" — drives BOTH the styling/aria-current signal AND the
	// submenu state machine (the arrow mirrors the toggle, not the
	// page).
	const inSettings = hasActiveChild || isParentActive;
	// ONE source of truth for the submenu. Deterministic transitions:
	//   - clicking the parent toggles it directly (open -> closed even on
	//     a Settings sub-page; closed -> open + navigate);
	//   - ENTERING the Settings section from anywhere (parent click,
	//     Ctrl+, , tray/back-forward navigation) reveals it;
	//   - LEAVING the Settings section closes it — navigation itself is
	//     the sync signal, no persisted preference, no timeouts.
	const [submenuOpen, setSubmenuOpen] = useState(inSettings);
	useEffect(() => {
		setSubmenuOpen(inSettings);
	}, [inSettings]);

	// Collapsed-rail flyout state (controlled so outside clicks and
	// Escape dismiss it through the same handler). Reset when the
	// sidebar expands — a flyout left "open" across a state toggle
	// would otherwise pop open instantly on the next collapse.
	const [flyoutOpen, setFlyoutOpen] = useState(false);
	useEffect(() => {
		if (!collapsed) setFlyoutOpen(false);
	}, [collapsed]);

	const parentLabel = t(`nav.${item.id}`);
	const parentKeyShortcut = NAV_KEYSHORTCUTS[item.id];

	// The parent button is the roving-tabindex target whenever no
	// child is ACTUALLY VISIBLE — a child being active but hidden
	// behind a closed submenu leaves the parent as the focusable
	// stand-in for the section (only `submenuOpen && hasActiveChild`
	// means the child holds tabIndex=0 and the parent drops to -1).
	// In the COLLAPSED rail the inline children are never visible (the
	// flyout renders its own buttons outside the nav's roving scope),
	// so the parent keeps the roving tab stop whenever the roving
	// index points here — even while a Settings sub-page is active —
	// otherwise every rail button would be tabIndex=-1 and Tab would
	// skip the entire sidebar.
	const parentTabIndex = collapsed
		? rovingIdx
			? 0
			: -1
		: rovingIdx && !(submenuOpen && hasActiveChild)
			? 0
			: -1;

	return (
		<Popover.Root open={flyoutOpen} onOpenChange={setFlyoutOpen}>
			<HotkeyTooltip
				label={parentLabel}
				keys={navShortcut(item.id)}
				side="right"
				disabled={!collapsed}
			>
				<Popover.Anchor asChild>
					<Button
						variant="ghost"
						data-nav-item="true"
						aria-keyshortcuts={parentKeyShortcut}
						tabIndex={parentTabIndex}
						// aria-expanded reflects what the button controls in
						// each state: the flyout when collapsed, the inline
						// submenu when expanded. aria-haspopup="dialog" is
						// carried only by the collapsed flyout trigger (the
						// expanded parent is an inline disclosure, not a
						// dialog opener).
						aria-expanded={
							(collapsed ? flyoutOpen : submenuOpen) ? "true" : "false"
						}
						aria-haspopup={collapsed ? "dialog" : undefined}
						// aria-current is set on the PARENT when the parent
						// literal itself is the active page (e.g. tests /
						// stale persisted nav state), OR when an active CHILD
						// is not actually rendered — hidden behind a closed
						// submenu OR in the collapsed rail (where the inline
						// children never mount and the flyout is shut). The
						// parent then carries the current-page signal because
						// the actual active leaf is not rendered. When a child
						// IS active AND visible (submenu open, sidebar
						// expanded), the child carries aria-current="page" and
						// the parent carries only aria-expanded="true"
						// (signaling "this section is open, look inside for
						// the active leaf").
						aria-current={
							isParentActive || (hasActiveChild && (!submenuOpen || collapsed))
								? "page"
								: undefined
						}
						className={cn(
							"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
							// Clip the row so the animating label never paints
							// outside the button while the rail width transitions.
							"overflow-hidden",
							"transition-[background-color,border-color,color] duration-200 ease-out",
							// Same anchored icon column as the leaves (px-2 in
							// both states).
							"px-2",
							inSettings
								? cn(
										// Parent groups (Settings) stay visually CALM
										// when their submenu is open: only the stronger
										// text/icon foreground marks the active section
										// — never the leaf card background/border (that
										// would compete with the active child and
										// introduce a third active style).
										"text-(--text-primary) font-medium",
										"hover:bg-foreground/5",
									)
								: cn(
										"text-(--text-muted)",
										"hover:bg-foreground/5 hover:text-(--text-primary)",
									),
						)}
						onClick={() => {
							if (collapsed) {
								setFlyoutOpen((open) => !open);
							} else if (submenuOpen) {
								// Deterministic close — direct state set, no
								// navigation side effect.
								setSubmenuOpen(false);
							} else {
								// Open + land on the default child; the sync
								// effect keeps it open.
								setSubmenuOpen(true);
								onNavigate(item.id);
							}
						}}
					>
						<HugeiconsIcon
							icon={item.icon}
							strokeWidth={2}
							className="h-4 w-4 shrink-0 transition-colors duration-200"
						/>
						<span className={navTextClasses(collapsed)}>{parentLabel}</span>
						{/* Expand/collapse indicator at the row's FAR END edge
					    (ms-auto pins it to the button's end padding).
					    aria-hidden — the button's aria-expanded already
					    exposes the state to assistive tech. ONE persistent
					    glyph rendered in BOTH sidebar states: direction is
					    animation (rotation only, never an icon swap) and the
					    WRAPPER fades with the rail instead of being unmounted
					    at t=0. Open rotates 90° to point down; closed points
					    forward (RTL-mirrored via the shared index.css rule,
					    which is why `nav-directional-icon` only applies while
					    closed — a mirrored + rotated glyph would point back
					    up). Only `rotate` transitions (not `transform`) so
					    the RTL mirror flip stays instant while the rotation
					    animates. */}
						<span
							aria-hidden="true"
							className={cn(
								"ms-auto flex size-6 shrink-0 items-center justify-center",
								// Fades out with the rail instead of popping out of the DOM;
								// in the collapsed rail it is fully transparent and inert
								// (and clipped by the button's overflow-hidden), so the
								// icon-only state stays clean.
								"transition-opacity duration-200 ease-out",
								collapsed ? "pointer-events-none opacity-0" : "opacity-100",
							)}
						>
							<HugeiconsIcon
								icon={ArrowRight01Icon}
								strokeWidth={2}
								className={cn(
									"h-4 w-4 transition-[rotate] duration-200 ease-out",
									submenuOpen ? "rotate-90" : "nav-directional-icon",
								)}
							/>
						</span>
					</Button>
				</Popover.Anchor>
			</HotkeyTooltip>
			{collapsed && (
				<Popover.Portal>
					<Popover.Content
						side="right"
						align="center"
						sideOffset={8}
						className={cn(
							"z-50 min-w-44 rounded-md border border-border/5 bg-(--bg-subtle) p-1 shadow-lg",
							"data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
							"data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
						)}
					>
						<div
							className="px-2 py-1 text-[0.6875rem] font-semibold uppercase tracking-wider text-(--text-muted) opacity-70"
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
										// Natural tab order (no tabIndex override):
										// the flyout renders in a portal OUTSIDE the
										// nav's roving-tabindex scope, so Tab is the
										// only keyboard path into these buttons —
										// hard-coding -1 would make the Settings
										// sub-pages unreachable by keyboard from the
										// collapsed rail.
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
			)}
			{/* Inline submenu — the SAME element renders in BOTH sidebar
		    states (open only while the rail is expanded), so collapsing
		    the sidebar ANIMATES an open submenu closed (collapsibleUp,
		    200ms) in sync with the aside width transition instead of
		    swapping branches and unmounting it at t=0. */}
			<Collapsible open={submenuOpen && !collapsed}>
				<CollapsibleContent
					className={cn(
						// Smooth reveal/hide in BOTH directions: the content
						// animates height 0 ↔ --radix-collapsible-content-height
						// (set by Radix) + opacity, and Radix's Presence keeps
						// the content mounted until the closing animation
						// finishes — no layout jump, no instant removal. The
						// global prefers-reduced-motion block reduces both
						// animations to 0.01ms (instant, still correct).
						"overflow-hidden",
						"data-[state=open]:animate-[collapsibleDown_200ms_ease-out]",
						"data-[state=closed]:animate-[collapsibleUp_200ms_ease-out]",
					)}
				>
					{/* Plain grouping container — deliberately NOT role="menu".
						    These are navigation links inside a nav landmark (the
						    submenu is part of the page's navigation, not a
						    transient action menu): menu semantics would demand
						    menuitem roles + arrow-key menu behavior and break the
						    nav's roving-tabindex contract. */}
					<div
						className={cn(
							"ms-3 mt-0.5 flex flex-col gap-px border-s border-border/5 ps-2",
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
										"transition-[background-color,border-color,color] duration-200 ease-out",
										"px-3 py-1.5",
										childActive
											? cn(
													// Same standard card treatment as the
													// top-level active leaf.
													"border-border/5 bg-(--bg) hover:bg-(--bg)",
													"text-(--text-primary) font-medium",
												)
											: cn(
													"text-(--text-muted)",
													"hover:bg-foreground/5 hover:text-(--text-primary)",
												),
									)}
									onClick={() => onNavigate(child.id)}
								>
									<HugeiconsIcon
										icon={child.icon ?? item.icon}
										strokeWidth={2}
										className="h-4 w-4 shrink-0"
									/>
									<span className="overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal">
										{t(`nav.${child.id}`)}
									</span>
								</Button>
							);
						})}
					</div>
				</CollapsibleContent>
			</Collapsible>
		</Popover.Root>
	);
}

// wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed. All props
// (`currentPage`, `onNavigate`, `collapsed`) are primitives or stable
// `useCallback` refs from App.tsx (`navigate` from useNavigation) — so
// the default shallow-equal comparator (matching the TitleBar.tsx
// pattern) skips re-renders on unrelated App state changes (e.g.
// themeMode changes that only re-render the TitleBar, or recordingState
// transitions).
export const Sidebar = memo(SidebarInner);
