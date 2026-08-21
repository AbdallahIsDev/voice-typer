/**
 *  vitest suite — covers  for the TitleBar component.
 *
 * - : window-control icons render at `size-2.5` (10px) — the native
 *   Windows glyph proportions — and use
 *   `text-(--text-primary) dark:text-white` (PURE #fff white in dark
 *   mode, where the theme presets would otherwise tint the glyphs
 *   off-white/gray via --text-primary → --foreground). The MINIMIZE
 *   bar is a SOLID 1px FILLED rect (no stroke — a 0.5px stroked line
 *   is sub-pixel at 10px display and antialiases to ~50% white =
 *   gray); maximize/restore stroke 0.5px; the close X strokes 1px
 *   (deliberately heavier so it reads clearly on the red hover
 *   background).
 * - : the WHOLE title bar is FOCUS-AWARE: full brightness while the
 *   window is focused; while unfocused the bar CONTAINER drops to
 *   `opacity-60`, dimming every element (sidebar/back/forward/help +
 *   window controls) uniformly. Opacity (not a dim color) is
 *   theme-agnostic — it scales whatever colors the active theme
 *   resolves — so light/dark/custom themes are all safe and the
 *   pure-white glyph pins stay untouched. Driven by the DOM
 *   `focus`/`blur` events on `window` (no IPC; fires in both Electron
 *   Chromium and the Tauri webviews).
 * - : the close button hover is PLATFORM-CONVENTION-DEPENDENT:
 *   Windows uses the native red (`hover:bg-[#e81123]` + `dark:`
 *   twins, red in EVERY theme), while Linux (GNOME/KDE) uses the same
 *   neutral hover as minimize/maximize — never red. The dedicated
 *   `IS_WIN-pinned` / `IS_LINUX-pinned` tests assert BOTH halves of
 *   the coupling: the UA→constant derivation AND the close-variant
 *   gate (`IS_WIN ? "close" : "default"`), using the constants from
 *   the same fresh module registry the loaded component saw.
 * - : the MINIMIZE glyph shape is PLATFORM-SPECIFIC: Windows draws a
 *   horizontal bar (`<line>` at y=5), while Linux/GNOME draws a
 *   filled dot (`<circle>` at cx/cy=5). Maximize/restore/close share
 *   identical geometry on both platforms.
 * - : aria-keyshortcuts="Control+B" on the sidebar toggle.
 * - : aria-keyshortcuts="?" on the help button.
 *
 * Platform note: `IS_WIN` / `IS_LINUX` / `IS_MAC` are module-load
 * constants derived from `navigator.userAgent` (hotkey-utils.ts).
 * jsdom's DEFAULT UA is Linux, so the Windows assertions below stub a
 * Windows UA and re-import the module (same pattern as the macOS
 * block). The static `TitleBar` import used by the XA-1 block runs on
 * the jsdom Linux UA, which is fine — those assertions are
 * platform-neutral (focus rings, hover parity of non-window-control
 * buttons).
 *
 * The WindowBridge is stubbed so the component can mount in jsdom
 * without the Electron preload present.
 */
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
// ancestor — the app shell provides one (App.tsx:475). Same props as
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

/**
 * Load a fresh TitleBar module with the given platform UA stubbed.
 * `IS_WIN` / `IS_LINUX` / `IS_MAC` are module-load constants computed
 * from `navigator.userAgent`, so the module cache must be wiped and
 * the component re-imported for the constants to re-evaluate.
 *
 * The returned object ALSO carries the platform constants, imported
 * from the same fresh registry (both imports run after the same
 * `vi.resetModules()` call), so tests can pin the rendered behavior
 * to the exact `IS_WIN` / `IS_LINUX` / `IS_MAC` values the loaded
 * component instance resolved.
 */
async function loadTitleBarFor(ua: string): Promise<LoadedTitleBar> {
	vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(ua);
	vi.resetModules();
	// Fresh module instances — hotkey-utils re-evaluates its
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

describe("TitleBar — Windows window controls (red close hover)", () => {
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
		// Pin the platform constants FIRST — the red hover below is
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

	it("close button hover uses the native Windows red + white glyph (light AND dark)", async () => {
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
		expect(closeBtn).toBeTruthy();
		const cls = closeBtn.className;
		// Native Windows close hover: solid red bg + pure-white X,
		// applied on hover/focus ONLY (neutral at rest). The `dark:`
		// twins are REQUIRED: without them, the shared Button ghost
		// variant's `dark:hover:bg-muted/50` (specificity 0-3-0)
		// beats the plain `hover:bg-[#e81123]` (0-2-0) in dark mode,
		// so the close button hovered gray instead of red.
		expect(cls).toContain("hover:bg-[#e81123]");
		expect(cls).toContain("hover:text-white");
		expect(cls).toContain("dark:hover:bg-[#e81123]");
		expect(cls).toContain("dark:hover:text-white");
		expect(cls).toContain("focus-visible:bg-[#e81123]");
		expect(cls).toContain("focus-visible:text-white");
		expect(cls).toContain("dark:focus-visible:bg-[#e81123]");
		expect(cls).toContain("dark:focus-visible:text-white");
		expect(cls).toContain("active:bg-[#c42b1c]");
		expect(cls).toContain("dark:active:bg-[#c42b1c]");
		// twMerge must DEDUPE the ghost variant's gray dark-hover
		// (`dark:hover:bg-muted/50`) against our dark-red twin — if
		// both classes stayed in the DOM, CSS source order (not
		// specificity, which is equal at 0-3-0) would decide which
		// wins, and the close button could hover gray again.
		expect(cls).not.toContain("dark:hover:bg-muted");
		expect(cls).not.toContain("hover:bg-muted");
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
		// `focus-visible:bg-[#e81123]` — never as a standalone
		// (resting) `bg-[#e81123]` class or any theme-tinted red wash.
		expect(cls).toContain("hover:bg-[#e81123]");
		expect(cls).not.toMatch(/(^|\s)bg-\[#e81123\](\s|$)/);
		expect(cls).not.toContain("bg-destructive/10");
		expect(cls).not.toContain("bg-destructive/20");
		expect(cls).not.toContain("dark:bg-destructive/20");
	});

	it("window-control icons pin size-2.5 so the shared Button 16px svg rule cannot stretch them", async () => {
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
		// Minimize + Maximize + Close icons live inside the three
		// window-control buttons (data-slot="button" marks the shared
		// Button). Each glyph svg must carry the explicit size-2.5
		// (10px) class; otherwise the Button base rule
		// `[&_svg:not([class*='size-'])]:size-4` inflates the 10x10
		// glyphs to 16px.
		const svgs = container.querySelectorAll(
			'[data-slot="button"] svg[aria-hidden="true"]',
		);
		expect(svgs.length).toBe(3);
		for (const svg of svgs) {
			expect(svg.getAttribute("class")).toContain("size-2.5");
		}
	});

	it("window-control glyph strokes: minimize SOLID fill, maximize 0.5px, close 1px", async () => {
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
		// Minimize is a SOLID FILLED bar — NO stroke at all (a 0.5px
		// stroked line is sub-pixel at 10px display and renders ~50%
		// white = gray; the fill renders full currentColor). Maximize
		// keeps the 0.5px stroke; the close X is deliberately heavier
		// (1px) per user request — it reads more clearly inside the
		// red hover background.
		const minSvg = screen
			.getByLabelText("Minimize")
			.querySelector("svg[aria-hidden='true']");
		expect(minSvg?.getAttribute("stroke-width")).toBeNull();
		expect(minSvg?.getAttribute("class")).toContain("fill-current");
		expect(minSvg?.getAttribute("class")).not.toContain("stroke-current");
		expect(
			screen
				.getByLabelText("Maximize")
				.querySelector("svg[aria-hidden='true']")
				?.getAttribute("stroke-width"),
		).toBe("0.5");
		expect(
			screen
				.getByLabelText("Close")
				.querySelector("svg[aria-hidden='true']")
				?.getAttribute("stroke-width"),
		).toBe("1");
	});

	it("restore glyph (maximized state) also uses the 0.5px stroke", async () => {
		const { TitleBar: WinTitleBar } = await loadTitleBarFor(WIN_UA);
		const bridge = makeBridge();
		(window as unknown as { window_?: WindowBridge }).window_ = bridge;
		renderWithProviders(
			<WinTitleBar
				onToggleSidebar={() => {}}
				isMaximized={true}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		// Maximized → the middle button becomes "Restore" and renders
		// the two-box RestoreIcon — its stroke must match the 0.5px
		// weight of the other window-control glyphs.
		const restoreBtn = screen.getByLabelText("Restore");
		const svg = restoreBtn.querySelector('svg[aria-hidden="true"]');
		expect(svg).toBeTruthy();
		expect(svg?.getAttribute("stroke-width")).toBe("0.5");
	});

	it("minimize glyph is a SOLID 1px filled BAR on Windows (no stroke, full-opacity fill)", async () => {
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
		// SOLID FILLED bar (rect spanning y 4.5-5.5 = 1px, centered at
		// y=5) — NOT a stroked <line>. A solid fill is solid mass: it
		// renders the full currentColor (pure #fff in dark mode) at
		// any DPI, with no sub-pixel antialiasing and no opacity.
		const bar = minBtn.querySelector("rect");
		expect(bar).toBeTruthy();
		expect(bar?.getAttribute("y")).toBe("4.5");
		expect(bar?.getAttribute("height")).toBe("1");
		expect(bar?.getAttribute("width")).toBe("10");
		// No stroke-based line must be present.
		expect(minBtn.querySelector("line")).toBeNull();
		const svg = minBtn.querySelector("svg[aria-hidden='true']");
		expect(svg?.getAttribute("class")).toContain("fill-current");
		expect(svg?.getAttribute("class")).not.toContain("stroke-current");
		expect(svg?.getAttribute("stroke-width")).toBeNull();
	});

	it("window-control buttons pin pure white glyphs in dark mode (dark:text-white)", async () => {
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
		// --text-primary aliases --foreground, which theme presets
		// (Nord/Dracula/Tokyo Night/...) tint off-white (L 0.90-0.92)
		// in dark mode — so the glyphs rendered gray. `dark:text-white`
		// pins the dark-mode glyph to true #fff.
		for (const label of ["Minimize", "Maximize", "Close"]) {
			const cls = screen.getByLabelText(label).className;
			expect(cls).toContain("text-(--text-primary)");
			expect(cls).toContain("dark:text-white");
		}
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
		// theme's own colors — theme-agnostic, safe in light/dark/
		// custom themes) rather than a per-glyph dim color.
		const bar = container.querySelector(".drag-region");
		expect(bar).toBeTruthy();
		// Focused by default → no dim.
		expect(bar?.className).not.toContain("opacity-60");
		// Window loses focus (user clicked another app, e.g. VS Code) →
		// the WHOLE bar dims — every element (sidebar/back/forward/help
		// + all three window controls) — while the pure-white glyph
		// color pins stay untouched underneath.
		act(() => {
			window.dispatchEvent(new Event("blur"));
		});
		expect(bar?.className).toContain("opacity-60");
		const minBtn = screen.getByLabelText("Minimize");
		const maxBtn = screen.getByLabelText("Maximize");
		const closeBtn = screen.getByLabelText("Close");
		for (const btn of [minBtn, maxBtn, closeBtn]) {
			expect(btn.className).toContain("text-(--text-primary)");
			expect(btn.className).toContain("dark:text-white");
		}
		// Window regains focus → dim removed, full brightness.
		act(() => {
			window.dispatchEvent(new Event("focus"));
		});
		expect(bar?.className).not.toContain("opacity-60");
	});

	it("close button hover stays red+white even while the window is UNFOCUSED (dim is opacity-only, hover classes untouched)", async () => {
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
		// Dim the window first (container opacity only — the button's
		// own classes are untouched).
		act(() => {
			window.dispatchEvent(new Event("blur"));
		});
		const closeBtn = screen.getByLabelText("Close");
		const cls = closeBtn.className;
		// The dim is container opacity — the button's color classes
		// are intact, so the native Windows red+white close hover
		// still applies (hovering the close button of an unfocused
		// window shows red+white, exactly like Windows 11).
		expect(cls).toContain("dark:text-white");
		expect(cls).toContain("hover:bg-[#e81123]");
		expect(cls).toContain("hover:text-white");
		expect(cls).toContain("dark:hover:bg-[#e81123]");
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
		// The plain-text `title` is gone — the shortcut moved into the
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
		// The accessible name is preserved — aria-label untouched.
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
		// en.json: help.openHelp = "Open this help overlay"
		const helpBtn = screen.getByLabelText("Open this help overlay");
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

describe("TitleBar — Linux window controls (GNOME/KDE neutral close hover)", () => {
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
		// Pin the platform constants FIRST — the neutral hover below
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
		// NO red anywhere — neutral hover because IS_LINUX=true.
		expect(cls).not.toContain("hover:bg-[#e81123]");
		expect(cls).not.toContain("dark:hover:bg-[#e81123]");
		expect(cls).toContain("hover:bg-foreground/5");
		expect(cls).toContain("dark:hover:bg-foreground/5");
	});

	it("close button uses the NEUTRAL hover on Linux — never red (GNOME/KDE convention)", async () => {
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
		const cls = closeBtn.className;
		// GNOME/KDE draw a NEUTRAL close-button hover — no Windows red.
		expect(cls).not.toContain("hover:bg-[#e81123]");
		expect(cls).not.toContain("dark:hover:bg-[#e81123]");
		expect(cls).not.toContain("hover:text-white");
		// The close button must match the minimize/maximize neutral
		// hover exactly (foreground/5 wash, light AND dark).
		const minCls = screen.getByLabelText("Minimize").className;
		expect(cls).toContain("hover:bg-foreground/5");
		expect(cls).toContain("dark:hover:bg-foreground/5");
		expect(minCls).toContain("hover:bg-foreground/5");
		expect(minCls).toContain("dark:hover:bg-foreground/5");
		// Neutral at rest in dark mode — glyph still pure white.
		expect(cls).toContain("dark:text-white");
		expect(cls).not.toMatch(/(^|\s)bg-\[#e81123\](\s|$)/);
	});

	it("minimize glyph is a GNOME-style filled DOT on Linux (not the Windows bar)", async () => {
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
		const minBtn = screen.getByLabelText("Minimize");
		// GNOME/Adwaita minimize = filled dot, NOT the Windows bar.
		const circle = minBtn.querySelector("circle");
		expect(circle).toBeTruthy();
		expect(circle?.getAttribute("cx")).toBe("5");
		expect(circle?.getAttribute("cy")).toBe("5");
		expect(circle?.getAttribute("r")).toBe("2");
		// Dot is filled (fill-current), not a stroked outline — and the
		// Windows bar must NOT be present.
		expect(minBtn.querySelector("line")).toBeNull();
		const svg = minBtn.querySelector("svg[aria-hidden='true']");
		expect(svg?.getAttribute("class")).toContain("fill-current");
	});

	it("maximize/close glyph geometry is SHARED across platforms (only minimize is platform-specific)", async () => {
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
		// GNOME/Adwaita draws maximize as a square outline and close as
		// an X — the SAME geometry as Windows — so only the minimize
		// glyph branches on IS_LINUX. Pin the shared shapes here so a
		// future "fix" that diverges them fails loudly.
		const maxBtn = screen.getByLabelText("Maximize");
		expect(maxBtn.querySelector("rect")).toBeTruthy();
		expect(maxBtn.querySelector("line")).toBeNull();
		const closeBtn = screen.getByLabelText("Close");
		expect(closeBtn.querySelectorAll("line").length).toBe(2);
		expect(closeBtn.querySelector("rect")).toBeNull();
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
describe("TitleBar — macOS native traffic-light mode", () => {
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
		expect(screen.getByLabelText("Open this help overlay")).toBeTruthy();
	});

	it("reserves a traffic-light gutter on macOS so bar buttons don't collide with the dots", async () => {
		const MacTitleBar = await loadMacTitleBar();
		const { container } = renderWithProviders(
			<MacTitleBar
				onToggleSidebar={() => {}}
				isMaximized={false}
				onOpenHelp={() => {}}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);
		const gutters = container.querySelectorAll('[aria-hidden="true"]');
		// w-18 = 72px fixed-width spacer for the OS traffic lights.
		expect([...gutters].some((g) => g.className.includes("w-18"))).toBe(true);
	});
});

describe("TitleBar — XA-1 (focus-ring parity + sidebar-toggle hover)", () => {
	// NOTE: this block uses the STATIC `TitleBar` import, which was
	// resolved at module load with jsdom's DEFAULT UA — Linux — so
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
		// Previously the toggle was the only TitleBar button missing
		// rounded corners + a hover background. The fix brings it in
		// line with its sibling back/forward/help buttons.
		expect(cls).toContain("rounded");
		expect(cls).toContain("transition-colors");
		expect(cls).toContain("duration-150");
		expect(cls).toContain("hover:bg-foreground/5");
	});

	it("XA-1: all four TitleBar icon buttons use the shared focusRing (ring-3, not ring-2)", () => {
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
			expect(cls).toContain("focus-visible:ring-3");
			expect(cls).not.toContain("focus-visible:ring-2");
		}
	});
});

describe("TitleBar — theme control (icon-only, moved from sidebar)", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		vi.resetModules();
	});

	it("renders a theme icon button in the title bar (icon-only — no visible text label)", async () => {
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
		// No visible text label — the span that used to say "Light",
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
		// button (in the same parent container — the drag-region bar).
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
