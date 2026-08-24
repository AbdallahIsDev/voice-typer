import type {
	LinuxWindowButtonsConfig,
	LinuxWindowButtonsSystemInfo,
} from "@/types/config";

/** The fully-resolved Linux window-button layout the TitleBar renders.
 *  Computed once per config/system change in App.tsx and passed down as
 *  a single prop so the (memoized) TitleBar re-renders only when the
 *  effective layout actually changes. */
export interface ResolvedLinuxWindowButtons {
	/** Physical side of the title bar the cluster sits on. The bar is
	 *  pinned dir="ltr", so "left" is the physical left edge. */
	side: "left" | "right";
	showMinimize: boolean;
	showMaximize: boolean;
	showClose: boolean;
	/** Button shell style: GNOME/Yaru-style always-visible circle (the
	 *  default) or KDE/Breeze-style flat square. */
	buttonStyle: "circle" | "square";
	/** True when the effective layout came from the desktop's own
	 *  button-layout (mode "system" AND a system snapshot was available). */
	followsSystem: boolean;
}

/** Server-side defaults — MUST mirror the `linux_window_buttons`
 *  dataclass default in `voice_typer/server/config/_schema.py`. */
export const DEFAULT_LINUX_WINDOW_BUTTONS: LinuxWindowButtonsConfig = {
	mode: "system",
	side: "right",
	show_minimize: true,
	show_maximize: true,
	show_close: true,
};

/** Fallback when mode is "system" but the sidecar snapshot is missing
 *  (older sidecar, probe failure, non-Linux): the classic right-side
 *  minimize/maximize/close trio. */
const FALLBACK_LAYOUT = {
	side: "right" as const,
	buttons: ["minimize", "maximize", "close"] as string[],
};

/** Resolve the effective Linux window-button layout.
 *
 * Precedence: `mode: "custom"` uses the user's side/visibility flags;
 * `mode: "system"` uses the desktop's own button-layout from the
 * sidecar snapshot when available, falling back to the classic trio
 * when it isn't. KDE sessions always get Breeze-style squares (option 3)
 * regardless of layout source — that's a styling concern, not a layout
 * one. Non-Linux callers never invoke this (TitleBar gates on IS_LINUX),
 * but the function is total so tests can call it bare.
 */
export function resolveLinuxWindowButtons(
	config?: Partial<LinuxWindowButtonsConfig> | null,
	system?: LinuxWindowButtonsSystemInfo | null,
): ResolvedLinuxWindowButtons {
	const merged: LinuxWindowButtonsConfig = {
		...DEFAULT_LINUX_WINDOW_BUTTONS,
		...(config ?? {}),
	};

	let side = merged.side;
	let showMinimize = merged.show_minimize;
	let showMaximize = merged.show_maximize;
	let showClose = merged.show_close;
	let followsSystem = false;

	if (merged.mode === "system") {
		const layout = system?.layout ?? FALLBACK_LAYOUT;
		side = layout.side;
		const buttons = layout.buttons;
		showMinimize = buttons.includes("minimize");
		showMaximize = buttons.includes("maximize");
		showClose = buttons.includes("close");
		followsSystem = system?.layout != null;
	}

	return {
		side,
		showMinimize,
		showMaximize,
		showClose,
		buttonStyle: system?.desktop_environment === "kde" ? "square" : "circle",
		followsSystem,
	};
}
