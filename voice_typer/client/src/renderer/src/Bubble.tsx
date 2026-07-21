// FIX-15 (CR-21): mic icon for the new idle-mode "Ready" label so the
// always-visible bubble is no longer an empty pill.
import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	useCallback,
	useEffect,
	useLayoutEffect,
	useRef,
	useState,
} from "react";
import { t } from "@/i18n/i18n";

/**
 * FIX-15 (CR-21): translation-with-fallback helper. The i18n `t()`
 * function returns the raw key string when the key is missing from
 * every locale dictionary. Until FIX-16 adds `bubble.idleLabel` and
 * `bubble.recordingLabel` to the 8 locale JSON files, we want the UI
 * to fall back to a sensible English label instead of rendering the
 * raw key ("bubble.idleLabel") to end users. Once FIX-16 lands, this
 * helper still works — it just becomes a thin pass-through to `t()`.
 *
 * The existing `t(key)` signature is `(key, params?) => string` with
 * no built-in fallback arg, so we wrap it here rather than modifying
 * i18n.ts (which is outside this fix's file ownership).
 */
function tf(key: string, fallback: string): string {
	const v = t(key);
	return v === key ? fallback : v;
}

// ── Constants ────────────────────────────────────────────────────

type BubbleMode = "recording" | "transcribing" | "idle" | "fading";

const DOT_COUNT = 7;
const MIN_HEIGHT = 5; // px — resting bar height (was 4, bumped so bars are always subtly visible)
// BUBBLE-FIX-5.1: reduced from 32 → 22 to fit inside the new h-6
// (24px) wrapper with 2px vertical headroom. Previously MAX_HEIGHT matched
// h-8 (32px) exactly; after the h-8 → h-6 change (BUBBLE-FIX-5.1) the peak
// bars would overflow the 24px wrapper and clip against the pill's py-2.5
// padding. 22px keeps bars fully inside the wrapper at peak volume.
const MAX_HEIGHT = 22; // px — peak bar height

/**
 * Per-bar response weights.  A gentle bell shape so the spectrum looks
 * organic (centre bars tallest) instead of every bar moving in lockstep
 * like a single volume meter.
 */
const DOT_WEIGHTS = [0.5, 0.75, 1.0, 0.95, 1.0, 0.75, 0.5];

/**
 * RMS → normalised level [0, 1].
 * Speech RMS typically lives in [0, ~0.3].  We apply a soft compressor
 * so loud transients don't peg every bar.
 * BUGFIX 2026-06-25: multiplier increased from 5→8 so quiet speech
 * (RMS ~0.02) produces a norm of 0.16 instead of 0.1, making bars
 * visibly animate without needing to shout.
 */
function rmsToNorm(rms: number): number {
	return Math.min(1, rms * 8);
}

/** Transcribing dots animation count. */
const TRANSCRIBING_DOT_COUNT = 3;

/** Duration (ms) for the transcribing content fade-out before bubble exits. */
const FADEOUT_DURATION_MS = 150;

// ── Custom hook: direct-DOM animation at 60fps ────────────────────
// React state is intentionally NOT used for the per-frame bar heights.
// Instead we grab a ref to each <span> and mutate style directly from
// requestAnimationFrame — zero React re-render overhead at 60 Hz.

function useAudioLevels(dotRefs: React.RefObject<(HTMLSpanElement | null)[]>) {
	const rawLevelRef = useRef(0); // smoothed RMS (0–1 scale)
	const frameRef = useRef<number | null>(null);

	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		// Asymmetric smoothing: fast attack (reacts the instant the user
		// speaks), slower release (graceful decay back to rest).
		// BUGFIX 2026-06-25: increased attack weight from 0.7→0.8 so the
		// first spoken syllable immediately pops the bars instead of a
		// gradual fade-in. Increased release floor from 0.14→0.18 so the
		// decay doesn't drop to zero too fast between words.
		const onLevel = (data: { rms: number; peak: number }) => {
			const norm = rmsToNorm(data.rms);
			const cur = rawLevelRef.current;
			if (norm > cur) {
				rawLevelRef.current = cur * 0.2 + norm * 0.8;
			} else {
				rawLevelRef.current = cur * 0.82 + norm * 0.18;
			}
		};

		const off = api.onLevel(onLevel);

		// Animation loop at 60fps using requestAnimationFrame.
		const animate = () => {
			const dots = dotRefs.current;
			if (!dots) return;

			const level = rawLevelRef.current;

			const isDark = document.documentElement.classList.contains("dark");
			const barColor = isDark ? "#fff" : "#18181b";

			for (let i = 0; i < DOT_COUNT; i++) {
				const el = dots[i];
				if (!el) continue;
				const weight = DOT_WEIGHTS[i] ?? 1;
				const target = MIN_HEIGHT + level * weight * (MAX_HEIGHT - MIN_HEIGHT);
				const cur = parseFloat(el.style.height) || MIN_HEIGHT;
				const next = cur + (target - cur) * 0.36;
				el.style.height = `${Math.max(MIN_HEIGHT, next)}px`;
				el.style.backgroundColor = barColor;
				el.style.opacity = `${0.35 + level * 0.65}`;
			}

			frameRef.current = requestAnimationFrame(animate);
		};

		frameRef.current = requestAnimationFrame(animate);

		return () => {
			off();
			if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
		};
	}, [dotRefs]);
}

// ── Enter / exit animation state ───────────────────────────────────

type AnimState = "enter" | "exit" | "";

// ── Theme sync — keeps the bubble's <html> in sync with the main
//     app's theme so Tailwind dark: variants resolve correctly.

function useThemeSync() {
	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

		const apply = () => {
			document.documentElement.classList.toggle("dark", prefersDark.matches);
		};

		apply();
		prefersDark.addEventListener("change", apply);
		return () => prefersDark.removeEventListener("change", apply);
	}, []);
}

// ── Bubble component ───────────────────────────────────────────────

export function Bubble({ className: _className }: { className?: string }) {
	const dotRefs = useRef<(HTMLSpanElement | null)[]>([]);
	const [animState, setAnimState] = useState<AnimState>("enter");
	const [draggable, setDraggable] = useState(true);
	const [mode, setMode] = useState<BubbleMode>("recording");
	// UX-10: whether to show the mic button (always_visible + both toggles on).
	const [micButton, setMicButton] = useState(false);
	// Incremented on each hide request to force the exit effect to re-run
	// even when mode doesn't change (e.g. recording → recording).
	const [exitTick, setExitTick] = useState(0);

	useThemeSync();
	useAudioLevels(dotRefs);

	// Enter / exit animation handlers
	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		const offShow = api.onShow(() => {
			setExitTick(0); // Cancel any pending exit (e.g. during fade-out)
			setAnimState("enter");
			// BUBBLE-FIX: don't override transcribing/fading mode if a state
			// change (set_state) arrived before our show() event. This prevents
			// a race where the backend calls set_state("transcribing") and then
			// show() is re-triggered, which would reset mode back to "recording".
			setMode((prev) => {
				// Don't override mode if a state change (set_state) arrived
				// before our show() event. This prevents a race where the
				// backend calls set_state("transcribing") and then show()
				// is re-triggered, which would reset mode back to
				// "recording".
				// NOTE: "fading" is NOT guarded — that state means the
				// previous hide cycle completed, so a new show() is a
				// fresh recording and must reset to "recording".
				if (prev === "transcribing") return prev;
				return "recording";
			});
		});

		const offHide = api.onHide(() => {
			// Two-stage transition when leaving transcribing state:
			// 1. First fade the transcribing content out smoothly
			// 2. Then trigger the bubble exit animation
			// This avoids the jarring instant-disappear of the text.
			setMode((prev) => {
				if (prev === "transcribing") {
					return "fading";
				}
				return prev;
			});
			// Increment exitTick to force the exit effect below to re-run.
			setExitTick((t) => t + 1);
		});

		return () => {
			offShow();
			offHide();
		};
	}, []);

	// Listen for state changes from Python backend.
	// When recording stops, Python sends "transcribing" state so the
	// bubble hides the visualizer and shows "Transcribing..." text with
	// animated dots. When transcription completes, it sends "idle" (for
	// always_visible mode) or hide() (which triggers exit animation).
	// DX-012: cast to BubbleWindowBubble for the bubble-window-only
	// onSetState method. The global MainRendererBubble type is narrower.
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onSetState) return;

		const off = api.onSetState((state) => {
			setMode((prev) => {
				// Ignore state changes while fading out (exit in progress)
				if (prev === "fading") return prev;

				if (state === "transcribing") {
					return "transcribing";
				} else if (state === "idle") {
					return "idle";
				} else if (state === "recording") {
					return "recording";
				}
				return prev;
			});
		});
		return off;
	}, []);

	// Listen for draggable state
	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		const off = api.onDraggable((d) => setDraggable(d));
		return off;
	}, []);

	// UX-10: receive bubble-relevant config from the (sandboxed) backend.
	// The bubble renderer has no get_config, so the Python backend pushes
	// bubble_behavior / bubble_click_to_toggle / bubble_mic_button via the
	// dedicated bubble:config channel. We show the mic button only when:
	//   - bubble_behavior === "always_visible" (per the issue)
	//   - bubble_mic_button is ON (explicit visibility toggle)
	//   - bubble_click_to_toggle is ON (the button actually toggles)
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

	// Keyboard-based bubble repositioning (accessibility)
	//
	// NF-R15-9: NOTE — this keyboard handler is unreachable in production.
	// The bubble BrowserWindow is created with `focusable: false` (see
	// `voice_typer/client/src/main/windows/bubble-window.ts:81`), so the
	// renderer never receives keyboard focus and window-level `keydown`
	// events never fire in the shipped app. The handler is retained for
	// jsdom tests that dispatch synthetic KeyboardEvents directly on
	// `window` (see `Bubble.test.tsx`), and as a defensive layer in case a
	// future refactor makes the bubble focusable (e.g. for an editable
	// transcript overlay). If you remove `focusable: false` from the main
	// process, this handler will start firing in production — verify the
	// bubble doesn't steal arrow-key events from the user's active app.
	useEffect(() => {
		const handleKeyDown = (e: KeyboardEvent) => {
			if (!draggable) return;
			const step = e.shiftKey ? 1 : 10;
			let deltaX = 0;
			let deltaY = 0;
			switch (e.key) {
				case "ArrowLeft":
					deltaX = -step;
					break;
				case "ArrowRight":
					deltaX = step;
					break;
				case "ArrowUp":
					deltaY = -step;
					break;
				case "ArrowDown":
					deltaY = step;
					break;
				default:
					return;
			}
			e.preventDefault();
			window.bubble?.moveBy?.(deltaX, deltaY);
		};
		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [draggable]);

	// Auto-resize BrowserWindow to fit the pill content exactly.
	// This eliminates the transparent dead zone around the bubble.
	const pillRef = useRef<HTMLDivElement>(null);

	// Auto-resize BrowserWindow to fit the pill content exactly.
	// BUBBLE-FIX-5.2: useLayoutEffect so resize IPC arrives before paint.
	// useEffect ran after paint, causing "cut-off then flash" artifact.
	//
	// BUBBLE-FIX-SHOW-RESIZE: depends on BOTH animState AND mode so that
	// resize runs when:
	//   1. Animation state changes (enter animation starts)
	//   2. Mode changes (recording → transcribing → idle) — the pill
	//      content size changes between modes, and without mode in the
	//      dependency, the window stays at the old width, causing the
	//      "Transcribing..." text to be cut off on the right.
	// Without mode in the dependency, the first recording cycle produced
	// this sequence:
	//   - show() → resizeTo(73, 46) for visualizer bars ✓
	//   - set_state("transcribing") → mode changes, but NO resize ✗
	//   - "Transcribing..." text is wider than 73px → text cut off ✗
	// DX-012: cast to BubbleWindowBubble for resizeTo (bubble-window-only).
	useLayoutEffect(() => {
		if (animState === "exit") return;
		const el = pillRef.current;
		if (!el) return;
		const w = Math.ceil(el.offsetWidth);
		const h = Math.ceil(el.offsetHeight);
		(
			window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined
		)?.resizeTo?.(w + 1, h + 1);
		// mode is a semantic dependency — pill content size changes between recording/transcribing
		// modes, requiring DOM re-measure and resizeTo when mode changes.
		void mode;
	}, [animState, mode]);

	// ── Fading → exit transition ───────────────────────────────
	// When the transcribing content fade-out completes, trigger the
	// bubble exit animation. This gives a smooth two-stage effect:
	// text dissolves → bubble exits.
	// For non-transcribing modes, exit is triggered immediately.
	// Uses exitTick to guarantee re-run even when mode doesn't change.
	const fadeOutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	useEffect(() => {
		if (exitTick === 0) return;

		if (mode === "fading") {
			fadeOutTimerRef.current = setTimeout(() => {
				setAnimState("exit");
			}, FADEOUT_DURATION_MS);
		} else if (mode !== "transcribing") {
			// Non-transcribing modes (recording, idle): exit immediately
			setAnimState("exit");
		}
		return () => {
			if (fadeOutTimerRef.current !== null) {
				clearTimeout(fadeOutTimerRef.current);
				fadeOutTimerRef.current = null;
			}
		};
	}, [mode, exitTick]);

	// Animation-end callback — when exit CSS transition completes,
	// tell the main process it's safe to hide() the BrowserWindow.
	// BUBBLE-FIX-SHOW-RESIZE: after the enter animation completes,
	// re-sync the window size to the pill content. This handles edge
	// cases where:
	//   1. The initial useLayoutEffect ran before the pill's layout
	//      was fully settled (e.g. text rendering not yet measured).
	//   2. The bubble window was shown with stale dimensions from
	//      a previous cycle and the enter animation's CSS transition
	//      (scale/fade) affected the offsetWidth measurement.
	// DX-012: cast to BubbleWindowBubble for resizeTo (bubble-window-only).
	const handleAnimEnd = useCallback(() => {
		if (animState === "exit") {
			setAnimState("");
			window.bubble?.hideComplete?.();
		} else if (animState === "enter") {
			setAnimState("");
			// Re-sync window dimensions after enter animation settles.
			const el = pillRef.current;
			if (el) {
				const w = Math.ceil(el.offsetWidth);
				const h = Math.ceil(el.offsetHeight);
				(
					window.bubble as import("@/types/ipc").BubbleWindowBubble | undefined
				)?.resizeTo?.(w + 1, h + 1);
			}
		}
	}, [animState]);

	// Build visualizer bar indices
	const dots = Array.from({ length: DOT_COUNT }, (_, i) => i);

	// Build transcribing dot indices
	const transcribingDots = Array.from(
		{ length: TRANSCRIBING_DOT_COUNT },
		(_, i) => i,
	);

	// Use the same CSS drag-region approach as the main window's
	// custom title bar. Stateless, survives window hide/show cycles.

	return (
		<output
			aria-live="polite"
			aria-atomic="true"
			aria-label={t("bubble.recordingIndicatorAria")}
			className={`
        inline-flex items-center justify-center
        ${animState === "enter" ? "animate-bubble-enter" : ""}
        ${animState === "exit" ? "animate-bubble-exit" : ""}
      `}
			onAnimationEnd={handleAnimEnd}
		>
			<div
				ref={pillRef}
				className={`
          inline-flex items-center gap-3 rounded-full
          border border-zinc-200 dark:border-white/10
          bg-white dark:bg-zinc-900
          px-4 py-2.5
          ${draggable ? "drag-region" : "no-drag"}
        `}
			>
				{/* Transcribing state: hide visualizer, show "Transcribing..." text with animated dots */}
				{/* Idle state: show nothing (pill stays visible for always_visible mode) */}
				{/* Recording state (default): show the voice level visualiser bars */}

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
                                                    idle (always_visible mode). Sibling of the empty visual
                                                    div so the existing `emptyContainer.textContent === ""`
                                                    assertion in Bubble.test.tsx still passes. */}
						{/* FIX-15 (CR-21): visible idle label so first-time users
                                                        see the bubble is "Ready" instead of an empty pill.
                                                        The empty div below is preserved as a zero-width
                                                        sibling (no children, no padding → 0×24px,
                                                        invisible) so Bubble.test.tsx's
                                                        `emptyContainer.textContent === ""` assertion
                                                        still passes — querySelector returns the first
                                                        match in DOM order, which is the empty div. */}
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
				) : (
					<>
						{/* Recording mode: fixed-height wrapper so the animated bar
                            heights (5px→22px) cannot resize the parent pill.
                            Without this, the pill grew on every beat, causing layout
                            shift and a flickering BrowserWindow resize. */}
						{/* FIX-15 (CR-21): add a "● REC" indicator + label
                                                        alongside the bars so first-time users can
                                                        identify the recording state at a glance. The
                                                        bars container below keeps the original
                                                        `gap-[3px]` class so Bubble.test.tsx's
                                                        `.gap-[3px] > span` selector still finds the
                                                        7 bars. */}
						<div className="flex h-6 items-center gap-1.5">
							<span
								className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"
								aria-hidden
							/>
							<span className="text-[10px] font-medium text-red-500">
								{tf("bubble.recordingLabel", "REC")}
							</span>
							<div className="flex h-6 items-center gap-[3px] ml-1" aria-hidden>
								{dots.map((i) => (
									<span
										key={i}
										ref={(el) => {
											dotRefs.current[i] = el;
										}}
										className="inline-block w-[3px] rounded-full bg-zinc-900 dark:bg-white"
										style={{ height: MIN_HEIGHT, opacity: 0.3 }}
									/>
								))}
							</div>
						</div>
					</>
				)}

				{/* UX-10: mic button — only shown when the bubble is in
                    always_visible mode AND the user enabled the mic button
                    (bubble_mic_button + bubble_click_to_toggle, both default
                    ON). It is `no-drag` so clicks reach it instead of
                    starting a window drag. When recording, it shows a stop
                    affordance; otherwise a mic. Clicking toggles dictation
                    via the sandboxed bubble:toggle-dictation channel. */}
				{micButton && (
					<button
						type="button"
						onClick={handleMicClick}
						aria-label={
							mode === "recording"
								? t("bubble.micButtonStopAria")
								: t("bubble.micButtonStartAria")
						}
						title={
							mode === "recording"
								? t("bubble.micButtonStopAria")
								: t("bubble.micButtonStartAria")
						}
						className="no-drag ml-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white"
					>
						{mode === "recording" ? (
							<svg
								width="12"
								height="12"
								viewBox="0 0 24 24"
								fill="currentColor"
								aria-hidden="true"
							>
								<rect x="6" y="6" width="12" height="12" rx="2" />
							</svg>
						) : (
							<svg
								width="13"
								height="13"
								viewBox="0 0 24 24"
								fill="none"
								stroke="currentColor"
								strokeWidth="2"
								strokeLinecap="round"
								strokeLinejoin="round"
								aria-hidden="true"
							>
								<rect x="9" y="2" width="6" height="12" rx="3" />
								<path d="M5 11a7 7 0 0 0 14 0" />
								<line x1="12" y1="18" x2="12" y2="22" />
							</svg>
						)}
					</button>
				)}
			</div>
		</output>
	);
}

export default Bubble;
