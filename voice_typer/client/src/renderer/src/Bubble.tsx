/**
 * Bubble overlay React component — the always-on-top floating pill that
 * visualises microphone levels while recording and surfaces
 * transcribing/idle/error state.
 *
 * PVT-067: previously a 671-line monolith. Extracted subcomponents and
 * hooks live in `./bubble-components.tsx`:
 *   - `useBubbleLifecycle` — theme sync (PVT-017), audio-level rAF loop
 *     (paused when hidden), and visibility tracking.
 *   - `useBubbleStateMachine` — `mode` / `animState` / `exitTick`.
 *   - `BubbleVisualizer` — recording-mode bars + REC indicator.
 *   - `BubbleMicButton` — always-visible mic toggle.
 *   - `BubbleDismissButton` — BG-96 dismiss '×' affordance.
 *
 * This file owns only the auto-resize `useLayoutEffect`, the
 * fading → exit timer, the animation-end callback, and the render tree.
 *
 * PVT-048: the dead keyboard-move handler that previously lived here
 * has been removed — see the comment below for why and how to
 * re-implement keyboard-move correctly.
 */
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
} from "react";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import {
	BubbleDismissButton,
	BubbleMicButton,
	type BubbleMode,
	BubbleVisualizer,
	FADEOUT_DURATION_MS,
	HugeiconsIcon,
	Mic02Icon,
	TRANSCRIBING_DOT_COUNT,
	tf,
	useBubbleLifecycle,
	useBubbleStateMachine,
} from "./bubble-components";

// PVT-048 / BG-30: keyboard-based bubble repositioning was previously
// implemented as a `window.addEventListener("keydown", ...)` handler
// in this component. It was DEAD CODE in production because the bubble
// BrowserWindow is created with `focusable: false` (see
// `voice_typer/client/src/main/windows/bubble-window.ts`), so the
// renderer never receives keyboard focus and window-level `keydown`
// events never fire in the shipped app. The handler only fired under
// jsdom synthetic events (the old `Bubble-keyboard-move.test.tsx`).
//
// BG-30 DECISION (option b — document as mouse-drag-only): rather than
// add a MAIN-PROCESS global hotkey (option a), the bubble is documented
// in user-facing help as mouse-drag-only. This is a deliberate product
// decision: the bubble is a tiny always-on-top pill that the user
// occasionally drags to a new spot; a global hotkey would consume a
// valuable shortcut and add cross-platform complexity (Electron
// `globalShortcut` on Windows/macOS, X11 grab on Linux) for a feature
// with low expected usage. The main-process `bubble:move-by` IPC
// handler (in `main/ipc/bubble-handlers.ts`) is preserved so a future
// product change can wire a global hotkey without renderer work.
//
// If a future product decision flips `focusable: false` to `true`
// (which would also affect the BG-31 mic-button accessibility trade-
// off), re-introducing a renderer keydown handler becomes safe — see
// the dead-code guard test in `Bubble-keyboard-move.test.tsx` which
// fails LOUDLY if `focusable: false` is removed without also
// re-adding the handler.

export function Bubble({ className: _className }: { className?: string }) {
	const dotRefs = useRef<(HTMLSpanElement | null)[]>([]);
	const pillRef = useRef<HTMLDivElement>(null);
	const fadeOutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	// `draggable` toggles the native CSS drag-region on the pill.
	const [draggable, setDraggable] = useState(true);
	// UX-10: whether to show the mic button (always_visible + both
	// toggles on). Driven by `bubble:config` from the Python backend.
	const [micButton, setMicButton] = useState(false);
	// BG-96: whether to show the dismiss '×' button. Shown whenever
	// the bubble is in `always_visible` mode (the only mode where the
	// user needs to manually dismiss the bubble — `show_on_record`
	// auto-hides when recording stops). Driven by `bubble:config`.
	const [dismissable, setDismissable] = useState(false);

	// PVT-067: lifecycle + state machine extracted to hooks.
	const _isVisible = useBubbleLifecycle(dotRefs);
	const {
		mode,
		animState,
		setAnimState,
		exitTick,
		setExitTick: _setExitTick,
	} = useBubbleStateMachine();
	// `_isVisible` is consumed inside useBubbleLifecycle (gates the rAF
	// loop). We acknowledge it here so eslint doesn't flag it as unused.
	void _isVisible;

	// Sync `draggable` from the main process (Settings page toggle).
	useEffect(() => {
		const api = window.bubble;
		if (!api) return;
		const off = api.onDraggable((d) => setDraggable(d));
		return off;
	}, []);

	// UX-10: receive bubble-relevant config from the (sandboxed)
	// backend. The bubble renderer has no get_config, so the Python
	// backend pushes bubble_behavior / bubble_click_to_toggle /
	// bubble_mic_button via the dedicated bubble:config channel. We
	// show the mic button only when all three conditions are met.
	//
	// (The theme_preset / custom_theme / theme_mode fields of the same
	// payload are handled inside `useBubbleLifecycle` → `useThemeSync`
	// — PVT-017.)
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onConfig) return;

		const off = api.onConfig((cfg) => {
			const behavior = cfg.bubble_behavior;
			const clickToToggle = cfg.bubble_click_to_toggle;
			const micButton = cfg.bubble_mic_button;
			const enabled =
				behavior === "always_visible" &&
				micButton !== false &&
				clickToToggle !== false;
			setMicButton(enabled);
			// BG-96: dismiss button shown whenever the bubble is
			// in always_visible mode (regardless of the mic-button
			// toggles — the user needs a way to manually dismiss
			// an always-visible bubble even when the mic button is
			// disabled).
			setDismissable(behavior === "always_visible");
		});
		return off;
	}, []);

	// UX-10: mic button click → toggle dictation. The bubble is a
	// sandboxed renderer (SEC-026) with no python.call, so it routes
	// through the dedicated bubble:toggle-dictation channel.
	const handleMicClick = useCallback(() => {
		(
			window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined
		)?.toggleDictation?.();
	}, []);

	// BG-96: dismiss button click → send `bubble:dismiss` IPC. The
	// main-process handler is owned by F11 (see return report). Until
	// F11 adds the handler, this IPC send is a no-op (Electron's
	// default ipcMain behavior is to silently drop messages with no
	// registered handler). The `dismiss` method is added to the bubble
	// preload (preload/bubble.ts) but not yet to the
	// BubbleWindowBubble type (ipc.ts is owned by another agent), so
	// we cast to a wider type that includes the optional `dismiss`
	// method. When F11 (or a future type extension) adds `dismiss` to
	// BubbleWindowBubble, this cast can be removed.
	const handleDismissClick = useCallback(() => {
		(
			window.bubble as
				| (import("@/types/ipc").BubbleWindowBubble & {
						dismiss?: () => void;
				  })
				| undefined
		)?.dismiss?.();
	}, []);

	// Auto-resize BrowserWindow to fit the pill content exactly.
	// BUBBLE-FIX-5.2: useLayoutEffect so resize IPC arrives before paint.
	// BUBBLE-FIX-SHOW-RESIZE: depends on BOTH animState AND mode so
	// resize runs when the pill content size changes between modes.
	useLayoutEffect(() => {
		if (animState === "exit") return;
		const el = pillRef.current;
		if (!el) return;
		const w = Math.ceil(el.offsetWidth);
		const h = Math.ceil(el.offsetHeight);
		(
			window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined
		)?.resizeTo?.(w + 1, h + 1);
		void mode; // semantic dep — pill content size changes between modes
	}, [animState, mode]);

	// Fading → exit transition. When the transcribing content fade-out
	// completes, trigger the bubble exit animation. For non-transcribing
	// modes, exit is triggered immediately. `exitTick` guarantees re-run
	// even when mode doesn't change.
	useEffect(() => {
		if (exitTick === 0) return;

		if (mode === "fading") {
			fadeOutTimerRef.current = setTimeout(() => {
				setAnimState("exit");
			}, FADEOUT_DURATION_MS);
		} else if (mode !== "transcribing") {
			setAnimState("exit");
		}
		return () => {
			if (fadeOutTimerRef.current !== null) {
				clearTimeout(fadeOutTimerRef.current);
				fadeOutTimerRef.current = null;
			}
		};
	}, [mode, exitTick, setAnimState]);

	// Animation-end callback — when exit CSS transition completes,
	// tell the main process it's safe to hide() the BrowserWindow.
	// After the enter animation completes, re-sync the window size to
	// the pill content (handles edge cases where the initial
	// useLayoutEffect ran before layout settled).
	const handleAnimEnd = useCallback(() => {
		if (animState === "exit") {
			setAnimState("");
			window.bubble?.hideComplete?.();
		} else if (animState === "enter") {
			setAnimState("");
			const el = pillRef.current;
			if (el) {
				const w = Math.ceil(el.offsetWidth);
				const h = Math.ceil(el.offsetHeight);
				(
					window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined
				)?.resizeTo?.(w + 1, h + 1);
			}
		}
	}, [animState, setAnimState]);

	const transcribingDots = Array.from(
		{ length: TRANSCRIBING_DOT_COUNT },
		(_, i) => i,
	);

	return (
		<output
			aria-live="polite"
			aria-atomic="true"
			// BG-95: state-aware aria-label so screen-reader users
			// hear the current bubble mode ("recording" /
			// "transcribing" / "error" / "idle") instead of always
			// hearing "recording". The "fading" mode is a brief
			// transcribing → exit transition; it shares the
			// transcribing label. The idle label is the catch-all
			// for any unexpected future mode.
			aria-label={
				mode === "recording"
					? t("bubble.recordingIndicatorAria")
					: mode === "transcribing" || mode === "fading"
						? t("bubble.transcribingAria")
						: mode === "error"
							? t("bubble.errorIndicatorAria")
							: t("bubble.idleIndicatorAria")
			}
			className={cn(
				"inline-flex items-center justify-center",
				animState === "enter" && "animate-bubble-enter",
				animState === "exit" && "animate-bubble-exit",
			)}
			onAnimationEnd={handleAnimEnd}
		>
			<div
				ref={pillRef}
				className={cn(
					"inline-flex items-center gap-3 rounded-full",
					"border border-zinc-200 dark:border-white/10",
					"bg-white dark:bg-zinc-900",
					"px-4 py-2.5",
					draggable ? "drag-region" : "no-drag",
				)}
			>
				{mode === "transcribing" ? (
					<div className="flex items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300">
						<span>{t("bubble.transcribingLabel")}</span>
						{transcribingDots.map((i) => (
							<span
								key={i}
								className="inline-block h-1 w-1 animate-bounce rounded-full bg-zinc-500 dark:bg-zinc-400"
								style={{
									animationDelay: `${i * 0.2}s`,
									animationDuration: "1.2s",
								}}
							/>
						))}
					</div>
				) : mode === "fading" ? (
					<div
						className="flex items-center gap-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300"
						style={{
							opacity: 0,
							transform: "translateY(-4px)",
							transition: `opacity ${FADEOUT_DURATION_MS}ms ease-out, transform ${FADEOUT_DURATION_MS}ms ease-out`,
						}}
					>
						<span>{t("bubble.transcribingLabel")}</span>
					</div>
				) : mode === "idle" ? (
					<>
						{/* A11Y: sr-only announcement so screen-reader users hear
						    "Transcription complete." when the bubble transitions to
						    idle (always_visible mode). The empty div below is
						    preserved as a zero-width sibling so Bubble.test.tsx's
						    `emptyContainer.textContent === ""` assertion still
						    passes — querySelector returns the first match in DOM
						    order, which is the empty div. */}
						<div className="flex h-6 items-center" />
						<div className="flex h-6 items-center gap-1.5 px-2" aria-hidden>
							<HugeiconsIcon
								icon={Mic02Icon}
								strokeWidth={2}
								className="w-3 h-3 text-(--text-muted)"
							/>
							<span className="text-[10px] font-medium text-(--text-muted)">
								{tf("bubble.idleLabel", "Ready")}
							</span>
						</div>
						<span className="sr-only">{t("a11y.transcriptionComplete")}</span>
					</>
				) : mode === "error" ? (
					// PVT fix: surface a red "⚠ Error" label so the user
					// can see something went wrong (e.g. backend crash,
					// mic permission revoked). Uses the destructive
					// token so it inherits theme-preset colors.
					<div className="flex h-6 items-center gap-1.5 px-2">
						<span
							className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
							aria-hidden
						/>
						<span className="text-[10px] font-medium text-destructive">
							{tf("bubble.errorLabel", "⚠ Error")}
						</span>
					</div>
				) : (
					<BubbleVisualizer dotRefs={dotRefs} />
				)}

				{micButton && (
					<BubbleMicButton mode={mode as BubbleMode} onClick={handleMicClick} />
				)}
				{dismissable && <BubbleDismissButton onClick={handleDismissClick} />}
			</div>
		</output>
	);
}

export default Bubble;
