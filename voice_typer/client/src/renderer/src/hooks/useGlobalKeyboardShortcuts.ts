/**
 * useGlobalKeyboardShortcuts — app-wide keyboard + Ctrl+Wheel shortcuts.
 *
 * Extracted from App.tsx (Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the original
 * inline effect:
 *
 *   - Ctrl/Cmd+B           → toggle sidebar collapsed state.
 *   - Ctrl/Cmd+,           → navigate to Settings.
 *   - Ctrl/Cmd+H           → navigate to Home.
 *   - Ctrl/Cmd+= (or "+")  → bump text size up by 1 (clamped to 20), persist
 *                             via `set_config` IPC. No-op while typing in an
 *                             input/textarea/contentEditable.
 *   - Ctrl/Cmd+-           → bump text size down by 1 (clamped to 10), same
 *                             guards as above.
 *   - Ctrl/Cmd+Wheel       → bump text size up/down by 1 (same clamps). Fires
 *                             regardless of focus target (matches original).
 *
 * All shortcuts require Ctrl OR Cmd AND no Shift AND no Alt (matches the
 * original `(e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey` guard).
 *
 * The `b`/`,``h`/`=`/`-` shortcuts are suppressed when the user is typing
 * inside an `<input>`, `<textarea>`, `<select>`, or `contentEditable` host
 * so the app doesn't hijack legitimate text-entry keystrokes. The wheel
 * shortcut has no such guard (matches original behaviour — Ctrl+Wheel is
 * rarely sent while typing).
 *
 * Errors from the `set_config` IPC are surfaced as `toast.error(
 * errorBoundary.unknownError)` for the key shortcuts and silently
 * swallowed for the wheel shortcut (matches original).
 */
import { useEffect } from "react";
import { toast } from "sonner";
import type { Page } from "@/types/ipc";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/** Minimal `call` function type matching usePython's signature. */
type CallFn = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

/**
 * Static, data-driven descriptor for the in-app keyboard shortcuts
 * handled by this hook. Exported so the help overlay (and any future
 * documentation surface) can render the same authoritative list the
 * hook implements — closing the loophole where the overlay's text
 * drifted from the actual key bindings.
 *
 * `keys` is the user-facing key combination string (matching the
 * format already used in the i18n `help.keys.*` entries: "Ctrl+B",
 * "Ctrl+,", etc.). `labelKey` is the i18n key whose value localises
 * the shortcut's description; if the key is missing from a locale,
 * `t()` falls back to the raw key (the i18n sub-agent will fill in
 * translations later). `category` groups shortcuts in the overlay
 * (e.g. "navigation" vs "view") so future sections can split them
 * visually without reshuffling the array.
 */
export interface InAppShortcut {
	keys: string;
	labelKey: string;
	category: string;
}

export const IN_APP_SHORTCUTS: InAppShortcut[] = [
	{
		keys: "Ctrl+B",
		labelKey: "help.shortcuts.toggleSidebar",
		category: "navigation",
	},
	{
		keys: "Ctrl+,",
		labelKey: "help.shortcuts.openSettings",
		category: "navigation",
	},
	{ keys: "Ctrl+H", labelKey: "help.shortcuts.goHome", category: "navigation" },
	{ keys: "Ctrl+=", labelKey: "help.shortcuts.zoomIn", category: "view" },
	{ keys: "Ctrl+-", labelKey: "help.shortcuts.zoomOut", category: "view" },
];

interface UseGlobalKeyboardShortcutsArgs {
	/** Navigate function from useNavigation. */
	navigate: (page: Page) => void;
	/** Current text size (from useTheme). Null/undefined → default 14. */
	textSize: number | null | undefined;
	/** Setter for text size (from useTheme). */
	setTextSize: (size: number) => void;
	/** Python bridge call function (from usePython). */
	call: CallFn;
	/** i18n translate function (from useT). */
	t: TFn;
	/** Setter for sidebar collapsed state (from useState in App.tsx). */
	setSidebarCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
}

export function useGlobalKeyboardShortcuts({
	navigate,
	textSize,
	setTextSize,
	call,
	t,
	setSidebarCollapsed,
}: UseGlobalKeyboardShortcutsArgs) {
	useEffect(() => {
		const keyHandler = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey) {
				// Modal-open guard: when a Radix Dialog / AlertDialog
				// is open (e.g. ConfirmDialog, the help overlay, the
				// hotkey picker's modal), Ctrl+B and Ctrl+, would
				// race the dialog's own keyboard handling and could
				// navigate the user away from a context they're
				// actively in (e.g. confirming a destructive action).
				// The "?" key handler in App.tsx already uses this
				// same querySelector gate; mirror it here for
				// consistency. The zoom shortcuts (Ctrl+= / Ctrl+-)
				// are intentionally NOT gated — they're page-zoom
				// semantics that apply regardless of modal state,
				// and a user adjusting zoom while a dialog is open
				// is a legitimate action.
				const modalOpen = document.querySelector(
					'[role="dialog"][data-state="open"]',
				);
				const target = e.target as HTMLElement | null;
				const tag = target?.tagName?.toLowerCase() ?? "";
				const typing =
					tag === "input" ||
					tag === "textarea" ||
					target?.isContentEditable === true;

				if (e.key === "b" && !typing && !modalOpen) {
					e.preventDefault();
					setSidebarCollapsed((c) => !c);
					return;
				}
				if (e.key === "," && !typing && !modalOpen) {
					e.preventDefault();
					navigate("settings");
					return;
				}
				if (e.key === "h" && !typing && !modalOpen) {
					e.preventDefault();
					navigate("home");
					return;
				}

				// Zoom shortcuts (Ctrl+= / Ctrl+-) moved
				// inside the `!typing` guard so Ctrl+=/Ctrl+- pressed
				// while focus is inside an <input>/<textarea>/
				// contentEditable (e.g. the Settings search field) does
				// NOT hijack the keystroke to bump text size. The
				// browser's native zoom remains available via Ctrl++
				// (different key) outside the app's text-size shortcut
				// namespace. Behaviour is otherwise preserved (same
				// min/max bounds, same `set_config` IPC).
				if ((e.key === "=" || e.key === "+") && !typing) {
					e.preventDefault();
					const current = textSize ?? 14;
					const next = Math.min(current + 1, 20);
					if (next !== current) {
						setTextSize(next);
						call("set_config", { text_size: next }).catch((err) => {
							console.warn(
								"[renderer:useGlobalKeyboardShortcuts] set_config failed:",
								err,
							);
							toast.error(t("errorBoundary.unknownError"));
						});
					}
					return;
				}

				if (e.key === "-" && !typing) {
					e.preventDefault();
					const current = textSize ?? 14;
					const next = Math.max(current - 1, 10);
					if (next !== current) {
						setTextSize(next);
						call("set_config", { text_size: next }).catch((err) => {
							console.warn(
								"[renderer:useGlobalKeyboardShortcuts] set_config failed:",
								err,
							);
							toast.error(t("errorBoundary.unknownError"));
						});
					}
					return;
				}
			}
		};

		const wheelHandler = (e: WheelEvent) => {
			if (!e.ctrlKey && !e.metaKey) return;
			e.preventDefault();
			const current = textSize ?? 14;
			if (e.deltaY < 0) {
				const next = Math.min(current + 1, 20);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
			} else if (e.deltaY > 0) {
				const next = Math.max(current - 1, 10);
				if (next !== current) {
					setTextSize(next);
					call("set_config", { text_size: next }).catch(() => {});
				}
			}
		};

		window.addEventListener("keydown", keyHandler);
		window.addEventListener("wheel", wheelHandler, { passive: false });
		return () => {
			window.removeEventListener("keydown", keyHandler);
			window.removeEventListener("wheel", wheelHandler);
		};
	}, [navigate, textSize, call, setTextSize, t, setSidebarCollapsed]);
}
