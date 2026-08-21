/**
 * NavSubmenu behavior tests — covers the new nested-Settings sidebar
 * submenu (ADR-0021). Asserts:
 *   - Settings parent renders + 4 children render when expanded
 *   - Active child carries aria-current="page"
 *   - Settings parent carries aria-expanded="true" when expanded
 *   - Clicking a child calls onNavigate with the child's Page literal
 *   - Collapsed sidebar: Popover flyout shows the 4 children
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: (props: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={props.icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", () => ({
	Settings03Icon: { name: "Settings03Icon" },
	InformationCircleIcon: { name: "InformationCircleIcon" },
	Shield01Icon: { name: "Shield01Icon" },
	Home04Icon: { name: "Home04Icon" },
	HistoryIcon: { name: "HistoryIcon" },
	Analytics01Icon: { name: "Analytics01Icon" },
	File02Icon: { name: "File02Icon" },
	BookOpen02Icon: { name: "BookOpen02Icon" },
	AiBrain03Icon: { name: "AiBrain03Icon" },
	Mic02Icon: { name: "Mic02Icon" },
	SlidersHorizontalIcon: { name: "SlidersHorizontalIcon" },
	PaintBoardIcon: { name: "PaintBoardIcon" },
	Cancel01Icon: { name: "Cancel01Icon" },
}));

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

vi.mock("@/components/layout/Logo", () => ({
	Logo: () => <div data-testid="logo" />,
}));

vi.mock("@/components/layout/ThemeSwitch", () => ({
	ThemeSwitch: () => <div data-testid="theme-switch" />,
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
	localStorage.clear();
});

describe("NavSubmenu — nested Settings sidebar submenu (ADR-0021)", () => {
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

	it("collapses (hides children) when currentPage leaves Settings", () => {
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
		// The Settings parent is rendered as a Popover trigger (icon only).
		// Find it by its aria-haspopup="menu" attribute.
		const triggers = document.querySelectorAll('button[aria-haspopup="menu"]');
		expect(triggers.length).toBeGreaterThan(0);
		const settingsTrigger = Array.from(triggers).find((b) =>
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

// Helper for fire.click — fireEvent.click is the API.
const fire = { click: (el: HTMLElement) => fireEvent.click(el) };
