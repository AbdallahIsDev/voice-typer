/**
 * NavSubmenu behavior tests — covers the nested Settings sidebar
 * submenu (expansion synchronized with navigation). Asserts:
 *   - Settings parent renders + 4 children render when expanded
 *   - Active child carries aria-current="page"
 *   - Settings parent carries aria-expanded="true" when expanded
 *   - Clicking a child calls onNavigate with the child's Page literal
 *   - Parent-click toggling is deterministic against the navigation
 *     sync: open+navigate / close-in-place / re-open / close
 *   - Collapse-while-open coherence: the submenu state survives the
 *     collapsed phase (expanding restores it without navigation)
 *   - Collapsed sidebar: Popover flyout shows the 4 children
 *   - ONE persistent arrow glyph (ArrowRight01Icon) that rotates —
 *     never swaps icons; rendered in BOTH sidebar states, faded-but-
 *     mounted in the collapsed rail; wrapper pinned to the row edge
 *     via ms-auto
 *   - Closing the submenu over an active child moves aria-current to
 *     the parent while the active leaf is hidden
 *   - CollapsibleContent animates reveal AND hide inside overflow-hidden
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: (props: { icon?: { name?: string }; className?: string }) => (
		<span
			data-testid="hugeicon"
			data-name={props.icon?.name}
			className={props.className}
		/>
	),
}));

// Canonical shared icon mock — a hand-rolled stub list here drifted out
// of sync with Sidebar.tsx's imports and crashed this suite at module
// load ("No 'ShieldUserIcon' export is defined on the mock").
vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

vi.mock("@/branding", () => ({ APP_NAME: "TestApp" }));

vi.mock("@/components/hotkey/HotkeyChips", () => ({
	HotkeyChips: () => <span data-testid="hotkey-chips" />,
}));

vi.mock("@/components/hotkey/HotkeyTooltip", () => ({
	HotkeyTooltip: ({ children }: { children: React.ReactNode }) => (
		<>{children}</>
	),
}));

vi.mock("@/components/hotkey/hotkey-utils", () => ({
	formatHotkey: (k: string) => k,
}));

vi.mock("@/components/hotkey/shortcuts", () => ({
	SHORTCUTS: {
		goHome: { ariaKeyshortcuts: "Control+h", pynput: "ctrl+h", keys: "Ctrl+h" },
		openSettings: {
			ariaKeyshortcuts: "Control+,",
			pynput: "ctrl+,",
			keys: "Ctrl+,",
		},
	},
}));

vi.mock("@/components/ui/button", () => ({
	Button: ({
		children,
		onClick,
		...rest
	}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
		children: React.ReactNode;
	}) => (
		<button type="button" onClick={onClick} {...rest}>
			{children}
		</button>
	),
}));

const wrap = (ui: React.ReactElement) => (
	<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>
);

const baseProps = {
	onNavigate: vi.fn(),
};

afterEach(() => {
	cleanup();
});

describe("NavSubmenu — nested Settings sidebar submenu (navigation-synchronized expansion)", () => {
	it("renders the 4 Settings children when the parent is expanded", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		// Settings parent auto-expands when a Settings sub-page is active.
		// The 4 children's labels are nav.settingsGeneral, nav.settingsAiAudio,
		// nav.settingsAppearance, nav.settingsPrivacy (mocked t() returns the
		// raw key).
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();
		expect(screen.getByText("nav.settingsAiAudio")).toBeTruthy();
		expect(screen.getByText("nav.settingsAppearance")).toBeTruthy();
		expect(screen.getByText("nav.settingsPrivacy")).toBeTruthy();
	});

	it("marks the active child with aria-current=page", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsPrivacy"
					collapsed={false}
				/>,
			),
		);
		const activeChild = document.querySelector('button[aria-current="page"]');
		expect(activeChild).toBeTruthy();
		expect(activeChild?.textContent).toContain("nav.settingsPrivacy");
	});

	it("marks the Settings parent with aria-expanded=true when expanded", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		const parent = Array.from(
			document.querySelectorAll('button[aria-expanded="true"]'),
		).find((b) => b.textContent?.includes("nav.settings"));
		expect(parent).toBeTruthy();
	});

	it("collapses (hides children) when currentPage leaves Settings, and re-opens when a Settings sub-page becomes active again", () => {
		const { rerender } = render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();

		// Navigate away from Settings.
		rerender(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={false} />),
		);

		// The 4 children should no longer be in the DOM (radix Collapsible
		// unmounts CollapsibleContent when closed).
		expect(screen.queryByText("nav.settingsGeneral")).toBeNull();
		expect(screen.queryByText("nav.settingsPrivacy")).toBeNull();

		// Navigate back into Settings: the submenu re-opens automatically
		// (expansion is synchronized with navigation — no persisted
		// preference to fall out of sync).
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();
		expect(screen.getByText("nav.settingsPrivacy")).toBeTruthy();
	});

	it("clicking a child calls onNavigate with the child's Page literal", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		fireEvent.click(screen.getByText("nav.settingsPrivacy"));
		expect(baseProps.onNavigate).toHaveBeenCalledWith("settingsPrivacy");
	});

	it("clicking the Settings parent when no child is active calls onNavigate(settings) [redirected by useNavigation to settingsGeneral]", () => {
		render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={false} />),
		);
		// Settings parent label is nav.settings.
		fireEvent.click(screen.getByText("nav.settings"));
		expect(baseProps.onNavigate).toHaveBeenCalledWith("settings");
	});

	it("parent-click toggling is deterministic against the navigation sync (open+navigate, close-in-place, re-open, close)", () => {
		const onNavigate = vi.fn();
		const { rerender } = render(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="home"
					collapsed={false}
				/>,
			),
		);
		const parent = () => findSettingsParent() as HTMLElement;

		// Closed -> open AND navigate (lands on the section's default child).
		fireEvent.click(parent());
		expect(onNavigate).toHaveBeenCalledTimes(1);
		expect(onNavigate).toHaveBeenCalledWith("settings");
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();

		// Land on the Settings sub-page: the sync effect keeps it open.
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();

		// Open -> close IN PLACE on a Settings sub-page: no navigation side effect.
		fireEvent.click(parent());
		expect(onNavigate).toHaveBeenCalledTimes(1);
		expect(screen.queryByText("nav.settingsGeneral")).toBeNull();

		// Closed -> open again (+ navigate).
		fireEvent.click(parent());
		expect(onNavigate).toHaveBeenCalledTimes(2);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();

		// Open -> close again: the cycle stays deterministic.
		fireEvent.click(parent());
		expect(onNavigate).toHaveBeenCalledTimes(2);
		expect(screen.queryByText("nav.settingsGeneral")).toBeNull();
	});

	it("collapse-while-open keeps the submenu state coherent (no branch swap): expanding restores the open submenu without navigation", () => {
		const onNavigate = vi.fn();
		const { rerender } = render(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();

		// Collapse the rail WHILE the submenu is open. The SAME inline
		// Collapsible renders in both states with
		// open={submenuOpen && !collapsed} — collapsing drives it closed
		// in step with the width transition instead of swapping branches.
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="settingsGeneral"
					collapsed={true}
				/>,
			),
		);
		// NO BRANCH SWAP: the SAME inline CollapsibleContent element stays
		// MOUNTED in both sidebar states — the closed state parks it as
		// data-state="closed" + hidden instead of removing it from the
		// DOM (jsdom runs no CSS animations, so the closing transition
		// completes instantly and the child buttons are gone).
		const content = document.querySelector<HTMLElement>(
			"[class*='collapsibleDown']",
		);
		expect(content).toBeTruthy();
		expect(content?.getAttribute("data-state")).toBe("closed");
		expect(content?.hasAttribute("hidden")).toBe(true);
		expect(screen.queryByText("nav.settingsGeneral")).toBeNull();

		// Expand again WITHOUT navigating: the submenu state survived the
		// collapsed phase, so the children come straight back.
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(screen.getByText("nav.settingsGeneral")).toBeTruthy();
		expect(onNavigate).not.toHaveBeenCalled();
	});

	it("collapsed sidebar: Popover flyout shows the 4 children when parent trigger is clicked", () => {
		render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={true} />),
		);
		// The Settings parent is rendered as a Popover trigger (icon
		// only). Locate it by its icon — the nav keeps plain navigation
		// semantics, so there is no menu-role/haspopup hook to key on.
		const navButtons = Array.from(
			document.querySelectorAll<HTMLButtonElement>(
				"button[data-nav-item='true']",
			),
		);
		const settingsTrigger = navButtons.find((b) =>
			b.querySelector('[data-name="Settings03Icon"]'),
		);
		expect(settingsTrigger).toBeTruthy();
		// Click the trigger to open the Popover flyout.
		fire.click(settingsTrigger as HTMLElement);
		// After the flyout opens, the 4 children render inside PopoverContent.
		// Use findAllByText since there may be multiple matches across the DOM.
		// (Popover.Portal renders into document.body, outside the sidebar
		// aside — so we search the whole document.)
		const generalButtons = document.querySelectorAll("button");
		// At least one button should now have the child label as text content.
		const found = Array.from(generalButtons).some((b) =>
			b.textContent?.includes("nav.settingsGeneral"),
		);
		expect(found).toBe(true);
	});
});

describe("NavSubmenu — disclosure semantics, arrow affordance, reveal animation", () => {
	beforeEach(() => {
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("expanded: renders NO role='menu' and NO aria-haspopup anywhere in the nav (inline disclosure, not a menu)", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		// The submenu children live INSIDE the nav landmark — they are
		// navigation links, not a transient action menu. Menu semantics
		// (role="menu" container + aria-haspopup="menu" triggers) would
		// demand menuitem roles + arrow-key menu behavior that conflicts
		// with the roving-tabindex composite pattern.
		expect(document.querySelector('[role="menu"]')).toBeNull();
		expect(document.querySelector("button[aria-haspopup]")).toBeNull();
	});

	it("closing the submenu over an active child moves aria-current to the parent (active leaf hidden behind the closed submenu)", () => {
		const onNavigate = vi.fn();
		render(
			wrap(
				<Sidebar
					{...baseProps}
					onNavigate={onNavigate}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		const parent = findSettingsParent() as HTMLElement;
		// Open over an active child: the CHILD carries aria-current="page"
		// and the parent only signals expansion.
		expect(parent.getAttribute("aria-expanded")).toBe("true");
		expect(parent.getAttribute("aria-current")).toBeNull();

		fireEvent.click(parent);

		// Submenu closed → the active leaf is no longer rendered, so the
		// PARENT carries the current-page signal. The close is in-place:
		// no navigation fired, no rerender needed.
		expect(onNavigate).not.toHaveBeenCalled();
		expect(parent.getAttribute("aria-current")).toBe("page");
		expect(parent.getAttribute("aria-expanded")).toBe("false");
		expect(screen.queryByText("nav.settingsGeneral")).toBeNull();
		// The roving tab stop migrates with the aria-current signal: the
		// hidden active child can't hold it, so the parent becomes the
		// focusable stand-in for the section.
		expect(parent.tabIndex).toBe(0);
	});

	it("roving tabindex follows the visible active leaf: child holds it while the submenu is open, parent reclaims it when closed", () => {
		const { rerender } = render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		const parent = findSettingsParent() as HTMLElement;
		// Open over the active child: the CHILD is the roving target.
		expect(parent.tabIndex).toBe(-1);
		const visibleChild = screen
			.getByText("nav.settingsGeneral")
			.closest("button") as HTMLElement;
		expect(visibleChild.tabIndex).toBe(0);

		// Collapse the submenu: the parent reclaims the tab stop.
		fireEvent.click(parent);
		expect(parent.tabIndex).toBe(0);

		// Re-open: the child holds it again.
		fireEvent.click(parent);
		expect(parent.tabIndex).toBe(-1);
		expect(
			(screen.getByText("nav.settingsGeneral").closest("button") as HTMLElement)
				.tabIndex,
		).toBe(0);

		// Collapsed rail: the parent keeps the stop even over an active
		// child (the inline children never mount there).
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={true}
				/>,
			),
		);
		expect((findCollapsedSettingsTrigger() as HTMLElement).tabIndex).toBe(0);
	});

	it("collapsed: the Settings flyout trigger carries aria-haspopup='dialog' with the persistent chevron faded-but-mounted", () => {
		render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={true} />),
		);
		const trigger = findCollapsedSettingsTrigger();
		expect(trigger?.getAttribute("aria-haspopup")).toBe("dialog");
		// The chevron renders UNCONDITIONALLY in both sidebar states —
		// in the collapsed rail it is faded-but-mounted inside its
		// aria-hidden wrapper: fully transparent + inert (and clipped by
		// the button's overflow-hidden), so the icon-only rail shows no
		// visual artifact.
		const chevron = trigger?.querySelector('[data-name="ArrowRight01Icon"]');
		expect(chevron).toBeTruthy();
		const wrapper = chevron?.parentElement;
		expect(wrapper?.getAttribute("aria-hidden")).toBe("true");
		expect(wrapper?.className).toContain("opacity-0");
		expect(wrapper?.className).toContain("pointer-events-none");
		// The chevron FADES with the rail (never pops out of the DOM).
		// Tailwind `transition-opacity` is the standard utility (Tailwind
		// v4 emits the same transition-property as the arbitrary
		// `transition-[opacity]` it replaced).
		expect(wrapper?.className).toContain("transition-opacity");
	});

	it("ONE persistent arrow glyph: ArrowRight01Icon in both states — rotated 90° when open, nav-directional-icon when closed", () => {
		const arrowInAside = () =>
			document.querySelector(
				'aside button[data-nav-item="true"] [data-name="ArrowRight01Icon"]',
			);
		const getArrowClass = () => arrowInAside()?.getAttribute("class") ?? "";

		// Closed submenu (home active): points forward, RTL-mirrored via
		// the shared index.css rule.
		const { rerender } = render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={false} />),
		);
		expect(arrowInAside()).toBeTruthy();
		let cls = getArrowClass();
		expect(cls).toContain("nav-directional-icon");
		expect(cls).not.toContain("rotate-90");
		// Only `rotate` transitions so the RTL mirror flip stays instant
		// while the rotation animates.
		expect(cls).toContain("transition-[rotate]");

		// Open submenu: the SAME glyph rotates 90° to point down — never
		// an icon swap, never the mirroring class while rotated (a
		// mirrored + rotated glyph would point back up).
		rerender(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(arrowInAside()).toBeTruthy();
		cls = getArrowClass();
		expect(cls).toContain("rotate-90");
		expect(cls).not.toContain("nav-directional-icon");
		expect(cls).toContain("transition-[rotate]");

		// Back to a closed submenu (navigation leaves Settings): the
		// SAME glyph returns to the forward/RTL-mirrored direction —
		// rotation follows the SUBMENU state, never the page identity,
		// and the glyph persists across every transition.
		rerender(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={false} />),
		);
		expect(arrowInAside()).toBeTruthy();
		cls = getArrowClass();
		expect(cls).toContain("nav-directional-icon");
		expect(cls).not.toContain("rotate-90");
	});

	it("no ArrowDown01Icon anywhere in the nav (the direction is rotation, never an icon swap)", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		expect(document.querySelector('[data-name="ArrowDown01Icon"]')).toBeNull();
	});

	it("arrow indicator sits in an aria-hidden size-6 wrapper pinned to the row's far-end edge (ms-auto)", () => {
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		const parent = findSettingsParent();
		const wrapper = parent?.querySelector("span[aria-hidden='true']");
		expect(wrapper).toBeTruthy();
		// size-6 = 24px square pointer target around the 16px glyph;
		// ms-auto pins it to the button's end padding edge.
		expect(wrapper?.className).toContain("size-6");
		expect(wrapper?.className).toContain("ms-auto");
	});

	it("submenu content animates its reveal AND hide (collapsibleDown / collapsibleUp) inside an overflow-hidden wrapper", () => {
		const getContent = () =>
			document.querySelector<HTMLElement>("[class*='collapsibleDown']");
		render(
			wrap(
				<Sidebar
					{...baseProps}
					currentPage="settingsGeneral"
					collapsed={false}
				/>,
			),
		);
		const content = getContent();
		expect(content).toBeTruthy();
		// Height clips during the animation; Radix's Presence keeps the
		// node mounted until the closing animation finishes.
		expect(content?.className).toContain("overflow-hidden");
		expect(content?.className).toContain(
			"data-[state=open]:animate-[collapsibleDown_200ms_ease-out]",
		);
		expect(content?.className).toContain(
			"data-[state=closed]:animate-[collapsibleUp_200ms_ease-out]",
		);
	});
});

/** Find the expanded Settings parent button by its exact label text
 *  (child buttons render longer labels like "nav.settingsGeneral"). */
function findSettingsParent(): HTMLElement | null {
	return (
		Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
			(b) => b.textContent === "nav.settings",
		) ?? null
	);
}

/** Find the collapsed-rail Settings flyout trigger by its icon (the
 *  rail renders icon-only buttons with no visible label text). */
function findCollapsedSettingsTrigger(): HTMLElement | null {
	return (
		Array.from(
			document.querySelectorAll<HTMLButtonElement>(
				"aside button[data-nav-item='true']",
			),
		).find((b) => b.querySelector('[data-name="Settings03Icon"]')) ?? null
	);
}

// Helper for fire.click — fireEvent.click is the API.
const fire = { click: (el: HTMLElement) => fireEvent.click(el) };
