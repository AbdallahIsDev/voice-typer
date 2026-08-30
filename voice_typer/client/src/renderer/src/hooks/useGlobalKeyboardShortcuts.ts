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
 * The keydown listener dispatches on `IN_APP_BINDINGS` from
 * `components/hotkey/shortcuts.ts` — the catalog owns which
 * `KeyboardEvent.key` values map to which binding, so the actual
 * bindings (not just the display strings) can't drift from the Help
 * overlay / tooltips. This hook only supplies the per-binding actions.
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
import { useEffect, useRef } from "react";
import { toast } from "sonner";
import type {
	InAppBinding,
	InAppShortcutId,
} from "@/components/hotkey/shortcuts";
import { IN_APP_BINDINGS } from "@/components/hotkey/shortcuts";
import type { Page } from "@/types/ipc";

/** Minimal `t` function type matching i18n.t's signature. */
type TFn = (key: string, params?: Record<string, string>) => string;

/** Minimal `call` function type matching usePython's signature. */
type CallFn = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

// NOTE: these are re-exported for the hook's public type surface; the
// LOCAL bindings above (`import type`) are what the implementation uses
// — `export … from` alone does NOT create a module-local binding.
export type {
	InAppBinding,
	InAppShortcut,
	InAppShortcutId,
} from "@/components/hotkey/shortcuts";
// The shortcut string catalog (`SHORTCUTS`), the derived
// `IN_APP_SHORTCUTS` list, and the actual keyboard-event dispatch
// table (`IN_APP_BINDINGS`) all live in
// `components/hotkey/shortcuts.ts` — the single source of truth
// shared with TitleBar, Sidebar, the Help overlay, and the About
// page. The keydown listener below dispatches on `IN_APP_BINDINGS`,
// so the bindings themselves (not just the display strings) can't
// drift from the catalog. Re-exported here so the hook's
// documentation surface (and any importer) resolves the same catalog.
export {
	IN_APP_BINDINGS,
	IN_APP_SHORTCUTS,
} from "@/components/hotkey/shortcuts";

/**
 * The renderer-side half of each modifier profile declared in the
 * catalog: `IN_APP_BINDINGS[].modifier` → the event guard that decides
 * whether a keystroke matches. Keyed exhaustively over
 * `InAppBinding["modifier"]`, so adding a NEW modifier profile to the
 * catalog is a type error until a guard exists here — the modifier
 * axis can no longer drift (the binding would otherwise never fire).
 */
const MODIFIER_GUARDS: Record<
	InAppBinding["modifier"],
	(e: KeyboardEvent) => boolean
> = {
	// Ctrl OR Cmd, never Shift/Alt — the original
	// `(e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey` guard.
	ctrlCmd: (e) => (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey,
};

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
	// Ref mirror of `call` so the keydown-listener effect doesn't
	// re-register on every render when a test mock returns a fresh
	// `call` per render. The listener body reads the latest `call` via
	// the ref. Same pattern as useVocabulary.ts.
	const callRef = useRef(call);
	useEffect(() => {
		callRef.current = call;
	}, [call]);

	// Ref mirror of `textSize` (same pattern as callRef above) so the
	// listener effect doesn't re-install on every text-size change and
	// so `bumpTextSize` never reads a stale closure value: the ref is
	// advanced SYNCHRONOUSLY inside bumpTextSize, which makes rapid
	// wheel/keystroke bursts between renders accumulate correctly
	// (14 → 15 → 16) instead of replaying the last rendered value.
	const textSizeRef = useRef(textSize);
	useEffect(() => {
		textSizeRef.current = textSize;
	}, [textSize]);

	useEffect(() => {
		// Per-binding handlers keyed by catalog id — the ACTIONS half of
		// the binding table. Which key triggers which action comes from
		// `IN_APP_BINDINGS` (the catalog); this map only decides what
		// each binding DOES. Both halves are exhaustive over
		// `InAppShortcutId`, so adding a shortcut to the catalog forces
		// a handler here (type error) and removing one forces cleanup.
		//
		// Guard helpers: the navigation bindings (toggleSidebar,
		// openSettings, goHome) suppress while a Radix Dialog is open
		// OR the user is typing in an input; the zoom bindings only
		// suppress while typing.
		const isModalOpen = () =>
			document.querySelector('[role="dialog"][data-state="open"]') != null;
		const isTyping = (e: KeyboardEvent) => {
			const target = e.target as HTMLElement | null;
			const tag = target?.tagName?.toLowerCase() ?? "";
			return (
				tag === "input" ||
				tag === "textarea" ||
				target?.isContentEditable === true
			);
		};

		const bumpTextSize = (
			delta: number,
			opts: { silentOnError?: boolean } = {},
		) => {
			const current = textSizeRef.current ?? 14;
			const next =
				delta > 0 ? Math.min(current + 1, 20) : Math.max(current - 1, 10);
			if (next === current) return;
			// Advance the ref BEFORE awaiting any re-render so consecutive
			// events (Ctrl+Wheel bursts) see the accumulated value.
			textSizeRef.current = next;
			setTextSize(next);
			callRef
				.current("set_config", { text_size: next })
				.catch((err: unknown) => {
					// Key shortcuts surface failures loudly; the wheel path
					// swallows them (documented contract below — the wheel
					// handler passes silentOnError: true).
					if (opts.silentOnError) return;
					console.warn(
						"[renderer:useGlobalKeyboardShortcuts] set_config failed:",
						err,
					);
					toast.error(t("errorBoundary.unknownError"));
				});
		};

		const handlers: Record<InAppShortcutId, (e: KeyboardEvent) => void> = {
			toggleSidebar: (e) => {
				if (isTyping(e) || isModalOpen()) return;
				e.preventDefault();
				setSidebarCollapsed((c) => !c);
			},
			openSettings: (e) => {
				if (isTyping(e) || isModalOpen()) return;
				e.preventDefault();
				navigate("settings");
			},
			goHome: (e) => {
				if (isTyping(e) || isModalOpen()) return;
				e.preventDefault();
				navigate("home");
			},
			// Zoom shortcuts (Ctrl+= / Ctrl+-) are intentionally NOT
			// modal-gated — they're page-zoom semantics that apply
			// regardless of modal state, and a user adjusting zoom
			// while a dialog is open is a legitimate action. They ARE
			// typing-gated so Ctrl+=/Ctrl+- pressed while focus is
			// inside an <input>/<textarea>/contentEditable (e.g. the
			// Settings search field) does NOT hijack the keystroke to
			// bump text size. The browser's native zoom remains
			// available via Ctrl++ (different key) outside the app's
			// text-size shortcut namespace. Behaviour is otherwise
			// preserved (same min/max bounds, same `set_config` IPC).
			zoomIn: (e) => {
				if (isTyping(e)) return;
				e.preventDefault();
				bumpTextSize(1);
			},
			zoomOut: (e) => {
				if (isTyping(e)) return;
				e.preventDefault();
				bumpTextSize(-1);
			},
		};

		const keyHandler = (e: KeyboardEvent) => {
			// The modifier guard comes from the CATALOG: the binding's
			// `modifier` profile maps to the event guard that must pass.
			// The key axis (eventKeys) and the modifier axis (modifier →
			// MODIFIER_GUARDS) are both catalog-driven, so a binding can
			// only fire when BOTH halves of its catalog descriptor match.
			const binding = IN_APP_BINDINGS.find((b) => b.eventKeys.includes(e.key));
			if (!binding) return;
			// The Record is exhaustive over the catalog's modifier
			// profiles, so the guard always exists at runtime — the
			// optional call is only for noUncheckedIndexedAccess.
			const guard = MODIFIER_GUARDS[binding.modifier];
			if (!guard?.(e)) return;
			handlers[binding.id](e);
		};

		const wheelHandler = (e: WheelEvent) => {
			if (!e.ctrlKey && !e.metaKey) return;
			e.preventDefault();
			if (e.deltaY < 0) {
				bumpTextSize(1, { silentOnError: true });
			} else if (e.deltaY > 0) {
				bumpTextSize(-1, { silentOnError: true });
			}
		};

		window.addEventListener("keydown", keyHandler);
		window.addEventListener("wheel", wheelHandler, { passive: false });
		return () => {
			window.removeEventListener("keydown", keyHandler);
			window.removeEventListener("wheel", wheelHandler);
		};
	}, [navigate, setTextSize, t, setSidebarCollapsed]);
}
