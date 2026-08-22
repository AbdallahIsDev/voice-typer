/**
 * RTL regression guard for the TitleBar window chrome.
 *
 * The document direction (dir="rtl", set by i18n for Arabic) mirrors
 * flex rows — without protection the whole title bar would flip:
 * the macOS traffic-light gutter would jump to the RIGHT edge and the
 * Windows/Linux minimize/maximize/close cluster to the LEFT edge.
 * Native window chrome never moves with UI language direction, so the
 * bar root is pinned dir="ltr" (physical sides preserved) while the
 * Back/Forward chevrons opt INTO mirroring via the shared
 * `.nav-directional-icon` rule ([dir="rtl"] ancestor selector).
 *
 * jsdom does no layout, so "stays on the physical right" is asserted
 * structurally: inside a dir="ltr" container DOM order == visual order
 * left→right, so the window controls must appear AFTER the leading
 * nav buttons, and the macOS gutter must be the bar's FIRST child.
 *
 * Pattern follows rtl-locale-guard.test.ts / TitleBar.test.tsx:
 * platform constants are module-load derived, so each block stubs the
 * UA and re-imports a fresh TitleBar module.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({ icon }: { icon?: { name?: string } }) => (
		<span data-testid="hugeicon" data-name={icon?.name} />
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

import { TooltipProvider } from "@/components/ui/tooltip";
import type { WindowBridge } from "@/types/ipc";

function renderWithProviders(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

const WIN_UA =
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const MAC_UA =
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36";

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

async function loadTitleBarFor(
	ua: string,
): Promise<typeof import("@/components/layout/TitleBar")["TitleBar"]> {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
	vi.resetModules();
	const { TitleBar } = await import("@/components/layout/TitleBar");
	return TitleBar;
}

describe("TitleBar — RTL pinning (window chrome keeps its physical sides)", () => {
	beforeEach(() => {
		cleanup();
		document.documentElement.dir = "rtl";
	});

	afterEach(() => {
		cleanup();
		document.documentElement.dir = "ltr";
		vi.restoreAllMocks();
		vi.resetModules();
	});

	it("bar root carries dir='ltr' under dir=rtl so the control cluster stays on the physical right", async () => {
		const WinTitleBar = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		const { container } = renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		// The ltr pin is what guarantees physical placement regardless
		// of the html-level rtl direction.
		expect(bar?.getAttribute("dir")).toBe("ltr");

		// Inside a dir=ltr flex row, DOM order == visual left→right.
		// The minimize/maximize/close cluster must therefore come AFTER
		// every leading nav control (toggle/back/forward/help) — i.e.
		// stay pinned at the bar's physical right edge — even though
		// the document is rtl.
		const labels = Array.from(
			bar?.querySelectorAll("button[aria-label]") ?? [],
		).map((b) => b.getAttribute("aria-label"));
		const closeIdx = labels.indexOf("Close");
		for (const leading of [
			"Toggle sidebar (Ctrl+B)",
			"Go back",
			"Go forward",
			"Help Overlay",
		]) {
			const idx = labels.indexOf(leading);
			expect(idx).toBeGreaterThanOrEqual(0);
			expect(closeIdx).toBeGreaterThan(idx);
		}
	});

	it("macOS traffic-light gutter stays the bar's physically-leftmost element under dir=rtl", async () => {
		const MacTitleBar = await loadTitleBarFor(MAC_UA);
		const { container } = renderWithProviders(
			<MacTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		expect(bar?.getAttribute("dir")).toBe("ltr");
		// The 72px (w-18) gutter is the bar's first child = physical
		// left edge, where the native traffic lights live.
		const firstChild = bar?.children[0];
		expect(firstChild?.className).toContain("w-18");
	});

	it("back/forward chevrons carry nav-directional-icon so they still mirror under dir=rtl", async () => {
		const WinTitleBar = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		const { container } = renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		// The chevrons are raw SVGs inside the back/forward buttons;
		// the [dir="rtl"] ancestor selector in index.css flips them via
		// this class even though the bar itself is pinned dir="ltr".
		const backSvg = container
			.querySelector('button[aria-label="Go back"]')
			?.querySelector("svg.nav-directional-icon");
		const forwardSvg = container
			.querySelector('button[aria-label="Go forward"]')
			?.querySelector("svg.nav-directional-icon");
		expect(backSvg).toBeTruthy();
		expect(forwardSvg).toBeTruthy();
	});
});
