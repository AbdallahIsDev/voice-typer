/**
 * TitleBar — Linux window-button layout (system / custom / KDE square).
 *
 * Covers the `linuxWindowButtons` prop contract added with the
 * linux_window_buttons config field:
 *   1. Default (no prop): right-side trio, circle shells.
 *   2. side "left": the cluster renders at the PHYSICAL left edge
 *      (before the sidebar-toggle toolbar button — the bar is pinned
 *      dir="ltr").
 *   3. Custom visibility: hidden buttons are not rendered at all.
 *   4. KDE square: button shells lose `rounded-full`, gain `rounded-none`.
 *   5. Windows ignores the prop entirely (native fixed convention).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { WindowBridge } from "@/types/ipc";

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

const LINUX_UA =
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const WIN_UA =
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

async function loadTitleBarFor(ua: string) {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
	vi.resetModules();
	const hotkeyUtils = await import("@/components/hotkey/hotkey-utils");
	const { TitleBar } = await import("@/components/layout/TitleBar");
	return {
		TitleBar,
		IS_LINUX: hotkeyUtils.IS_LINUX,
		IS_WIN: hotkeyUtils.IS_WIN,
	};
}

function makeBridge(): WindowBridge {
	return {
		minimize: vi.fn().mockResolvedValue(undefined),
		toggleMaximize: vi.fn().mockResolvedValue(true),
		close: vi.fn().mockResolvedValue(undefined),
		isMaximized: vi.fn().mockResolvedValue(false),
		onMaximizedChanged: vi.fn().mockReturnValue(() => {}),
		exportHistory: vi.fn().mockResolvedValue({ success: true }),
		exportVocabulary: vi.fn().mockResolvedValue({ success: true }),
	};
}

function renderBar(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

function buttonLabels(): string[] {
	return Array.from(document.querySelectorAll("button[aria-label]")).map(
		(button) => button.getAttribute("aria-label") ?? "",
	);
}

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	vi.resetModules();
});

const BASE_PROPS = {
	onToggleSidebar: () => {},
	isMaximized: false,
	onOpenHelp: () => {},
	themeMode: "light" as const,
	onThemeChange: () => {},
};

describe("TitleBar — Linux window-button layout", () => {
	it("default (no prop): right-side trio with circle shells", async () => {
		const { TitleBar, IS_LINUX } = await loadTitleBarFor(LINUX_UA);
		expect(IS_LINUX).toBe(true);
		(window as unknown as { window_?: WindowBridge }).window_ = makeBridge();
		renderBar(<TitleBar {...BASE_PROPS} />);
		const labels = buttonLabels();
		expect(labels).toContain("Minimize");
		expect(labels).toContain("Maximize");
		expect(labels).toContain("Close");
		// Right side: the window buttons come AFTER the theme switch in
		// the DOM (the theme control sits immediately left of them).
		const themeIdx = labels.findIndex((l) => l.includes("theme"));
		const minIdx = labels.indexOf("Minimize");
		expect(minIdx).toBeGreaterThan(themeIdx);
		const minBtn = screen.getByLabelText("Minimize");
		expect(minBtn.className).toContain("rounded-full");
		expect(minBtn.className).not.toContain("rounded-none");
	});

	it("side left: the cluster renders BEFORE the sidebar-toggle button", async () => {
		const { TitleBar } = await loadTitleBarFor(LINUX_UA);
		(window as unknown as { window_?: WindowBridge }).window_ = makeBridge();
		renderBar(
			<TitleBar
				{...BASE_PROPS}
				linuxWindowButtons={{
					side: "left",
					showMinimize: true,
					showMaximize: true,
					showClose: true,
					buttonStyle: "circle",
					followsSystem: false,
				}}
			/>,
		);
		const labels = buttonLabels();
		const toggleIdx = labels.findIndex((l) => l.startsWith("Toggle sidebar"));
		const minIdx = labels.indexOf("Minimize");
		expect(toggleIdx).toBeGreaterThan(-1);
		expect(minIdx).toBeGreaterThan(-1);
		expect(minIdx).toBeLessThan(toggleIdx);
	});

	it("custom visibility: hidden buttons are not rendered", async () => {
		const { TitleBar } = await loadTitleBarFor(LINUX_UA);
		(window as unknown as { window_?: WindowBridge }).window_ = makeBridge();
		renderBar(
			<TitleBar
				{...BASE_PROPS}
				linuxWindowButtons={{
					side: "right",
					showMinimize: true,
					showMaximize: false,
					showClose: false,
					buttonStyle: "circle",
					followsSystem: false,
				}}
			/>,
		);
		expect(screen.getByLabelText("Minimize")).toBeTruthy();
		expect(screen.queryByLabelText("Maximize")).toBeNull();
		expect(screen.queryByLabelText("Close")).toBeNull();
	});

	it("KDE square style: rounded-none instead of rounded-full", async () => {
		const { TitleBar } = await loadTitleBarFor(LINUX_UA);
		(window as unknown as { window_?: WindowBridge }).window_ = makeBridge();
		renderBar(
			<TitleBar
				{...BASE_PROPS}
				linuxWindowButtons={{
					side: "right",
					showMinimize: true,
					showMaximize: true,
					showClose: true,
					buttonStyle: "square",
					followsSystem: false,
				}}
			/>,
		);
		const minBtn = screen.getByLabelText("Minimize");
		expect(minBtn.className).toContain("rounded-none");
		expect(minBtn.className).not.toContain("rounded-full");
	});

	it("Windows ignores the prop (fixed native convention)", async () => {
		const { TitleBar, IS_WIN } = await loadTitleBarFor(WIN_UA);
		expect(IS_WIN).toBe(true);
		(window as unknown as { window_?: WindowBridge }).window_ = makeBridge();
		renderBar(
			<TitleBar
				{...BASE_PROPS}
				linuxWindowButtons={{
					side: "left",
					showMinimize: true,
					showMaximize: true,
					showClose: false, // even "hide close" must be ignored
					buttonStyle: "square",
					followsSystem: false,
				}}
			/>,
		);
		const labels = buttonLabels();
		// All three present (close NOT hidden) and still on the RIGHT
		// (after the theme switch), exactly like the native convention.
		expect(labels.indexOf("Close")).toBeGreaterThan(-1);
		const themeIdx = labels.findIndex((l) => l.includes("theme"));
		expect(labels.indexOf("Minimize")).toBeGreaterThan(themeIdx);
	});
});
