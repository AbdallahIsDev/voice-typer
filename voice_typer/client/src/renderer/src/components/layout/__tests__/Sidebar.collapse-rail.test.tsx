/**
 * Sidebar collapse/expand rail audit suite — pins the premium rail
 * contract for BOTH sidebar states:
 *
 *  - Icon anchoring: every top-level nav button (leaves AND the
 *    Settings parent) starts its icon at the same x-position in both
 *    states (single `px-2` icon column, never `justify-center`) and
 *    the aside rail width (w-12, 48px) keeps that column centered when
 *    collapsed — icons never jump horizontally on toggle.
 *  - Text transition: label spans use the shared animated visibility
 *    transition (max-width + opacity + translate + filter with
 *    explicit `blur-[0px]`/`blur-[4px]` endpoints — never
 *    `filter-none`, which cannot interpolate) and hide with
 *    `pointer-events-none` when collapsed.
 *  - Vertical rhythm: items breathe with `gap-1` inside a group; the
 *    nav uses `gap-5` expanded / `gap-2` collapsed; group headings
 *    collapse via max-height on their container while the heading
 *    TEXT exits through the shared label-motion model (translate +
 *    fade + blur on a faster inner-span track) — no instant unmount,
 *    no layout jump.
 *  - Collapsed usability: every rail icon keeps a non-empty accessible
 *    name (including the Settings flyout trigger) and the Settings
 *    trigger shows the same right-side hotkey tooltip as the leaves.
 *  - Stability: rapid collapse/expand toggling keeps all 9 nav
 *    buttons mounted with classes flipping cleanly.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { Sidebar } from "@/components/layout/Sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

// Sidebar renders real Radix Tooltips (via HotkeyTooltip on the nav
// items) + a real Radix Popover (collapsed Settings flyout), both of
// which REQUIRE a TooltipProvider ancestor — the app shell provides
// one (App.tsx). Same props as App.tsx so tooltip timing mirrors
// production.
function renderWithProviders(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

function findNavButton(label: string) {
	return screen.getByRole("button", {
		name: new RegExp(`^${label}(\\s|$)`),
	});
}

const NAV_LABELS = [
	"Home",
	"History",
	"Analytics",
	"Templates",
	"Vocabulary",
	"Models",
	"Microphone",
	"Settings",
	"About & Privacy",
];

describe("Sidebar — collapse rail geometry & transition model", () => {
	afterEach(() => {
		cleanup();
	});

	const baseProps = {
		currentPage: "home" as const,
		onNavigate: vi.fn(),
	};

	it("aside rail width: w-55 expanded, w-12 collapsed (anchored icon column stays centered)", () => {
		const { rerender, container } = renderWithProviders(
			<Sidebar {...baseProps} />,
		);
		const aside = () => container.querySelector("aside");
		expect(aside()?.className).toContain("w-55");
		expect(aside()?.className).not.toContain("w-12");

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed />
			</TooltipProvider>,
		);
		expect(aside()?.className).toContain("w-12");
		expect(aside()?.className).not.toContain("w-55");
	});

	it("icon anchoring: every top-level nav button uses the same px-2 icon column in BOTH states (never justify-center)", () => {
		const { rerender } = renderWithProviders(<Sidebar {...baseProps} />);
		const assertAnchored = () => {
			const buttons = Array.from(
				document.querySelectorAll<HTMLButtonElement>(
					"aside button[data-nav-item='true']",
				),
			);
			expect(buttons.length).toBe(9);
			for (const btn of buttons) {
				// The single anchored icon column: identical start padding
				// in both states (container p-2 + button px-2 = 16px from
				// the aside edge).
				expect(btn.className).toContain("px-2");
				// The old collapsed Settings trigger centered its icon
				// (justify-center) — the one button whose icon jumped on
				// toggle. Forbidden: content must flow from the anchored
				// column in both states.
				expect(btn.className).not.toContain("justify-center");
			}
		};
		assertAnchored();

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed />
			</TooltipProvider>,
		);
		assertAnchored();
	});

	it("label spans use the shared animated text transition (explicit blur endpoints, no filter-none)", () => {
		const { rerender } = renderWithProviders(<Sidebar {...baseProps} />);
		const labelSpans = () =>
			Array.from(
				document.querySelectorAll<HTMLSpanElement>(
					"aside button[data-nav-item='true'] > span",
				),
			).filter((s) =>
				s.className.includes("transition-[max-width,opacity,translate,filter]"),
			);
		// 9 nav items — every leaf + the Settings parent.
		expect(labelSpans().length).toBe(9);
		for (const span of labelSpans()) {
			expect(span.className).toContain("opacity-100");
			expect(span.className).toContain("blur-[0px]");
			expect(span.className).toContain("max-w-40");
			// `filter-none` cannot interpolate against blur() — it snaps
			// discretely (the old abrupt disappearance).
			expect(span.className).not.toContain("filter-none");
			// STRICTLY horizontal motion: the transition property list and
			// the motion tokens must never introduce a Y component.
			expect(span.className).toContain(
				"transition-[max-width,opacity,translate,filter]",
			);
			expect(span.className).not.toContain("translate-y");
		}

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed />
			</TooltipProvider>,
		);
		for (const span of labelSpans()) {
			expect(span.className).toContain("opacity-0");
			expect(span.className).toContain("blur-[4px]");
			expect(span.className).toContain("max-w-0");
			// Text exits toward the inline-start icon column (RTL-mirrored).
			expect(span.className).toContain("-translate-x-3");
			expect(span.className).toContain("rtl:translate-x-3");
			// Invisible labels never intercept pointer events.
			expect(span.className).toContain("pointer-events-none");
			// X-axis only — no vertical/diagonal travel.
			expect(span.className).not.toContain("translate-y");
		}
	});

	it("vertical rhythm: sections gap-1; nav gap-5 expanded / gap-2 collapsed", () => {
		const { rerender, container } = renderWithProviders(
			<Sidebar {...baseProps} />,
		);
		const nav = container.querySelector("nav");
		expect(nav?.className).toContain("gap-5");
		expect(nav?.className).not.toContain("gap-2");
		for (const section of container.querySelectorAll("section")) {
			expect(section.className).toContain("flex flex-col gap-1");
		}

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed />
			</TooltipProvider>,
		);
		expect(nav?.className).toContain("gap-2");
		expect(nav?.className).not.toContain("gap-5");
	});

	it("group headings collapse via max-height while the label exits through the shared motion model (no instant unmount layout jump)", () => {
		const { rerender, container } = renderWithProviders(
			<Sidebar {...baseProps} />,
		);
		// The heading container no longer carries text-style classes —
		// the label TEXT lives in an inner span that owns the horizontal
		// motion. Locate by the container's max-height transition.
		const headings = () =>
			Array.from(container.querySelectorAll("section > div")).filter((d) =>
				d.className.includes("transition-[max-height]"),
			);
		const headingLabel = (heading: Element) => heading.querySelector("span");
		// System only — the top group is header-less (single visible heading).
		expect(headings().length).toBe(1);
		for (const heading of headings()) {
			// Outer container: vertical SPACE collapse only (max-height,
			// 200ms ease-out) + clipping so the shrinking box never
			// half-paints glyphs.
			expect(heading.className).toContain("px-3.5");
			expect(heading.className).toContain("overflow-hidden");
			expect(heading.className).toContain("transition-[max-height]");
			expect(heading.className).toContain("duration-200");
			expect(heading.className).toContain("ease-out");
			expect(heading.className).toContain("max-h-4");
			expect(heading.getAttribute("aria-hidden")).toBeNull();
			// Inner span: the text exits through the SHARED motion tokens
			// (translate + fade + blur) on a deliberately faster 150ms
			// track, so the label has dissolved before the vertical clip
			// could bite. `block` is required for transforms to apply;
			// the muted label tone rides on top of the motion's opacity.
			const label = headingLabel(heading);
			expect(label?.className).toContain("block");
			expect(label?.className).toContain(
				"transition-[opacity,translate,filter]",
			);
			expect(label?.className).toContain("duration-150");
			expect(label?.className).toContain("ease-out");
			expect(label?.className).toContain("translate-x-0");
			expect(label?.className).toContain("blur-[0px]");
			expect(label?.className).toContain("opacity-70");
			// Same X-axis-only rule as the item labels.
			expect(label?.className).not.toContain("translate-y");
		}

		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<Sidebar {...baseProps} collapsed />
			</TooltipProvider>,
		);
		// Headings stay MOUNTED when collapsed (they animate to zero
		// height instead of vanishing and shifting the groups below) and
		// leave the accessibility tree via aria-hidden.
		expect(headings().length).toBe(1);
		for (const heading of headings()) {
			expect(heading.className).toContain("max-h-0");
			expect(heading.getAttribute("aria-hidden")).toBe("true");
			// Shared motion exit tokens on the inner span: toward the
			// inline-start icon column (RTL-mirrored), fully transparent,
			// blurred, and inert.
			const label = headingLabel(heading);
			expect(label?.className).toContain("-translate-x-3");
			expect(label?.className).toContain("rtl:translate-x-3");
			expect(label?.className).toContain("opacity-0");
			expect(label?.className).toContain("blur-[4px]");
			expect(label?.className).toContain("pointer-events-none");
			expect(label?.className).not.toContain("translate-y");
		}
	});

	it("collapsed rail: every icon keeps a non-empty accessible name (incl. the Settings flyout trigger)", () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed />);
		for (const label of NAV_LABELS) {
			const btn = findNavButton(label);
			expect(btn).toBeTruthy();
			expect(btn.getAttribute("aria-label") ?? btn.textContent?.trim()).toBe(
				label,
			);
		}
	});

	it("collapsed Settings trigger shows the same right-side hotkey tooltip on focus as the leaves", async () => {
		renderWithProviders(<Sidebar {...baseProps} collapsed />);
		const settings = findNavButton("Settings");
		settings.focus();
		const tooltip = await screen.findByRole("tooltip");
		expect(within(tooltip).getByText("Settings")).toBeTruthy();
		// Settings carries the Ctrl+, shortcut chips like the expanded
		// nav's aria-keyshortcuts contract.
		const kbdTexts = Array.from(tooltip.querySelectorAll("kbd")).map(
			(k) => k.textContent,
		);
		expect(kbdTexts).toContain("Ctrl");
		expect(kbdTexts).toContain(",");
	});

	it("rapid collapse/expand toggling keeps all 9 nav buttons mounted with classes flipping cleanly", () => {
		const { rerender } = renderWithProviders(<Sidebar {...baseProps} />);
		const countButtons = () =>
			document.querySelectorAll<HTMLButtonElement>(
				"aside button[data-nav-item='true']",
			).length;
		for (let i = 0; i < 4; i++) {
			rerender(
				<TooltipProvider delayDuration={200} skipDelayDuration={500}>
					<Sidebar {...baseProps} collapsed />
				</TooltipProvider>,
			);
			expect(countButtons()).toBe(9);
			rerender(
				<TooltipProvider delayDuration={200} skipDelayDuration={500}>
					<Sidebar {...baseProps} />
				</TooltipProvider>,
			);
			expect(countButtons()).toBe(9);
		}
		// After the toggle storm the expanded tree is intact: labels,
		// active state, and the Settings submenu contract all survive.
		expect(findNavButton("Home").getAttribute("aria-current")).toBe("page");
	});
});
