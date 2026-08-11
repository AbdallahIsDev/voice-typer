/**
 *  vitest suite — covers  for the TitleBar component.
 *
 * -  (Medium): TitleBar close button used hardcoded hex
 *   `#C42B1C`. Replaced with the destructive design tokens
 *   (`hover:bg-destructive hover:text-destructive-foreground`).
 * - : aria-keyshortcuts="Control+B" on the sidebar toggle.
 * - : aria-keyshortcuts="?" on the help button.
 *
 * The WindowBridge is stubbed so the component can mount in jsdom
 * without the Electron preload present.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", () => ({
	PanelLeftIcon: { name: "PanelLeftIcon" },
}));

import { TitleBar } from "@/components/layout/TitleBar";
import type { WindowBridge } from "@/types/ipc";

function makeBridge(overrides: Partial<WindowBridge> = {}): WindowBridge {
	return {
		minimize: vi.fn().mockResolvedValue(undefined),
		toggleMaximize: vi.fn().mockResolvedValue(true),
		close: vi.fn().mockResolvedValue(undefined),
		isMaximized: vi.fn().mockResolvedValue(false),
		onMaximizedChanged: vi.fn().mockReturnValue(() => {}),
		exportHistory: vi.fn().mockResolvedValue({ success: true }),
		exportVocabulary: vi.fn().mockResolvedValue({ success: true }),
		...overrides,
	};
}

describe("TitleBar", () => {
	beforeEach(() => {
		cleanup();
		// Wipe window_ between tests so each render starts fresh.
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("PROD-9: close button uses destructive tokens instead of hardcoded #C42B1C", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		expect(closeBtn).toBeTruthy();
		const cls = closeBtn.className;
		// The destructive token replacement (hover/focus ONLY — the
		// resting state must stay neutral like a platform title bar).
		expect(cls).toContain("hover:bg-destructive");
		expect(cls).toContain("hover:text-destructive-foreground");
		expect(cls).toContain("focus-visible:bg-destructive");
		expect(cls).toContain("focus-visible:text-destructive-foreground");
		// The hardcoded hex must be gone.
		expect(cls).not.toContain("#C42B1C");
	});

	it("close button is neutral at rest (no red tint / destructive variant class)", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		const cls = closeBtn.className;
		// The `destructive` cva variant paints `bg-destructive/10` at
		// REST (a red wash over the whole hit target). The close
		// button must use the neutral ghost base + destructive hover
		// so it stays clean until hovered/focused.
		expect(cls).toContain("hover:bg-destructive");
		expect(cls).not.toContain("bg-destructive/10");
		expect(cls).not.toContain("bg-destructive/20");
		expect(cls).not.toContain("dark:bg-destructive/20");
	});

	it("window-control icons pin size-3.5 so the shared Button 16px svg rule cannot stretch them", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		const { container } = render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		// Minimize + Maximize + Close icons live inside the three
		// window-control buttons (data-slot="button" marks the shared
		// Button). Each glyph svg must carry the explicit size-3.5
		// (14px) class; otherwise the Button base rule
		// `[&_svg:not([class*='size-'])]:size-4` inflates the 10x10
		// glyphs to 16px.
		const svgs = container.querySelectorAll(
			'[data-slot="button"] svg[aria-hidden="true"]',
		);
		expect(svgs.length).toBe(3);
		for (const svg of svgs) {
			expect(svg.getAttribute("class")).toContain("size-3.5");
		}
	});

	it("PROD-9: minimize/maximize buttons keep the neutral hover (not destructive)", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const minBtn = screen.getByLabelText("Minimize");
		const maxBtn = screen.getByLabelText("Maximize");
		expect(minBtn.className).not.toContain("hover:bg-destructive");
		expect(maxBtn.className).not.toContain("hover:bg-destructive");
	});

	it("PROD-9: sidebar toggle button exposes aria-keyshortcuts='Control+B'", () => {
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		expect(toggle.tagName).toBe("BUTTON");
		expect(toggle.getAttribute("aria-keyshortcuts")).toBe("Control+B");
	});

	it("PROD-9: help button exposes aria-keyshortcuts='?'", () => {
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		// en.json: help.openHelp = "Open this help overlay"
		const helpBtn = screen.getByLabelText("Open this help overlay");
		expect(helpBtn.tagName).toBe("BUTTON");
		expect(helpBtn.getAttribute("aria-keyshortcuts")).toBe("?");
	});

	it("PROD-9: close button still calls bridge.close() on click", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		closeBtn.click();
		expect(bridge.close).toHaveBeenCalledTimes(1);
	});
});

describe("TitleBar — XA-1 (focus-ring parity + sidebar-toggle hover)", () => {
	beforeEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("XA-1: sidebar-toggle button has rounded + transition + hover:bg-foreground/5 (parity with back/forward/help)", () => {
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		const cls = toggle.className;
		// Previously the toggle was the only TitleBar button missing
		// rounded corners + a hover background. The fix brings it in
		// line with its sibling back/forward/help buttons.
		expect(cls).toContain("rounded");
		expect(cls).toContain("transition-colors");
		expect(cls).toContain("duration-150");
		expect(cls).toContain("hover:bg-foreground/5");
	});

	it("XA-1: all four TitleBar icon buttons use the shared focusRing (ring-3, not ring-2)", () => {
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		const back = screen.getByLabelText("Go back");
		const forward = screen.getByLabelText("Go forward");
		const help = screen.getByLabelText("Open this help overlay");
		for (const btn of [toggle, back, forward, help]) {
			const cls = btn.className;
			// Design-system Button uses ring-3; TitleBar previously used
			// ring-2 (thinner). Migrate to ring-3 via the shared focusRing
			// constant. The ring uses the full-opacity ring-ring token —
			// not ring-ring/30 (see focus-ring-contrast.test.tsx: the 30%
			// alpha ring failed WCAG 2.4.7 focus-visible contrast and was
			// replaced repo-wide with the full-opacity token).
			expect(cls).toContain("focus-visible:ring-3");
			expect(cls).toContain("focus-visible:ring-ring");
			expect(cls).not.toContain("focus-visible:ring-2");
		}
	});

	it("XA-1: window-control TitleBarButtons use ring-3 focus ring (matches Button)", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		render(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
			/>,
		);
		const minimize = screen.getByLabelText("Minimize");
		const maximize = screen.getByLabelText("Maximize");
		const close = screen.getByLabelText("Close");
		for (const btn of [minimize, maximize, close]) {
			const cls = btn.className;
			expect(cls).toContain("focus-visible:ring-3");
			expect(cls).not.toContain("focus-visible:ring-2");
		}
	});
});
