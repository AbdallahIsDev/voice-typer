import { act, cleanup, render, screen, within } from "@testing-library/react";
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

import { TitleBar } from "@/components/layout/TitleBar";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { WindowBridge } from "@/types/ipc";

// TitleBar renders real Radix Tooltips (via HotkeyTooltip on the
// sidebar/back/forward/help buttons), which REQUIRE a TooltipProvider
// ancestor, the app shell provides one (App.tsx:475). Same props as
// App.tsx so tooltip timing in tests mirrors production.
function renderWithProviders(ui: React.ReactElement) {
	return render(
		<TooltipProvider delayDuration={200} skipDelayDuration={500}>
			{ui}
		</TooltipProvider>,
	);
}

const WIN_UA =
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
const LINUX_UA =
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";
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

interface LoadedTitleBar {
	TitleBar: typeof TitleBar;
	/** The platform constants from the SAME fresh module registry the
	 *  loaded TitleBar instance saw (see loadTitleBarFor). */
	IS_WIN: boolean;
	IS_LINUX: boolean;
	IS_MAC: boolean;
}

async function loadTitleBarFor(ua: string): Promise<LoadedTitleBar> {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
	vi.resetModules();
	// Fresh module instances, hotkey-utils re-evaluates its
	// module-load platform constants against the stubbed UA, and
	// TitleBar re-imports them from that same fresh registry.
	const hotkeyUtils = await import("@/components/hotkey/hotkey-utils");
	const { TitleBar: PlatformTitleBar } = await import(
		"@/components/layout/TitleBar"
	);
	return {
		TitleBar: PlatformTitleBar,
		IS_WIN: hotkeyUtils.IS_WIN,
		IS_LINUX: hotkeyUtils.IS_LINUX,
		IS_MAC: hotkeyUtils.IS_MAC,
	};
}

describe("TitleBar, Windows window controls (red close hover)", () => {
	// Reset the module registry after each test so a later block's
	// static `TitleBar` import re-resolves on the default (Linux) UA.
	afterEach(() => {
		vi.restoreAllMocks();
		vi.resetModules();
	});

	it("IS_WIN-pinned: Windows UA resolves IS_WIN=true/IS_LINUX=false and the close button renders the red hover", async () => {
		const {
			TitleBar: WinTitleBar,
			IS_WIN,
			IS_LINUX,
			IS_MAC,
		} = await loadTitleBarFor(WIN_UA);
		// Pin the platform constants FIRST, the red hover below is
		// only correct because the component resolved IS_WIN=true.
		// These assertions guard the UA→constant derivation AND the
		// close-variant gate (`IS_WIN ? "close" : "default"`), so a
		// change to either half of the coupling fails loudly.
		expect(IS_WIN).toBe(true);
		expect(IS_LINUX).toBe(false);
		expect(IS_MAC).toBe(false);

		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		const cls = closeBtn.className;
		// Red hover must be present BECAUSE IS_WIN=true.
		expect(cls).toContain("hover:bg-[#e81123]");
		expect(cls).toContain("dark:hover:bg-[#e81123]");
	});

	it("close button is neutral at rest (red appears only on hover/focus)", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		const cls = closeBtn.className;
		// The red must only appear as `hover:bg-[#e81123]` /
		// `focus-visible:bg-[#e81123]`, never as a standalone
		// (resting) `bg-[#e81123]` class or any theme-tinted red wash.
		expect(cls).toContain("hover:bg-[#e81123]");
		expect(cls).not.toMatch(/(^|\s)bg-\[#e81123\](\s|$)/);
		expect(cls).not.toContain("bg-destructive/10");
		expect(cls).not.toContain("bg-destructive/20");
		expect(cls).not.toContain("dark:bg-destructive/20");
	});

	it("whole title bar dims via CONTAINER opacity while the window is UNFOCUSED, restored on refocus", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
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
		// The dim lives on the bar CONTAINER (opacity scales the
		// theme's own colors, theme-agnostic, safe in light/dark/
		// custom themes) rather than a per-glyph dim color.
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		// Focused by default → no dim.
		expect(bar?.className).not.toContain("opacity-60");
		// Window loses focus (user clicked another app, e.g. VS Code) →
		// the WHOLE bar dims, every element (sidebar/back/forward/help
		// + all three window controls), while the pure-white glyph
		// color pins stay untouched underneath.
		act(() => {
			window.dispatchEvent(new Event("blur"));
		});
		expect(bar?.className).toContain("opacity-60");
		const minBtn = screen.getByLabelText("Minimize");
		const maxBtn = screen.getByLabelText("Maximize");
		const closeBtn = screen.getByLabelText("Close");
		for (const btn of [minBtn, maxBtn, closeBtn]) {
			expect(btn.className).toContain("text-foreground");
			expect(btn.className).toContain("dark:text-white");
		}
		// Window regains focus → dim removed, full brightness.
		act(() => {
			window.dispatchEvent(new Event("focus"));
		});
		expect(bar?.className).not.toContain("opacity-60");
	});

	it("PROD-9: minimize/maximize buttons keep the neutral hover (not destructive)", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const minBtn = screen.getByLabelText("Minimize");
		const maxBtn = screen.getByLabelText("Maximize");
		expect(minBtn.className).not.toContain("hover:bg-destructive");
		expect(maxBtn.className).not.toContain("hover:bg-destructive");
	});

	it("PROD-9: sidebar toggle button exposes aria-keyshortcuts='Control+B'", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		expect(toggle.tagName).toBe("BUTTON");
		expect(toggle.getAttribute("aria-keyshortcuts")).toBe("Control+B");
	});

	it("renders the sidebar-toggle shortcut as Kbd chips in a Radix tooltip, keeping the aria-label", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		// The plain-text `title` is gone, the shortcut moved into the
		// Radix tooltip as Kbd chips.
		expect(toggle.hasAttribute("title")).toBe(false);
		// Focusing the trigger opens the tooltip (Radix opens on focus).
		toggle.focus();
		const tooltip = await screen.findByRole("tooltip");
		// Label text + one <kbd> chip per key of the combo ("Ctrl+B").
		// (KbdGroup wraps the combo in an outer <kbd>, so we assert the
		// chip texts rather than a fixed element count.)
		expect(within(tooltip).getByText("Toggle sidebar")).toBeTruthy();
		const kbdTexts = Array.from(tooltip.querySelectorAll("kbd")).map(
			(k) => k.textContent,
		);
		expect(kbdTexts).toContain("Ctrl");
		expect(kbdTexts).toContain("B");
		// The accessible name is preserved, aria-label untouched.
		expect(toggle.getAttribute("aria-label")).toBe("Toggle sidebar (Ctrl+B)");
	});

	it("PROD-9: help button exposes aria-keyshortcuts='?'", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		// en.json: help.openHelp = "Help Overlay"
		const helpBtn = screen.getByLabelText("Help Overlay");
		expect(helpBtn.tagName).toBe("BUTTON");
		expect(helpBtn.getAttribute("aria-keyshortcuts")).toBe("?");
	});

	it("PROD-9: close button still calls bridge.close() on click", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		closeBtn.click();
		expect(bridge.close).toHaveBeenCalledTimes(1);
	});
});

describe("TitleBar, Linux window controls (GNOME/KDE neutral close hover)", () => {
	// jsdom's default UA is Linux; the block below still loads the
	// module explicitly with a Linux UA so the assertions document the
	// Linux platform contract rather than depending on jsdom defaults.
	afterEach(() => {
		vi.restoreAllMocks();
		vi.resetModules();
	});

	it("IS_LINUX-pinned: Linux UA resolves IS_LINUX=true/IS_WIN=false and the close button renders the neutral hover", async () => {
		const {
			TitleBar: LinuxTitleBar,
			IS_WIN,
			IS_LINUX,
			IS_MAC,
		} = await loadTitleBarFor(LINUX_UA);
		// Pin the platform constants FIRST, the neutral hover below
		// is only correct because the component resolved
		// IS_LINUX=true / IS_WIN=false. These assertions guard the
		// UA→constant derivation AND the close-variant gate, so a
		// change to either half of the coupling fails loudly.
		expect(IS_LINUX).toBe(true);
		expect(IS_WIN).toBe(false);
		expect(IS_MAC).toBe(false);

		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<LinuxTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		const cls = closeBtn.className;
		// NO red anywhere, neutral hover because IS_LINUX=true.
		expect(cls).not.toContain("hover:bg-[#e81123]");
		expect(cls).not.toContain("dark:hover:bg-[#e81123]");
		expect(cls).toContain("hover:bg-foreground/10");
		expect(cls).toContain("dark:hover:bg-foreground/10");
	});

	it("minimize/maximize/close buttons render on Linux (only macOS uses traffic lights)", async () => {
		const { TitleBar: LinuxTitleBar } = await loadTitleBarFor(LINUX_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<LinuxTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		expect(screen.getByLabelText("Minimize")).toBeTruthy();
		expect(screen.getByLabelText("Maximize")).toBeTruthy();
		expect(screen.getByLabelText("Close")).toBeTruthy();
	});

	it("close button still calls bridge.close() on Linux", async () => {
		const { TitleBar: LinuxTitleBar } = await loadTitleBarFor(LINUX_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<LinuxTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const closeBtn = screen.getByLabelText("Close");
		closeBtn.click();
		expect(bridge.close).toHaveBeenCalledTimes(1);
	});
});
describe("TitleBar, macOS native traffic-light mode", () => {
	// IS_MAC is a module-load constant computed from navigator.userAgent
	// (hotkey-utils.ts). To exercise the macOS path we stub the UA,
	// wipe the module cache, and re-import TitleBar with a cache-busting
	// query so the platform constants re-evaluate. Reuses the shared
	// `loadTitleBarFor` helper (same spy→resetModules→re-import
	// sequence as the Windows/Linux blocks).
	afterEach(() => {
		vi.restoreAllMocks();
		vi.resetModules();
	});

	async function loadMacTitleBar(): Promise<typeof TitleBar> {
		const { TitleBar: MacTitleBar } = await loadTitleBarFor(MAC_UA);
		return MacTitleBar;
	}

	it("hides the Windows-style minimize/maximize/close buttons on macOS (native traffic lights instead)", async () => {
		const MacTitleBar = await loadMacTitleBar();
		renderWithProviders(
			<MacTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		expect(screen.queryByLabelText("Minimize")).toBeNull();
		expect(screen.queryByLabelText("Maximize")).toBeNull();
		expect(screen.queryByLabelText("Close")).toBeNull();
		// The rest of the bar content stays.
		expect(screen.getByLabelText("Toggle sidebar (Ctrl+B)")).toBeTruthy();
		expect(screen.getByLabelText("Help Overlay")).toBeTruthy();
	});
});

describe("TitleBar, XA-1 (focus-ring parity + sidebar-toggle hover)", () => {
	// NOTE: this block uses the STATIC `TitleBar` import, which was
	// resolved at module load with jsdom's DEFAULT UA, Linux, so
	// the close button here renders with the neutral (non-red) hover.
	// That's fine: every assertion in this block is platform-neutral
	// (focus rings, sidebar-toggle hover parity). If a future test
	// adds a Windows-specific close-hover assertion, put it in the
	// Windows block above, NOT here.
	beforeEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	afterEach(() => {
		cleanup();
		(window as unknown as { window_?: unknown }).window_ = undefined;
	});

	it("XA-1: sidebar-toggle button has rounded + transition + hover:bg-foreground/5 (parity with back/forward/help)", () => {
		renderWithProviders(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		const cls = toggle.className;
		// rounded corners + a hover background. The fix brings it in
		// line with its sibling back/forward/help buttons.
		expect(cls).toContain("rounded");
		expect(cls).toContain("transition-colors");
		expect(cls).toContain("duration-150");
		expect(cls).toContain("hover:bg-foreground/5");
	});

	it("XA-1: all four TitleBar icon buttons use the shared focusRing (ring-1, not ring-1)", () => {
		renderWithProviders(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const toggle = screen.getByLabelText("Toggle sidebar (Ctrl+B)");
		const back = screen.getByLabelText("Go back");
		const forward = screen.getByLabelText("Go forward");
		const help = screen.getByLabelText("Help Overlay");
		for (const btn of [toggle, back, forward, help]) {
			const cls = btn.className;
			// Design-system Button uses ring-1; TitleBar matches it via the shared focusRing constant. The ring uses the full-opacity ring-ring token —
			// not ring-ring/30 (see focus-ring-contrast.test.tsx: the 30%
			// alpha ring failed WCAG 2.4.7 focus-visible contrast and was
			// replaced repo-wide with the full-opacity token).
			expect(cls).toContain("focus-visible:ring-1");
			expect(cls).toContain("focus-visible:ring-ring");
			expect(cls).not.toContain("focus-visible:ring-3");
		}
	});

	it("XA-1: window-control TitleBarButtons use ring-1focus ring (matches Button)", () => {
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const minimize = screen.getByLabelText("Minimize");
		const maximize = screen.getByLabelText("Maximize");
		const close = screen.getByLabelText("Close");
		for (const btn of [minimize, maximize, close]) {
			const cls = btn.className;
			expect(cls).toContain("focus-visible:ring-1");
			expect(cls).not.toContain("focus-visible:ring-3");
		}
	});
});

describe("TitleBar, theme control (icon-only, moved from sidebar)", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		vi.resetModules();
	});

	it("renders a theme icon button in the title bar (icon-only, no visible text label)", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		// The theme button is reachable by its aria-label (same wording
		// as the sidebar ThemeSwitch used: "Current theme: Light. Click
		// to switch to Dark.").
		const themeBtn = screen.getByLabelText(
			"Current theme: Light. Click to switch to Dark.",
		);
		expect(themeBtn).toBeTruthy();
		// "Dark", or "System" is gone.
		expect(themeBtn.textContent).not.toMatch(/Light|Dark|System/);
	});

	it("theme button aria-label and title update when themeMode changes", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const { rerender } = renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		expect(
			screen.getByLabelText("Current theme: Light. Click to switch to Dark."),
		).toBeTruthy();

		// rerender replaces the ROOT element, so the TooltipProvider
		// wrapper must be re-applied (the bar renders real Radix
		// Tooltips via HotkeyTooltip).
		rerender(
			<TooltipProvider delayDuration={200} skipDelayDuration={500}>
				<WinTitleBar
					onToggleSidebar={() => {}}
					isMaximized={false}
					onOpenHelp={() => {}}
					themeMode="dark"
					onThemeChange={() => {}}
				/>
			</TooltipProvider>,
		);
		expect(
			screen.getByLabelText("Current theme: Dark. Click to switch to System."),
		).toBeTruthy();
	});

	it("clicking the theme button calls onThemeChange with the next mode", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const onThemeChange = vi.fn();
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={onThemeChange}
			/>,
		);
		const themeBtn = screen.getByLabelText(
			"Current theme: Light. Click to switch to Dark.",
		);
		themeBtn.click();
		expect(onThemeChange).toHaveBeenCalledTimes(1);
		expect(onThemeChange).toHaveBeenCalledWith("dark");
	});

	it("theme button is positioned before the minimize/close window controls (on Windows)", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const themeBtn = screen.getByLabelText(
			"Current theme: Light. Click to switch to Dark.",
		);
		const minimizeBtn = screen.getByLabelText("Minimize");
		// The theme button must be a previous sibling of the minimize
		// button (in the same parent container, the drag-region bar).
		const bar = themeBtn.closest(".drag-region");
		expect(bar).toBeTruthy();
		if (bar) {
			const allButtons = Array.from(bar.querySelectorAll("[aria-label]"));
			const themeIdx = allButtons.indexOf(themeBtn);
			const minIdx = allButtons.indexOf(minimizeBtn);
			expect(themeIdx).toBeGreaterThanOrEqual(0);
			expect(minIdx).toBeGreaterThan(themeIdx);
		}
	});

	it("theme button carries the no-drag class so it does not interfere with window dragging", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const themeBtn = screen.getByLabelText(
			"Current theme: Light. Click to switch to Dark.",
		);
		expect(themeBtn.className).toContain("no-drag");
	});
});

describe("TitleBar, Tauri drag region (data-tauri-drag-region)", () => {
	afterEach(() => {
		delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
		cleanup();
	});

	it("renders WITHOUT data-tauri-drag-region under jsdom (attribute is Tauri-only)", () => {
		const { container } = renderWithProviders(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		// predecessor relies on the `-webkit-app-region: drag` CSS class
		// (index.css `.drag-region`); the `data-tauri-drag-region`
		// attribute must NOT be present.
		expect(bar?.getAttribute("data-tauri-drag-region")).toBeNull();
	});

	it("renders data-tauri-drag-region on the bar inside the Tauri webview", () => {
		// Simulate the Tauri global injected when `withGlobalTauri: true`
		// (tauri.conf.json), `isTauri()` returns true iff
		// `window.__TAURI__.core.invoke` exists (tauri-bridge/detect.ts).
		(window as unknown as { __TAURI__?: unknown }).__TAURI__ = {
			core: { invoke: vi.fn() },
		};
		const { container } = renderWithProviders(
			<TitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		// The attribute marks the bar as a drag region for Tauri's
		// drag-region handling. It applies ONLY to the element it sits
		// on (not children), so the window-control / sidebar buttons
		// inside the bar stay clickable.
		expect(bar?.getAttribute("data-tauri-drag-region")).toBe("");
	});
});
