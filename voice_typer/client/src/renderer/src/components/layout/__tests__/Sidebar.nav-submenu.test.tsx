/**
 * NavSubmenu behavior tests — covers the nested Settings sidebar
 * submenu (expansion synchronized with navigation). Asserts:
 *   - Settings parent renders + 4 children render when expanded
 *   - Active child carries aria-current="page"
 *   - Settings parent carries aria-expanded="true" when expanded
 *   - Clicking a child calls onNavigate with the child's Page literal
 *   - Collapsed sidebar: Popover flyout shows the 4 children
 *   - ONE persistent arrow glyph (ArrowRight01Icon) that rotates —
 *     never swaps icons; wrapper pinned to the row edge via ms-auto
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

	it("collapsed: the Settings flyout trigger carries aria-haspopup='dialog'", () => {
		render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={true} />),
		);
		const trigger = findCollapsedSettingsTrigger();
		expect(trigger?.getAttribute("aria-haspopup")).toBe("dialog");
		// The collapsed rail has no inline chevron — the arrow glyph
		// renders only when the sidebar is expanded.
		expect(trigger?.querySelector('[data-name="ArrowRight01Icon"]')).toBeNull();
	});

	it("ONE persistent arrow glyph: ArrowRight01Icon in both states — rotated 90° when open, nav-directional-icon when closed", () => {
		const getArrowClass = () => {
			const arrow = document.querySelector(
				'aside button[data-nav-item="true"] [data-name="ArrowRight01Icon"]',
			);
			return arrow?.getAttribute("class") ?? "";
		};

		// Closed submenu (home active): points forward, RTL-mirrored via
		// the shared index.css rule.
		const { rerender } = render(
			wrap(<Sidebar {...baseProps} currentPage="home" collapsed={false} />),
		);
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
		cls = getArrowClass();
		expect(cls).toContain("rotate-90");
		expect(cls).not.toContain("nav-directional-icon");
		expect(cls).toContain("transition-[rotate]");
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
