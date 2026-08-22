import { PanelLeftIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useEffect, useState } from "react";
import { HotkeyTooltip } from "@/components/hotkey/HotkeyTooltip";
import { IS_LINUX, IS_MAC, IS_WIN } from "@/components/hotkey/hotkey-utils";
import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { ThemeSwitch } from "@/components/layout/ThemeSwitch";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { WindowBridge } from "@/types/ipc";

// Focus-aware title bar: native OS title bars DIM their whole bar
// while the window is unfocused (the user clicked another app — e.g.
// VS Code — so the window is visible but inactive), and restore it to
// full brightness on refocus. We mirror that using the DOM
// `focus`/`blur` events on `window`, which fire in BOTH runtimes with
// zero IPC / main-process code: Electron's Chromium, and the Tauri
// webviews (WebView2 on Windows, WKWebView on macOS, WebKitGTK on
// Linux). The dim is CONTAINER OPACITY (see TitleBarInner) — not a
// specific dim color — so it scales whatever colors the active theme
// resolves and cannot break light/dark/custom themes.
function useWindowFocused(): boolean {
	// Start FOCUSED: a window opens focused, and the blur/focus events
	// below are the source of truth from there on. Deliberately NOT
	// synced from `document.hasFocus()` at mount — that call returns
	// false in jsdom and varies across webviews, which would start the
	// glyphs dimmed in tests; the event-driven state is deterministic.
	const [focused, setFocused] = useState(true);
	useEffect(() => {
		if (typeof window === "undefined") return;
		const onFocus = () => setFocused(true);
		const onBlur = () => setFocused(false);
		window.addEventListener("focus", onFocus);
		window.addEventListener("blur", onBlur);
		return () => {
			window.removeEventListener("focus", onFocus);
			window.removeEventListener("blur", onBlur);
		};
	}, []);
	return focused;
}

interface TitleBarProps {
	onToggleSidebar?: () => void;
	onGoBack?: () => void;
	onGoForward?: () => void;
	canGoBack?: boolean;
	canGoForward?: boolean;
	//(session-6 dedup): ``isMaximized`` is now a REQUIRED prop.
	// Previously TitleBar had its own local ``useState`` +
	// ``onMaximizedChanged`` subscription as a fallback for when the
	// prop was undefined — duplicating the subscription already living
	// in App.tsx (App.tsx:161-189) which always passes the prop. The
	// duplicate subscription is deleted; App.tsx is the single owner of
	// the maximize-state subscription.
	isMaximized: boolean;
	//open the keyboard-shortcut help overlay. Previously the
	// overlay was only reachable via the "?" key — invisible to users
	// who never discover that shortcut. Exposing a TitleBar button
	// makes the overlay discoverable for mouse + keyboard users alike.
	onOpenHelp?: () => void;
	// THEME CONTROL LIVES IN THE TITLE BAR (moved out of the sidebar).
	// The icon-only theme button sits in the window-control cluster so
	// it is part of the bar's control language. App.tsx passes the
	// SAME state + change handler the sidebar's ThemeSwitch used — no
	// second theme implementation.
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
}

// Shared window-control glyph style. NOTE: the shared <Button>
// component applies ``[&_svg:not([class*='size-'])]:size-4``, which
// would stretch these 10x10 viewBox glyphs to 16px — so each icon
// pins ``size-2.5`` (10px) to keep the glyphs small and crisp inside
// the 46x32 window-control buttons. Stroke weights: maximize/restore
// use ``strokeWidth=0.5`` (0.5 units in a 10-unit viewBox = 0.5px at
// 10px display) and the close X is 1px; the MINIMIZE bar is a SOLID
// 1px FILLED rect — never a stroked line — because a 0.5px stroke is
// sub-pixel at 10px display size and antialiasing renders it ~50%
// white, i.e. exactly the "toned-down / gray" glyph the user saw.
// The icons are purely decorative (the wrapping button carries the
// aria-label), so ``aria-hidden`` with NO child <title> (a <title>
// inside an aria-hidden SVG is dropped by screen readers AND
// duplicates the button's label).
//
// Platform-specific glyph shapes: the MINIMIZE glyph is the only
// shape that differs between Windows and Linux. Windows draws a
// horizontal bar; GNOME/Adwaita draws a filled dot. Maximize (square
// outline), restore (two squares) and close (X) use the same
// geometry on both platforms, so only minimize branches on
// ``IS_LINUX``.
function MinimizeIcon() {
	return IS_LINUX ? <LinuxMinimizeIcon /> : <WindowsMinimizeIcon />;
}

/** Windows minimize glyph: SOLID 1px filled bar, centered at y=5. */
function WindowsMinimizeIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 fill-current"
		>
			{/* SOLID FILLED bar — deliberately NOT a stroked line. A 0.5px
			    stroke at 10px display size is half a device pixel, so
			    antialiasing rendered it ~50% white = the gray / toned-down
			    minimize glyph. A solid 1px fill is solid mass: it renders
			    the full currentColor (pure #fff in dark mode) at ANY DPI
			    with no opacity applied. Centered at y=5 (rect spans
			    4.5-5.5) so the bar sits at the vertical center of the
			    46x32 button. */}
			<rect x="0" y="4.5" width="10" height="1" />
		</svg>
	);
}

/** GNOME/Adwaita minimize glyph: filled dot centered in the viewBox. */
function LinuxMinimizeIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 fill-current"
		>
			{/* Filled dot (r=2 → 4px diameter at 10px display), centered
			    at (5,5) — the GNOME/Adwaita minimize glyph, which is a
			    dot rather than the Windows bar. Matches the 0.5px visual
			    weight of the sibling stroke glyphs (4px solid dot reads
			    as roughly the same stroke mass as the 9-unit box). */}
			<circle cx="5" cy="5" r="2" />
		</svg>
	);
}

function MaximizeIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 stroke-current fill-none"
			strokeWidth="0.5"
		>
			<rect x="0.5" y="0.5" width="9" height="9" />
		</svg>
	);
}

function RestoreIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 stroke-current fill-none"
			strokeWidth="0.5"
		>
			<path d="M3 0.5 H9.5 V7" />
			<rect x="0.5" y="2.5" width="7" height="7" />
		</svg>
	);
}

function CloseIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 stroke-current"
			strokeWidth="1"
		>
			<line x1="0.5" y1="0.5" x2="9.5" y2="9.5" />
			<line x1="9.5" y1="0.5" x2="0.5" y2="9.5" />
		</svg>
	);
}

interface TitleBarButtonProps {
	onClick: () => void;
	ariaLabel: string;
	variant?: "default" | "close";
	children: React.ReactNode;
}

function TitleBarButton({
	onClick,
	ariaLabel,
	variant = "default",
	children,
}: TitleBarButtonProps) {
	// Red close hover is a WINDOWS convention (the native Windows
	// close button turns solid red on hover). GNOME/KDE draw a
	// neutral hover for the close button, so on Linux the close
	// button must NOT go red — it uses the same neutral hover as
	// minimize/maximize. The caller passes the resolved variant
	// (`IS_WIN ? "close" : "default"`); this component just applies
	// the styling for whichever variant it receives.
	const isClose = variant === "close";
	//migrate from raw <button> to the shared <Button> component
	// so the design-system contract (focus ring, cva variant tokens,
	// active:translate-y-px) is applied uniformly. The cva default
	// (rounded-4xl, outline-hidden, border-transparent) is overridden
	// via className to match the title-bar's edge-to-edge framing —
	// no rounded corners, no visible border, fixed 8x11.5 sizing.
	// NOTE: deliberately NOT `asChild` — the children are bare SVG icon
	// components, and `Slot.Root` would merge the click handler +
	// aria-label onto the <svg> itself, replacing the <button> element
	// entirely (breaking keyboard activation, screen-reader semantics,
	// and `getByRole("button")`). A real <button> wrapping the icon is
	// required.
	//
	// The close button uses the `ghost` base + a native-Windows-red
	// HOVER (matching platform convention: neutral at rest, red on
	// hover/focus) rather than the `destructive` cva variant, whose
	// resting state paints a `bg-destructive/10` red tint over the
	// whole hit target.
	return (
		<Button
			type="button"
			variant="ghost"
			size="icon-sm"
			onClick={onClick}
			aria-label={ariaLabel}
			className={cn(
				"no-drag",
				"h-8 w-11.5 rounded-none border-0",
				// Window-control glyphs are PURE WHITE in dark mode and
				// near-black in light mode, mirroring native title bars.
				// `text-(--text-primary)` alone is NOT enough: in dark
				// mode --text-primary aliases --foreground, which the
				// theme presets (Nord/Dracula/Tokyo Night/...) tint
				// off-white (L 0.90-0.92), so the glyphs rendered gray
				// instead of white. `dark:text-white` pins the dark-mode
				// glyph to true #fff regardless of preset/custom theme.
				// NOTE: the unfocused-window DIM does NOT live here — it's
				// applied as container opacity on the whole bar (see
				// TitleBarInner), which dims every element uniformly and
				// cannot clash with theme colors.
				"text-(--text-primary) dark:text-white transition-colors duration-150",
				isClose
					? cn(
							// Native Windows close-button hover: solid red
							// background + pure-white glyph, in EVERY theme
							// (the OS close button ignores the app palette).
							// #e81123 is the canonical Windows accent red;
							// pinned here deliberately instead of the
							// theme-tinted --destructive token (which is a
							// lighter orange-red and can be overridden by
							// custom themes). Neutral at rest; press
							// darkens to #c42b1c like the OS button.
							// The dark: twins are REQUIRED: the shared
							// Button `ghost` variant adds
							// `dark:hover:bg-muted/50`, whose rule
							// (`.dark .dark\:hover\:bg-muted\/50:hover`,
							// specificity 0-3-0) beats the plain
							// `hover:bg-[#e81123]` (0-2-0) in dark mode —
							// the close button hovered GRAY instead of red.
							// Adding the same `dark:` modifier lets twMerge
							// dedupe ghost's gray hover away entirely.
							"hover:bg-[#e81123] hover:text-white",
							"dark:hover:bg-[#e81123] dark:hover:text-white",
							"focus-visible:bg-[#e81123] focus-visible:text-white",
							"dark:focus-visible:bg-[#e81123] dark:focus-visible:text-white",
							"active:bg-[#c42b1c] active:text-white",
							"dark:active:bg-[#c42b1c] dark:active:text-white",
						)
					: "hover:bg-foreground/5 dark:hover:bg-foreground/5",
			)}
		>
			{children}
		</Button>
	);
}

function TitleBarInner({
	onToggleSidebar,
	onGoBack,
	onGoForward,
	canGoBack,
	canGoForward,
	isMaximized,
	onOpenHelp,
	themeMode,
	onThemeChange,
}: TitleBarProps) {
	// When the window is unfocused (user clicked another app), the
	// WHOLE title bar dims like native OS title bars (container
	// opacity below).
	const windowFocused = useWindowFocused();
	const bridge =
		typeof window !== "undefined"
			? (window.window_ as WindowBridge)
			: undefined;

	const handleMinimize = () =>
		bridge
			?.minimize()
			.catch((err) =>
				console.warn(
					"[renderer:TitleBar] window control failed: minimize:",
					err,
				),
			);
	const handleToggleMaximize = () =>
		bridge
			?.toggleMaximize()
			.catch((err) =>
				console.warn(
					"[renderer:TitleBar] window control failed: toggleMaximize:",
					err,
				),
			);
	const handleClose = () =>
		bridge
			?.close()
			.catch((err) =>
				console.warn("[renderer:TitleBar] window control failed: close:", err),
			);

	return (
		<div
			// PHYSICAL-SIDE PINNING: the bar is forced LTR so the window
			// chrome keeps its platform-conventional geometry regardless
			// of the document direction. Under dir=rtl a plain flex row
			// would mirror the whole bar — the macOS traffic-light gutter
			// would flip to the right edge and the Windows/Linux
			// minimize/maximize/close cluster to the left, both wrong:
			// native window controls never move when the UI language is
			// RTL. With dir="ltr" the gutter stays physically left, the
			// control cluster physically right, in every locale. Content
			// inside the buttons is icon-only (no text to mirror); the
			// Back/Forward chevrons opt INTO mirroring via the
			// `nav-directional-icon` class, whose [dir="rtl"] ancestor
			// selector still matches through this container.
			dir="ltr"
			className={cn(
				"drag-region flex w-full shrink-0 items-center select-none h-8 transition-opacity duration-150",
				// Native OS title bars dim the WHOLE bar while the window
				// is unfocused — every element (sidebar toggle, back,
				// forward, help, and all three window controls) tones
				// down together. Opacity is THE theme-agnostic dim: it
				// scales whatever colors the active theme resolves
				// (light/dark/custom), so the pure-white glyph pins and
				// hover colors stay intact underneath and nothing can
				// "break" under a custom theme.
				!windowFocused && "opacity-60",
			)}
		>
			{/* macOS traffic-light gutter: the native red/yellow/green dots
			    are drawn by the OS at trafficLightPosition x:12 spanning
			    ~52px — reserve 72px so the bar's buttons never collide
			    with them. Windows/Linux don't need it (their window
			    controls are the custom ones on the right). */}
			{IS_MAC && <div className="h-8 w-18 shrink-0" aria-hidden="true" />}
			<HotkeyTooltip
				label={t("a11y.toggleSidebar")}
				keys={SHORTCUTS.toggleSidebar.keys}
			>
				<button
					type="button"
					onClick={onToggleSidebar}
					aria-label={t("a11y.toggleSidebarWithShortcut", {
						shortcut: SHORTCUTS.toggleSidebar.keys,
					})}
					//expose the keyboard shortcut via aria-keyshortcuts
					// so AT users can discover it without inspecting the
					// tooltip. Sourced from the SHORTCUTS catalog so the
					// attribute can't drift from the tooltip chips.
					aria-keyshortcuts={SHORTCUTS.toggleSidebar.ariaKeyshortcuts}
					className={cn(
						// Fix: sidebar toggle button height matches the TitleBar
						// h-8 so the icon stays vertically centered (was h-10
						// w-10 which made it taller than the 32px title bar).
						"no-drag press-scale flex h-8 w-8 items-center justify-center",
						"text-(--text-muted)",
						//parity with sibling back/forward/help buttons —
						// add rounded corners + transition + hover bg so the
						// toggle snaps in consistently with its neighbors
						// (previously the only TitleBar button without a hover bg).
						"rounded transition-colors duration-150",
						"hover:bg-foreground/5 hover:text-(--text-primary)",
						focusRing,
					)}
				>
					<HugeiconsIcon
						icon={PanelLeftIcon}
						strokeWidth={2}
						className="h-4 w-4"
					/>
				</button>
			</HotkeyTooltip>

			{/* Back/Forward navigation */}
			<HotkeyTooltip label={t("titleBar.back")} keys={SHORTCUTS.navBack.keys}>
				<button
					type="button"
					onClick={onGoBack}
					disabled={!canGoBack}
					aria-label={t("a11y.goBack")}
					className={cn(
						"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
						"text-(--text-muted) transition-colors duration-150",
						// task-9: theme-aware hover (replaces the physical
						// black/white pairing so custom + dark themes get a
						// consistent hover wash).
						"hover:bg-foreground/5 hover:text-(--text-primary)",
						"disabled:opacity-30 disabled:cursor-not-allowed",
						focusRing,
					)}
				>
					<svg
						width="16"
						height="16"
						viewBox="0 0 16 16"
						fill="none"
						aria-hidden="true"
						// RTL: the back chevron points "back" — mirrored under
						// dir=rtl by the shared index.css rule so it points the
						// semantically correct way for right-to-left locales.
						className="nav-directional-icon"
					>
						<path
							d="M10 12L6 8L10 4"
							stroke="currentColor"
							strokeWidth="1.5"
							strokeLinecap="round"
							strokeLinejoin="round"
						/>
					</svg>
				</button>
			</HotkeyTooltip>
			<HotkeyTooltip
				label={t("titleBar.forward")}
				keys={SHORTCUTS.navForward.keys}
			>
				<button
					type="button"
					onClick={onGoForward}
					disabled={!canGoForward}
					aria-label={t("a11y.goForward")}
					className={cn(
						"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
						"text-(--text-muted) transition-colors duration-150",
						"hover:bg-foreground/5 hover:text-(--text-primary)",
						"disabled:opacity-30 disabled:cursor-not-allowed",
						focusRing,
					)}
				>
					<svg
						width="16"
						height="16"
						viewBox="0 0 16 16"
						fill="none"
						aria-hidden="true"
						// RTL: the forward chevron points "forward" — mirrored
						// under dir=rtl by the shared index.css rule.
						className="nav-directional-icon"
					>
						<path
							d="M6 4L10 8L6 12"
							stroke="currentColor"
							strokeWidth="1.5"
							strokeLinecap="round"
							strokeLinejoin="round"
						/>
					</svg>
				</button>
			</HotkeyTooltip>

			{/*discoverable "?" help button. Mirrors the "?"
                            keyboard shortcut (handled in App.tsx) so mouse users and
                            AT users can also open the keyboard-shortcut overlay. */}
			<HotkeyTooltip label={t("help.openHelp")} keys={SHORTCUTS.openHelp.keys}>
				<button
					type="button"
					onClick={onOpenHelp}
					aria-label={t("help.openHelp")}
					//expose the "?" shortcut via aria-keyshortcuts so
					// AT users can discover that pressing "?" opens this overlay.
					aria-keyshortcuts={SHORTCUTS.openHelp.ariaKeyshortcuts}
					className={cn(
						"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
						"text-(--text-muted) transition-colors duration-150",
						"hover:bg-foreground/5 hover:text-(--text-primary)",
						focusRing,
					)}
				>
					<span
						aria-hidden
						className="text-[0.8125rem] font-semibold leading-none"
					>
						?
					</span>
				</button>
			</HotkeyTooltip>

			<div className="flex-1" />

			{/* Theme control — icon-only, sitting in the window-control
			    cluster. On Windows/Linux it renders immediately LEFT of
			    minimize/maximize/close so it reads as part of the same
			    control group (matching their h-8 height, `no-drag`
			    exemption, and neutral hover wash). On macOS the native
			    traffic lights occupy the left gutter and there are no
			    window controls — the button anchors the bar's right
			    edge instead, so the theme switcher stays reachable on
			    every platform. The accessible name + hover title carry
			    the current→next theme info (no visible text). */}
			<ThemeSwitch
				themeMode={themeMode}
				onThemeChange={onThemeChange}
				className={cn(
					"no-drag press-scale h-8 w-8 rounded",
					"text-(--text-muted) hover:text-(--text-primary)",
				)}
			/>

			{/* Window controls — Windows/Linux only. macOS uses the native
			    traffic lights (titleBarStyle: 'hiddenInset' in the main
			    window), so rendering Windows-style buttons there would
			    duplicate the chrome with wrong-style buttons. */}
			{!IS_MAC && (
				<>
					<TitleBarButton
						onClick={handleMinimize}
						ariaLabel={t("titleBar.minimize")}
					>
						<MinimizeIcon />
					</TitleBarButton>
					<TitleBarButton
						onClick={handleToggleMaximize}
						ariaLabel={
							isMaximized ? t("titleBar.restore") : t("titleBar.maximize")
						}
					>
						{isMaximized ? <RestoreIcon /> : <MaximizeIcon />}
					</TitleBarButton>
					<TitleBarButton
						onClick={handleClose}
						ariaLabel={t("titleBar.close")}
						// Red close hover is a Windows convention; Linux
						// (GNOME/KDE) uses a neutral hover for the close
						// button, so only Windows gets the "close" variant.
						variant={IS_WIN ? "close" : "default"}
					>
						<CloseIcon />
					</TitleBarButton>
				</>
			)}
		</div>
	);
}

//wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed.
export const TitleBar = memo(TitleBarInner);
