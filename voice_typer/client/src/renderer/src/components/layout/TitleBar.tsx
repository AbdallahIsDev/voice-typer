import { PanelLeftIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useEffect, useState } from "react";
import { HotkeyTooltip } from "@/components/hotkey/HotkeyTooltip";
import { IS_LINUX, IS_MAC, IS_WIN } from "@/components/hotkey/hotkey-utils";
import { SHORTCUTS } from "@/components/hotkey/shortcuts";
import { GlobalSearchBar } from "@/components/layout/GlobalSearchBar";
import { ThemeSwitch } from "@/components/layout/ThemeSwitch";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { isTauri } from "@/lib/tauri-bridge/detect";
import { cn, focusRing } from "@/lib/utils";
import {
	type ResolvedLinuxWindowButtons,
	resolveLinuxWindowButtons,
} from "@/lib/utils/windowButtons";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page, WindowBridge } from "@/types/ipc";

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
	/** Linux-only: the resolved window-button layout (side, visibility,
	 *  circle-vs-square shell). App.tsx computes it from the
	 *  `linux_window_buttons` config + the sidecar's
	 *  `linux_window_buttons_system` snapshot via
	 *  `resolveLinuxWindowButtons`. Optional — omitted (undefined)
	 *  falls back to the classic right-side trio, and the prop is
	 *  ignored entirely on Windows/macOS. */
	linuxWindowButtons?: ResolvedLinuxWindowButtons;
	/** Active route — drives the global search bar's per-page
	 *  placeholder + visibility. Optional so existing TitleBar call
	 *  sites (tests, splash) render without the search bar. */
	currentPage?: Page;
}

// Window-control glyphs — native Windows caption icon geometry.
// CLOSE and RESTORE are the TRUE outlines of the glyphs Windows uses
// for its caption buttons, extracted from the `Segoe Fluent Icons`
// font that ships with Windows 11 (C:\Windows\Fonts\SegoeIcons.ttf,
// units-per-em 2048): ChromeClose U+E8BB, ChromeRestore U+E923,
// scaled to a 10px em (the size DWM renders them at inside a 46x32
// caption button) and centered in a 10x10 viewBox. Regenerate with
// `python scripts/gen_caption_glyph_paths.py` if ever needed.
// MINIMIZE and MAXIMIZE are deliberate sharp-cornered, integer-pixel
// variants (see the comments on each constant): the native glyphs'
// half-pixel edges / rounded corners read soft at DPR 1.

//
// WHY FILLED OUTLINES INSTEAD OF STROKED SHAPES: hand-rolled stroked
// SVGs can never match the native icons — a 0.5px stroke is sub-pixel
// at DPR 1 and antialiases to ~50% alpha (the "gray / toned-down"
// look), diagonal strokes land on half-pixel boundaries, and the X
// geometry (span/weight/caps) drifts from Microsoft's design. These
// paths are the font's own filled contours: solid currentColor mass,
// no stroke, no opacity — every covered pixel is pure #fff in dark
// mode, exactly like the OS rendering. The icons are purely
// decorative (the wrapping button carries the aria-label), so
// ``aria-hidden`` with NO child <title> (a <title> inside an
// aria-hidden SVG is dropped by screen readers AND duplicates the
// button's label).
//
// Platform note: the GLYPHS are identical on Windows and Linux (the
// shapes below match GNOME's caption icons closely enough that no
// branch is needed); what differs is the BUTTON, not the icon
// (Windows square 46×32 hit targets vs Linux circular buttons — see
// TitleBarButton).
const MINIMIZE_GLYPH_PATH =
	// Sharp-cornered bar snapped to WHOLE pixels (y 5→6). The native
	// ChromeMinimize outline spans y 4.50-5.50 with rounded caps — its
	// edges sit ON half-pixel boundaries, so at DPR 1 both edges
	// antialias to ~50% alpha = the same soft/gray bar as the old
	// hand-drawn rect. Snapping to an integer pixel row renders one
	// fully-lit row: solid color, razor-sharp.
	"M0.00 5.00H10.00V6.00H0.00Z";
const MAXIMIZE_GLYPH_PATH =
	// Sharp square outline (1px frame): outer contour clockwise, inner
	// contour counter-clockwise → nonzero fill-rule punches the hole.
	// Deliberately NOT the native ChromeMaximize outline, whose outer
	// corners are rounded (~1.5px radius) — product decision: crisp
	// right angles, all edges on integer pixels.
	"M0.00 0.00H10.00V10.00H0.00ZM1.00 1.00V9.00H9.00V1.00Z";

const RESTORE_GLYPH_PATH =
	"M9.00 2.96Q9.00 2.56 8.84 2.20Q8.68 1.84 8.40 1.57Q8.12 1.31 7.76 1.15Q7.40 1.00 7.00 1.00H2.08Q2.16 0.78 2.30 0.59Q2.45 0.41 2.63 0.27Q2.82 0.14 3.04 0.07Q3.26 0.00 3.50 0.00H7.00Q7.62 0.00 8.16 0.24Q8.71 0.47 9.12 0.88Q9.53 1.28 9.76 1.83Q10.00 2.38 10.00 3.00V6.50Q10.00 6.74 9.93 6.96Q9.86 7.18 9.73 7.37Q9.59 7.55 9.41 7.70Q9.22 7.84 9.00 7.92ZM1.47 10.00Q1.18 10.00 0.91 9.88Q0.64 9.76 0.44 9.56Q0.24 9.36 0.12 9.09Q0.00 8.82 0.00 8.53V3.48Q0.00 3.18 0.12 2.91Q0.24 2.65 0.44 2.44Q0.64 2.24 0.91 2.12Q1.18 2.00 1.47 2.00H6.52Q6.82 2.00 7.09 2.12Q7.36 2.24 7.56 2.44Q7.76 2.64 7.88 2.91Q8.00 3.18 8.00 3.48V8.53Q8.00 8.82 7.88 9.09Q7.76 9.36 7.56 9.56Q7.35 9.76 7.09 9.88Q6.82 10.00 6.52 10.00ZM6.50 9.00Q6.60 9.00 6.69 8.96Q6.78 8.92 6.85 8.85Q6.92 8.78 6.96 8.69Q7.00 8.60 7.00 8.50V3.50Q7.00 3.40 6.96 3.31Q6.92 3.21 6.86 3.14Q6.79 3.08 6.69 3.04Q6.60 3.00 6.50 3.00H1.50Q1.40 3.00 1.31 3.04Q1.22 3.08 1.15 3.15Q1.08 3.22 1.04 3.31Q1.00 3.40 1.00 3.50V8.50Q1.00 8.60 1.04 8.69Q1.08 8.78 1.15 8.85Q1.22 8.92 1.31 8.96Q1.40 9.00 1.50 9.00Z";
const CLOSE_GLYPH_PATH =
	"M5.00 5.71 0.85 9.85Q0.71 10.00 0.50 10.00Q0.29 10.00 0.14 9.86Q0.00 9.71 0.00 9.50Q0.00 9.29 0.15 9.15L4.29 5.00L0.15 0.85Q0.00 0.71 0.00 0.50Q0.00 0.40 0.04 0.30Q0.08 0.21 0.15 0.14Q0.21 0.08 0.31 0.04Q0.40 0.00 0.50 0.00Q0.71 0.00 0.85 0.15L5.00 4.29L9.15 0.15Q9.29 0.00 9.50 0.00Q9.60 0.00 9.69 0.04Q9.79 0.08 9.85 0.15Q9.92 0.21 9.96 0.31Q10.00 0.40 10.00 0.50Q10.00 0.71 9.85 0.85L5.71 5.00L9.85 9.15Q10.00 9.29 10.00 9.50Q10.00 9.60 9.96 9.69Q9.92 9.79 9.86 9.85Q9.79 9.92 9.70 9.96Q9.60 10.00 9.50 10.00Q9.29 10.00 9.15 9.85Z";

/** Shared SVG shell for all window-control glyphs: 10px em box, filled
 *  path only (no stroke), full-opacity currentColor. */
function CaptionGlyph({ d }: { d: string }) {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden="true"
			className="size-2.5 fill-current"
		>
			<path d={d} />
		</svg>
	);
}

function MinimizeIcon() {
	return <CaptionGlyph d={MINIMIZE_GLYPH_PATH} />;
}

function MaximizeIcon() {
	return <CaptionGlyph d={MAXIMIZE_GLYPH_PATH} />;
}

function RestoreIcon() {
	return <CaptionGlyph d={RESTORE_GLYPH_PATH} />;
}

function CloseIcon() {
	return <CaptionGlyph d={CLOSE_GLYPH_PATH} />;
}

interface TitleBarButtonProps {
	onClick: () => void;
	ariaLabel: string;
	variant?: "default" | "close";
	/** Linux-only shell style: GNOME/Yaru-style always-visible circle
	 *  (default) or KDE/Breeze-style flat square with a hover-only wash. */
	shape?: "circle" | "square";
	children: React.ReactNode;
}

function TitleBarButton({
	onClick,
	ariaLabel,
	variant = "default",
	shape = "circle",
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
				"border-0",
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
				// Linux shell styles. GNOME/Ubuntu (Yaru): circular buttons
				// with an ALWAYS-VISIBLE subtle circle background — the
				// gray circle is the button's resting state, NOT a hover
				// effect (matches the native Ubuntu header bar). KDE
				// Plasma (Breeze): flat SQUARES with a transparent rest
				// and a hover-only wash. Both share the same
				// deepen-on-interaction ladder. No red close on Linux —
				// the `variant` call-site already passes "default" when
				// IS_WIN is false, so all three buttons use the neutral
				// treatment. `shape` comes from the resolved window-button
				// layout (KDE detection lives in the sidecar snapshot).
				IS_LINUX
					? cn(
							// Linux buttons are SMALLER than the bar (h-6/w-6 =
							// 24px in the 32px bar → space around them) and
							// spaced gap-2 apart (see the cluster wrapper).
							shape === "square"
								? cn("h-6 w-6 rounded-none", "bg-transparent")
								: cn("h-6 w-6 rounded-full", "bg-foreground/5"),
							"hover:bg-foreground/10 dark:hover:bg-foreground/10",
							"focus-visible:bg-foreground/10 dark:focus-visible:bg-foreground/10",
							"active:bg-foreground/15 dark:active:bg-foreground/15",
						)
					: cn(
							"h-8 w-11.5 rounded-none",
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
						),
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
	linuxWindowButtons,
	currentPage,
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

	// ── Linux window-button layout (option: system / custom / KDE) ──
	// Resolved from the config + sidecar snapshot by App.tsx; undefined
	// falls back to the classic right-side trio. Only the IS_LINUX
	// cluster below consumes this — Windows keeps its fixed native
	// convention and macOS has no custom buttons at all.
	const linuxButtons =
		linuxWindowButtons ?? resolveLinuxWindowButtons(undefined, undefined);

	const linuxButtonDefs = [
		{
			key: "minimize",
			show: linuxButtons.showMinimize,
			label: t("titleBar.minimize"),
			onClick: handleMinimize,
			glyph: <MinimizeIcon />,
		},
		{
			key: "maximize",
			show: linuxButtons.showMaximize,
			label: isMaximized ? t("titleBar.restore") : t("titleBar.maximize"),
			onClick: handleToggleMaximize,
			glyph: isMaximized ? <RestoreIcon /> : <MaximizeIcon />,
		},
		{
			key: "close",
			show: linuxButtons.showClose,
			label: t("titleBar.close"),
			onClick: handleClose,
			glyph: <CloseIcon />,
		},
	].filter((button) => button.show);

	const renderLinuxCluster = (side: "left" | "right") => (
		<div
			className={cn(
				"flex items-center gap-2",
				// Right side: ms-1 separates from the theme switch and pe-2
				// keeps the close button off the window's right edge.
				// Left side: mirrored insets (the bar is pinned dir="ltr",
				// so these are PHYSICAL sides — see the root's LTR pin).
				side === "right" ? "ms-1 pe-2" : "me-1 ps-2",
			)}
		>
			{linuxButtonDefs.map((button) => (
				<TitleBarButton
					key={button.key}
					onClick={button.onClick}
					ariaLabel={button.label}
					shape={linuxButtons.buttonStyle}
				>
					{button.glyph}
				</TitleBarButton>
			))}
		</div>
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
			{...(isTauri() ? { "data-tauri-drag-region": "" } : {})}
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
			{/* Linux window controls pinned to the LEFT edge — either the
			    desktop's own button-layout says so (gsettings, "follow
			    system" mode) or the user picked "Left" in Settings →
			    Appearance. Rendered BEFORE the toolbar group so the app
			    buttons start after them. The bar is pinned dir="ltr", so
			    this is the physical left edge in every locale. */}
			{IS_LINUX && linuxButtons.side === "left" && renderLinuxCluster("left")}
			{IS_MAC && <div className="h-8 w-18 shrink-0" aria-hidden="true" />}
			{/* Toolbar button group — sidebar/back/forward/help wrapped in
			    a p-1 (4px) padded flex container so no button takes the
			    full 32px bar height; the 4px padding gives breathing room
			    top/bottom/left/right, and gap-1 separates the buttons. */}
			<div className="flex items-center gap-1 p-1">
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
							// Toolbar buttons are h-6 (24px) inside the p-1
							// group wrapper — the 4px padding keeps them off
							// the full 32px bar height with room to breathe.
							"no-drag press-scale flex h-6 w-6 items-center justify-center",
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
							"no-drag press-scale flex h-6 w-6 items-center justify-center rounded",
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
							"no-drag press-scale flex h-6 w-6 items-center justify-center rounded",
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
				<HotkeyTooltip
					label={t("help.openHelp")}
					keys={SHORTCUTS.openHelp.keys}
				>
					<button
						type="button"
						onClick={onOpenHelp}
						aria-label={t("help.openHelp")}
						//expose the "?" shortcut via aria-keyshortcuts so
						// AT users can discover that pressing "?" opens this overlay.
						aria-keyshortcuts={SHORTCUTS.openHelp.ariaKeyshortcuts}
						className={cn(
							"no-drag press-scale flex h-6 w-6 items-center justify-center rounded",
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
			</div>

			{/* Global search bar — centered in the middle of the title bar.
			    Only rendered on searchable pages (history, templates,
			    vocabulary, settings*). On non-searchable pages the flex-1
			    spacer keeps the toolbar pushed left and controls on the
			    right, exactly as before. */}
			<div className="flex min-w-0 flex-1 items-center justify-center px-2">
				{currentPage ? <GlobalSearchBar currentPage={currentPage} /> : null}
			</div>

			{/* Theme control — icon-only, in its OWN p-1 (4px) padded
			    container (separate from the toolbar group). It sits on
			    the right edge of the bar, immediately LEFT of the window
			    controls. The 4px padding insets the 24px button from the
			    full bar height AND keeps its hover background from
			    touching the window-control cluster (e.g. the Linux
			    minimize circle, whose always-visible background the theme
			    hover would otherwise collide with). On macOS the native
			    traffic lights occupy the left gutter and there are no
			    window controls — the button anchors the bar's right edge
			    instead. The accessible name + hover title carry the
			    current→next theme info (no visible text). */}
			<div className="flex items-center p-1">
				<ThemeSwitch
					themeMode={themeMode}
					onThemeChange={onThemeChange}
					className={cn(
						"no-drag press-scale h-6 w-6 rounded",
						"text-(--text-muted) hover:text-(--text-primary)",
					)}
				/>
			</div>

			{/* Window controls — Windows/Linux only. macOS uses the native
			    traffic lights (titleBarStyle: 'hiddenInset' in the main
			    window), so rendering Windows-style buttons there would
			    duplicate the chrome with wrong-style buttons.
			    Windows: the fixed 46×32 edge-to-edge trio — the layout is
			    NOT user-configurable on Windows (native convention).
			    Linux: the resolved cluster (side/visibility/shape from
			    the linux_window_buttons setting + system snapshot); the
			    LEFT-side variant renders before the toolbar group (see
			    the mirror block at the top of the bar). */}
			{IS_WIN && (
				<div
					className={cn(
						// ms-1 separates the cluster from the theme switch —
						// 4px here + the theme wrapper's 4px padding = an
						// 8px icon-to-icon gap. Windows square buttons are
						// edge-to-edge (no gap).
						"ms-1 flex items-center",
					)}
				>
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
						// Red close hover is a WINDOWS convention (the native
						// close button turns solid red); Linux never passes
						// the "close" variant.
						variant="close"
					>
						<CloseIcon />
					</TitleBarButton>
				</div>
			)}
			{IS_LINUX && linuxButtons.side === "right" && renderLinuxCluster("right")}
		</div>
	);
}

//wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed.
export const TitleBar = memo(TitleBarInner);
