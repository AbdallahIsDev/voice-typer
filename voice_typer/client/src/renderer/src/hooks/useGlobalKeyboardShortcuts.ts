/**
 * useGlobalKeyboardShortcuts — app-wide keyboard + Ctrl+Wheel shortcuts.
 *
 * Extracted from App.tsx (BG-27, Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the original
 * inline effect:
 *
 *   - Ctrl/Cmd+B           → toggle sidebar collapsed state.
 *   - Ctrl/Cmd+,           → navigate to Settings.
 *   - Ctrl/Cmd+H           → navigate to Home.
 *   - Ctrl/Cmd+= (or "+")  → bump text size up by 1 (clamped to 20), persist
 *                             via `set_config` IPC. No-op while typing in an
 *                             input/textarea/contentEditable (PVT-fix-11).
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
				const target = e.target as HTMLElement | null;
				const tag = target?.tagName?.toLowerCase() ?? "";
				const typing =
					tag === "input" ||
					tag === "textarea" ||
					target?.isContentEditable === true;

				if (e.key === "b" && !typing) {
					e.preventDefault();
					setSidebarCollapsed((c) => !c);
					return;
				}
				if (e.key === "," && !typing) {
					e.preventDefault();
					navigate("settings");
					return;
				}
				if (e.key === "h" && !typing) {
					e.preventDefault();
					navigate("home");
					return;
				}

				// PVT-fix-11: zoom shortcuts (Ctrl+= / Ctrl+-) moved
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
							console.warn("[IPC] set_config failed:", err);
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
							console.warn("[IPC] set_config failed:", err);
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
