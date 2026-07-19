/**
 * FIX-15 vitest suite — covers PROD-9 for the TitleBar component.
 *
 * - PROD-9 (Medium): TitleBar close button used hardcoded hex
 *   `#C42B1C`. Replaced with the destructive design tokens
 *   (`hover:bg-destructive hover:text-destructive-foreground`).
 * - PROD-9: aria-keyshortcuts="Control+B" on the sidebar toggle.
 * - PROD-9: aria-keyshortcuts="?" on the help button.
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

describe("TitleBar — FIX-15 PROD-9", () => {
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
		// The destructive token replacement.
		expect(cls).toContain("hover:bg-destructive");
		expect(cls).toContain("hover:text-destructive-foreground");
		expect(cls).toContain("focus-visible:bg-destructive");
		expect(cls).toContain("focus-visible:text-destructive-foreground");
		// The hardcoded hex must be gone.
		expect(cls).not.toContain("#C42B1C");
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
