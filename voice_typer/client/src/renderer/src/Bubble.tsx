/**
 * Bubble overlay React component — the always-on-top floating pill that
 * visualises microphone levels while recording and surfaces
 * transcribing/idle/error state.
 *
 * Previously a 671-line monolith. Extracted subcomponents and hooks
 * live in `./bubble-components.tsx`:
 *   - `useBubbleLifecycle` — theme sync, audio-level rAF loop (paused
 *     when hidden), and visibility tracking.
 *   - `useBubbleStateMachine` — `mode` / `animState` / `exitTick` /
 *     `errorMessage`.
 *   - `BubbleVisualizer` — recording-mode bars + REC indicator.
 *   - `BubbleMicButton` — always-visible mic toggle.
 *   - `BubbleDismissButton` — dismiss '×' affordance.
 *   - `BubbleModeContent` — 8-way mode-branch pill body (extracted
 *     from the inline ternary chain that used to live here).
 *   - `getBubbleAriaLabel` — pure helper that returns the
 *     state-aware aria-label for the `<output aria-live>` wrapper.
 *
 * This file owns only the auto-resize `useLayoutEffect`, the
 * fading → exit timer, the animation-end callback, the error-mode
 * auto-hide timer, and the render tree.
 *
 * The dead keyboard-move handler that previously lived here has been
 * removed — see the comment below for why and how to re-implement
 * keyboard-move correctly.
 */
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
} from "react";
import { cn } from "@/lib/utils";
import type { BubbleWindowBubble } from "@/types/ipc";
import {
	BubbleBridgeProvider,
	BubbleDismissButton,
	BubbleMicButton,
	type BubbleMode,
	BubbleModeContent,
	BubbleStopButton,
	FADEOUT_DURATION_MS,
	getBubbleAriaLabel,
	useBubbleBridge,
	useBubbleLifecycle,
	useBubbleStateMachine,
} from "./bubble-components";

// Auto-hide delay (ms) for error mode when the bubble is in
// `show_on_record` behavior. The bubble stays sticky in
// `always_visible` mode (the user can manually dismiss it). 7s is the
// middle of the 5-10s range — long enough for the user to notice the
// error and click retry, short enough that the pill doesn't linger
// over their text field after they've moved on.
const ERROR_AUTO_HIDE_MS = 7000;

// Previously every effect/callback in this file re-cast `window.bubble`
// to `BubbleWindowBubble | undefined` inline — the same
// `as import("@/types/ipc").BubbleWindowBubble | undefined` expression
// appeared 5+ times. Centralising the cast in one typed accessor makes
// the intent explicit (a single, named unsafe boundary at the preload
// bridge), removes the visual noise, and gives the test suite one
// function to mock if needed. The cast itself is unavoidable because
// `window.bubble` is typed as `unknown` from the sandboxed preload
// (SEC-026): the bubble renderer's preload script is intentionally
// minimal and the type augmentation lives in `@/types/ipc/bubble_bridge`.
function getBubbleApi(): BubbleWindowBubble | undefined {
	return window.bubble as BubbleWindowBubble | undefined;
}

// Keyboard-based bubble repositioning was previously implemented as a
// `window.addEventListener("keydown", ...)` handler in this component.
// It was DEAD CODE in production because the bubble BrowserWindow is
// created with `focusable: false` (see
// `voice_typer/client/src/main/windows/bubble-window.ts`), so the
// renderer never receives keyboard focus and window-level `keydown`
// events never fire in the shipped app. The handler only fired under
// jsdom synthetic events (the old `Bubble-keyboard-move.test.tsx`).
//
// DECISION (option b — document as mouse-drag-only): rather than add a
// MAIN-PROCESS global hotkey (option a), the bubble is documented in
// user-facing help as mouse-drag-only. This is a deliberate product
// decision: the bubble is a tiny always-on-top pill that the user
// occasionally drags to a new spot; a global hotkey would consume a
// valuable shortcut and add cross-platform complexity (Electron
// `globalShortcut` on Windows/macOS, X11 grab on Linux) for a feature
// with low expected usage. The main-process `bubble:move-by` IPC
// handler (in `main/ipc/bubble-handlers.ts`) is preserved so a future
// product change can wire a global hotkey without renderer work.
//
// If a future product decision flips `focusable: false` to `true`
// (which would also affect the mic-button accessibility trade-off),
// re-introducing a renderer keydown handler becomes safe — see the
// dead-code guard test in `Bubble-keyboard-move.test.tsx` which fails
// LOUDLY if `focusable: false` is removed without also re-adding the
// handler.

export function Bubble() {
	// Wrap the inner tree in `<BubbleBridgeProvider>` so every
	// descendant hook (`useBubbleLifecycle`, `useBubbleStateMachine`,
	// `useAudioLevels`, `useThemeSync`, and the `onConfig` /
	// `onDraggable` effects below) can obtain the shared bridge via
	// `useBubbleBridge()` and register handlers via `bridge.on(...)`.
	// The bridge owns the single per-event `window.bubble` IPC
	// subscription; consumers fan out via the bridge's in-process
	// handler Set.
	return (
		<BubbleBridgeProvider>
			<BubbleInner />
		</BubbleBridgeProvider>
	);
}

function BubbleInner() {
	const dotRefs = useRef<(HTMLSpanElement | null)[]>([]);
	const pillRef = useRef<HTMLDivElement>(null);
	const fadeOutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	// `draggable` toggles the native CSS drag-region on the pill.
	const [draggable, setDraggable] = useState(true);
	// Whether to show the mic button (always_visible + both toggles on).
	// Driven by `bubble:config` from the Python backend.
	const [micButton, setMicButton] = useState(false);
	// Whether to show the dismiss '×' button. Shown whenever the bubble
	// is in `always_visible` mode (the only mode where the user needs
	// to manually dismiss the bubble — `show_on_record` auto-hides when
	// recording stops). Driven by `bubble:config`.
	const [dismissable, setDismissable] = useState(false);
	// Tracks the current `bubble_behavior` so the error-mode auto-hide
	// effect can decide whether to auto-dismiss (show_on_record) or
	// stay sticky (always_visible — the user dismisses manually via
	// the '×' button). Defaults to `show_on_record` (the documented
	// default in the Settings page) until the first `bubble:config`
	// push arrives.
	const [bubbleBehavior, setBubbleBehavior] = useState<
		"show_on_record" | "always_visible"
	>("show_on_record");

	// Lifecycle + state machine extracted to hooks.
	const _isVisible = useBubbleLifecycle(dotRefs);
	const {
		mode,
		animState,
		setAnimState,
		exitTick,
		setExitTick: _setExitTick,
		errorMessage,
		transcript,
	} = useBubbleStateMachine();
	// `_isVisible` is consumed inside useBubbleLifecycle (gates the rAF
	// loop). We acknowledge it here so eslint doesn't flag it as unused.
	void _isVisible;
	void _setExitTick;

	// The bridge centralises all `window.bubble` IPC subscriptions
	// (onShow / onHide / onSetState / onConfig / onDraggable / onLevel)
	// into one listener per channel; consumers register handlers via
	// `bridge.on(event, handler)`. Pre-refactor each hook + this
	// component called `api.onX(...)` individually — 11 separate IPC
	// listeners across the bubble package.
	const bridge = useBubbleBridge();

	// Sync `draggable` from the main process (Settings page toggle).
	useEffect(() => {
		if (!bridge) return;
		return bridge.on("draggable", (d: boolean) => setDraggable(d));
	}, [bridge]);

	// Receive bubble-relevant config from the (sandboxed) backend. The
	// bubble renderer has no `get_config`, so the Python backend pushes
	// `bubble_behavior` / `bubble_click_to_toggle` / `bubble_mic_button`
	// via the dedicated `bubble:config` channel. We show the mic button
	// only when all three conditions are met.
	//
	// (The `theme_mode` / `theme_preset` / `custom_theme` / `locale`
	// fields of the same payload are handled inside `useBubbleLifecycle`
	// → `useThemeSync`.)
	useEffect(() => {
		if (!bridge) return;

		const off = bridge.on("config", (cfg) => {
			const behavior = cfg.bubble_behavior;
			const clickToToggle = cfg.bubble_click_to_toggle;
			const micButton = cfg.bubble_mic_button;
			const enabled =
				behavior === "always_visible" &&
				micButton !== false &&
				clickToToggle !== false;
			setMicButton(enabled);
			// Dismiss button shown whenever the bubble is in
			// always_visible mode (regardless of the mic-button toggles
			// — the user needs a way to manually dismiss an
			// always-visible bubble even when the mic button is
			// disabled).
			setDismissable(behavior === "always_visible");
			// Track the behavior so the error-mode auto-hide effect
			// can decide whether to auto-dismiss.
			if (behavior === "always_visible" || behavior === "show_on_record") {
				setBubbleBehavior(behavior);
			}
		});
		return off;
	}, [bridge]);

	// Mic button click → toggle dictation. The bubble is a sandboxed
	// renderer (SEC-026) with no `python.call`, so it routes through
	// the dedicated `bubble:toggle-dictation` channel.
	const handleMicClick = useCallback(() => {
		getBubbleApi()?.toggleDictation?.();
	}, []);

	// Stop / retry button click → toggle dictation. Same channel as the
	// mic button — when recording, `toggle_dictation` stops the
	// recording and triggers transcription; when in error mode, it
	// re-arms the dictation pipeline (effectively a retry). The visual
	// affordance is differentiated in `BubbleStopButton` based on the
	// parent-supplied `mode` (stop icon vs retry icon).
	const handleStopClick = useCallback(() => {
		getBubbleApi()?.toggleDictation?.();
	}, []);

	// Dismiss button click → send `bubble:dismiss` IPC. The
	// main-process handler (in bubble-handlers.ts) routes the message
	// to `hideBubbleWindow()`. The `dismiss` method is declared
	// required on `BubbleWindowExtras` (the Tauri bridge implements it
	// via `invoke("bubble_dismiss")`), but optional chaining guards
	// the call for tests / SSR contexts where the preload hasn't run.
	const handleDismissClick = useCallback(() => {
		getBubbleApi()?.dismiss?.();
	}, []);

	// Auto-resize BrowserWindow to fit the pill content exactly.
	// `useLayoutEffect` so the resize IPC arrives before paint. The
	// effect depends on BOTH `animState` AND `mode` so resize runs when
	// the pill content size changes between modes.
	useLayoutEffect(() => {
		if (animState === "exit") return;
		const el = pillRef.current;
		if (!el) return;
		const w = Math.ceil(el.offsetWidth);
		const h = Math.ceil(el.offsetHeight);
		// `+1` magic number: `offsetWidth` / `offsetHeight` report the
		// content-box size in CSS pixels, but the BrowserWindow's
		// `setSize` (which `resizeTo` forwards to) interprets the
		// arguments as the OUTER window size including any
		// devicePixelRatio scaling. On HiDPI displays the rounded-down
		// inner size can clip the pill's anti-aliased edge by 1px,
		// causing a visible hairline. Adding 1px of slack on each axis
		// absorbs the rounding without leaving a perceptible dead
		// zone (the pill has no visible border at the pixel level).
		getBubbleApi()?.resizeTo?.(w + 1, h + 1);
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

	// Error-mode auto-hide: when the bubble is in `show_on_record`
	// behavior and enters error mode, auto-hide after
	// `ERROR_AUTO_HIDE_MS` so the pill doesn't linger over the user's
	// text field. Sticky in `always_visible` mode (the user dismisses
	// manually via the '×' button). Triggers the same exit-animation
	// flow as a regular hide: `setAnimState("exit")` → CSS
	// `animate-bubble-exit` runs → `onAnimationEnd` →
	// `handleAnimEnd` → `api.hideComplete()` → main process hides the
	// window. The `dismiss` IPC is intentionally NOT used here because
	// it skips the exit animation (the dismiss button is for instant
	// user-initiated dismissal; the auto-hide is graceful).
	useEffect(() => {
		if (mode !== "error") return;
		if (bubbleBehavior !== "show_on_record") return;
		const timer = setTimeout(() => {
			setAnimState("exit");
		}, ERROR_AUTO_HIDE_MS);
		return () => clearTimeout(timer);
	}, [mode, bubbleBehavior, setAnimState]);

	// Animation-end callback — when exit CSS transition completes, tell
	// the main process it's safe to `hide()` the BrowserWindow. After
	// the enter animation completes, re-sync the window size to the
	// pill content (handles edge cases where the initial
	// `useLayoutEffect` ran before layout settled).
	const handleAnimEnd = useCallback(() => {
		const api = getBubbleApi();
		if (animState === "exit") {
			setAnimState("");
			api?.hideComplete?.();
		} else if (animState === "enter") {
			setAnimState("");
			const el = pillRef.current;
			if (el) {
				const w = Math.ceil(el.offsetWidth);
				const h = Math.ceil(el.offsetHeight);
				// `+1` magic number: see the `useLayoutEffect` comment
				// above for the HiDPI rounding rationale.
				api?.resizeTo?.(w + 1, h + 1);
			}
		}
	}, [animState, setAnimState]);

	return (
		<output
			aria-live="polite"
			aria-atomic="true"
			// State-aware aria-label so screen-reader users hear the
			// current bubble mode ("recording" / "transcribing" /
			// "error" / "idle") instead of always hearing "recording".
			// The implementation lives in `getBubbleAriaLabel` so it
			// can be unit-tested in isolation; the previous inline
			// 7-deep ternary over `mode` is gone.
			aria-label={getBubbleAriaLabel(mode, errorMessage)}
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
					// Muted frame: theme's --border at 10% so the pill floats
					// subtly over the desktop (same treatment as the page
					// window frame in App.tsx).
					"border border-border/10",
					"bg-card text-card-foreground",
					"px-4 py-2.5",
					draggable ? "drag-region" : "no-drag",
				)}
			>
				<BubbleModeContent
					mode={mode}
					errorMessage={errorMessage}
					transcript={transcript}
					dotRefs={dotRefs}
				/>

				{micButton && (
					<BubbleMicButton mode={mode as BubbleMode} onClick={handleMicClick} />
				)}
				{(mode === "recording" || mode === "error") && (
					<BubbleStopButton
						onClick={handleStopClick}
						mode={mode === "error" ? "error" : "recording"}
					/>
				)}
				{dismissable && <BubbleDismissButton onClick={handleDismissClick} />}
			</div>
		</output>
	);
}

export default Bubble;
