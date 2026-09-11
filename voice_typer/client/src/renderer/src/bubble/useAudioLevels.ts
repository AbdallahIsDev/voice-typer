/**
 * Bubble overlay package, `useAudioLevels` hook (60fps direct-DOM
 * animation, paused when hidden).
 *
 * rAF scheduling gate: the next-frame `requestAnimationFrame` call
 * sits at the END of the callback and is gated on
 * `visibleRef.current && recordingRef.current`. When either gate is
 * closed, the loop STOPS scheduling new frames entirely. The loop is
 * re-armed (via `wake()`) from the initial mount, the `api.onShow`
 * callback, the `api.onSetState` callback, and a separate `useEffect`
 * that watches the `isVisible` prop. The visibility-watching effect
 * also cancels the in-flight frame when `isVisible` becomes false.
 *
 * `prefers-reduced-motion`: when the user has reduced motion enabled
 * (vestibular disorders, motion sensitivity, or preference), the rAF
 * loop is short-circuited, bars are rendered ONCE at a fixed
 * mid-height and no further frames are scheduled. This matches the
 * CSS-side `@media (prefers-reduced-motion: reduce)` block in
 * `index.css` that disables CSS animations: the JS-driven bar
 * animation is the bubble's most motion-heavy element, so it gets the
 * same treatment. `wake()` is also gated so a stale `onShow` callback
 * can't re-arm the loop behind the user's back.
 */
import { type RefObject, useCallback, useEffect, useRef } from "react";
import { DOT_COUNT, DOT_WEIGHTS, MAX_HEIGHT, MIN_HEIGHT } from "./constants";
import { rmsToNorm } from "./helpers";
import { useBubbleBridge } from "./useBubbleBridge";

// Mid-height (in px) used for the reduced-motion fallback render.
// Picked as the midpoint between MIN_HEIGHT (5) and MAX_HEIGHT (22) so
// the bars are visible but static.
const REDUCED_MOTION_HEIGHT = (MIN_HEIGHT + MAX_HEIGHT) / 2;

// ── Transform-based bar animation (compositor-only writes) ─────────
//
// The per-frame rAF loop animates each bar by writing
// ``transform: scaleY(...)``, NOT ``height``. A per-frame ``height``
// write forces a layout pass on the bubble pill every frame; a scale
// write runs on the compositor. This mirrors the app's LevelBar rAF
// contract (``components/feedback/LevelBar.tsx``): the loop writes the
// transform (plus the cap-radius CSS var and the compositor-only
// ``opacity``), never layout-inducing geometry.
//
// Geometry preservation: each bar element is prepared ONCE at its FULL
// box height (``MAX_HEIGHT``) with a default (center) transform origin.
// The visualizer wrapper is a fixed-height flex container that centers
// its children, so a centered ``scaleY(h / MAX_HEIGHT)`` renders the
// same centered ``h``-pixel bar the old height write produced, at every
// level, with no re-anchoring.

// Half the dot's 3px width (``w-0.75`` in BubbleVisualizer.tsx). Kept as
// a local literal for the same reason the LevelBar pins its own 3px cap:
// the width lives in the component's class list, and the counter-scale
// below must divide by the exact cap radius.
const BAR_CAP_RADIUS_PX = 1.5;

/**
 * Prepare a bar element for transform-based animation: reserve the full
 * layout-box height and install the counter-scaled cap radius.
 *
 * Idempotent and cheap to re-check: a re-mounted element (React
 * re-applies its 5px inline height on mount) is re-prepared on the next
 * frame. Returns ``true`` when the element was (re)initialized, so the
 * caller resets its per-dot easing state to ``MIN_HEIGHT``.
 */
function prepareBarElement(el: HTMLElement): boolean {
	const baseHeight = `${MAX_HEIGHT}px`;
	if (el.style.height === baseHeight) return false;
	el.style.height = baseHeight;
	// COUNTER-SCALED CAPS: a transform scales painted geometry, so a
	// fixed 1.5px cap on a 3px-wide dot compresses vertically at low
	// scales (the caps read as squared-off). Dividing the VERTICAL
	// radius by the current scale keeps the post-transform cap a
	// 1.5px half-round at every level, the same counter-scale family
	// the LevelBar uses for its right cap. The horizontal radius
	// stays a plain 1.5px (scaleY never touches the horizontal axis).
	el.style.borderRadius = `${BAR_CAP_RADIUS_PX}px / calc(${BAR_CAP_RADIUS_PX}px / max(var(--bar-scale), 0.03))`;
	return true;
}

/**
 * Write a bar's current visual height as a compositor-only transform
 * (plus the cap-radius CSS var that the prepared border-radius calc
 * consumes, a style/paint-level write, geometry stays fixed).
 * ``opacity`` is a compositor-only property and rides along.
 */
function writeBarLevel(el: HTMLElement, height: number, opacity: number): void {
	const scale = height / MAX_HEIGHT;
	el.style.transform = `scaleY(${scale})`;
	el.style.setProperty("--bar-scale", String(scale));
	el.style.opacity = `${opacity}`;
}

function prefersReducedMotion(): boolean {
	if (
		typeof window === "undefined" ||
		typeof window.matchMedia !== "function"
	) {
		return false;
	}
	return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function useAudioLevels(
	dotRefs: RefObject<(HTMLSpanElement | null)[]>,
	isVisible: boolean,
) {
	const bridge = useBubbleBridge();
	const rawLevelRef = useRef(0);
	const frameRef = useRef<number | null>(null);
	const visibleRef = useRef(isVisible);
	visibleRef.current = isVisible;
	const recordingRef = useRef(true);
	const barColorRef = useRef<string | null>(null);
	// Per-dot easing state: the last visual height (px) written for each
	// bar. The loop reads/writes this instead of parsing the DOM style
	// back, the element's inline ``height`` now holds the constant FULL
	// box height (see ``prepareBarElement``), not the animated value.
	// A ref (not effect-local state) so the easing continuity survives
	// effect re-runs, exactly like the DOM value did before the
	// transform conversion.
	const barHeightsRef = useRef<number[]>(
		Array.from({ length: DOT_COUNT }, () => MIN_HEIGHT),
	);
	// `wake` function ref (re-armed by the recording-mode effect).
	const wakeRef = useRef<(() => void) | null>(null);
	// rAF handle used to debounce `refreshBarColor` writes so a burst of
	// `MutationObserver` callbacks (e.g. theme switch flipping multiple
	// classes) coalesces into a single `getComputedStyle` read.
	const colorRefreshFrameRef = useRef<number | null>(null);

	const applyBarColor = useCallback(() => {
		const c = barColorRef.current;
		if (c === null) return;
		const dots = dotRefs.current;
		if (!dots) return;
		for (let i = 0; i < DOT_COUNT; i++) {
			const el = dots[i];
			if (el) el.style.backgroundColor = c;
		}
	}, [dotRefs]);

	const refreshBarColor = useCallback(() => {
		const rootStyle = getComputedStyle(document.documentElement);
		const c =
			rootStyle.getPropertyValue("--text-primary").trim() ||
			rootStyle.getPropertyValue("--foreground").trim() ||
			(document.documentElement.classList.contains("dark")
				? "#fff"
				: "#18181b");
		barColorRef.current = c;
		applyBarColor();
	}, [applyBarColor]);

	// Debounce `refreshBarColor` via a microtask so a burst of
	// MutationObserver callbacks (e.g. a theme preset switch that
	// toggles multiple classes / style vars in quick succession)
	// coalesces into a single `getComputedStyle` read.
	// `getComputedStyle` forces layout, so batching is important when
	// the observer fires repeatedly.
	//
	//NOTE: the original  spec called for `requestAnimationFrame`
	// here, but jsdom's rAF fires at a 60 Hz `setInterval` (≈16ms)
	// rather than `setTimeout(0)`. That breaks the existing
	// `bubble-raf-gating.test.tsx` MutationObserver test, which
	// flushes via `setTimeout(0)` and expects `getComputedStyle` to
	// have been called within that single macrotask flush.
	// `queueMicrotask` achieves the same coalescing (multiple
	// observer callbacks in the same tick → one `getComputedStyle`
	// call) while flushing before the next macrotask, so the test's
	// `await setTimeout(0)` reliably drains the debounced refresh.
	// Switching back to `requestAnimationFrame` would require either
	// updating the test to await `vi.advanceTimersByTime(16)` (with
	// fake timers) or polyfilling jsdom's rAF as `setTimeout(0)`.
	const scheduleColorRefresh = useCallback(() => {
		if (colorRefreshFrameRef.current !== null) return;
		colorRefreshFrameRef.current = 1;
		queueMicrotask(() => {
			colorRefreshFrameRef.current = null;
			refreshBarColor();
		});
	}, [refreshBarColor]);

	useEffect(() => {
		refreshBarColor();
		if (typeof MutationObserver === "undefined") return;
		const observer = new MutationObserver(() => scheduleColorRefresh());
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["class", "style"],
		});
		return () => {
			observer.disconnect();
			// No need to cancel the queued microtask, it's a no-op
			// after unmount because `refreshBarColor`'s `useCallback`
			// deps are stable, but the ref guard (`colorRefreshFrameRef`)
			// prevents duplicate scheduling on the next mount.
			colorRefreshFrameRef.current = null;
		};
	}, [refreshBarColor, scheduleColorRefresh]);

	// Combined recording-mode tracking + rAF setup + onLevel subscription.
	//
	// (Single source of truth): the bubble's `mode` is NO LONGER
	// tracked in a local closure here. `useBubbleBridge` owns the
	// authoritative mode ref, kept in lockstep with the show / hide /
	// setState event stream by the shared `nextBubbleMode` reducer —
	// updated BEFORE handlers fan out, so this hook's handlers always
	// observe the current event's resulting mode regardless of
	// registration order. This hook reads `bridge.getMode()` in its
	// handlers and mirrors just the boolean `recordingRef` for
	// synchronous rAF-loop gating. The previous duplicate `let mode`
	// closure could drift from `useBubbleStateMachine`'s React state
	// (two independent implementations of the same transition table);
	// now both consume the same reducer, so drift is impossible by
	// construction. This also means the visualizer + onLevel
	// subscription correctly stop for ALL non-recording modes,
	// including `blocked` / `cancelling` / `permission_revoked` /
	// `paste_failed` (which the old local tracker silently ignored,
	// leaving the bars animating behind a non-recording pill).
	//
	// IPC subscriptions: this hook registers handlers on the shared
	// `useBubbleBridge` emitter (one of N consumers) instead of
	// calling `api.onShow` / `api.onSetState` / `api.onLevel`
	// directly. The bridge owns the single per-event IPC listener;
	// the dynamic `onLevel` gating is delegated to
	// `bridge.setLevelActive(boolean)` so the bridge can drop the
	// underlying IPC listener when no consumer is interested in
	// audio-peak events (currently only this hook subscribes to
	// `level`).
	//
	// Mitigation applied here: the `onLevel` IPC subscription is
	// DYNAMICALLY gated on the mode being `"recording"`. Audio-peak
	// IPC events fire at ~50-60 Hz from the Python backend while the
	// recorder is running; when the bubble is in `transcribing` /
	// `idle` / `error` / `fading` (or any mid-flow) mode those events
	// are pure waste (the visualizer doesn't render those peaks).
	// Subscribing only while in recording mode saves the IPC
	// marshalling cost during the ~90% of the bubble's lifetime it
	// spends NOT recording.
	useEffect(() => {
		if (!bridge) return;

		const onLevel = (data: { rms: number; peak: number }) => {
			const norm = rmsToNorm(data.rms);
			const cur = rawLevelRef.current;
			if (norm > cur) {
				rawLevelRef.current = cur * 0.2 + norm * 0.8;
			} else {
				rawLevelRef.current = cur * 0.82 + norm * 0.18;
			}
		};
		// Register the level handler ONCE on the bridge. The
		// bridge exposes `setLevelActive(boolean)` to toggle the
		// underlying `api.onLevel` IPC subscription; the handler
		// itself stays registered for the lifetime of this effect
		// so a re-subscribe (after a temporary unsubscribe) doesn't
		// miss the handler registration window.
		const offLevel = bridge.on("level", onLevel);
		const subscribeLevel = () => {
			bridge.setLevelActive(true);
		};
		const unsubscribeLevel = () => {
			bridge.setLevelActive(false);
		};

		const sync = () => {
			const isRecording = bridge.getMode() === "recording";
			recordingRef.current = isRecording;
			// Dynamic onLevel gating, see comment above.
			if (isRecording) {
				subscribeLevel();
			} else {
				unsubscribeLevel();
			}
		};

		// Render bars at a fixed mid-height (no animation) when the user
		// has reduced motion enabled. Skips all subsequent rAF scheduling.
		const renderReducedMotion = () => {
			const dots = dotRefs.current;
			if (!dots) return;
			for (let i = 0; i < DOT_COUNT; i++) {
				const el = dots[i];
				if (!el) continue;
				// Ensure the full-box base geometry exists before the
				// static-scale write (a fresh element still carries its
				// 5px inline height).
				prepareBarElement(el);
				writeBarLevel(el, REDUCED_MOTION_HEIGHT, 0.5);
				// Keep the easing state at the rendered height so a
				// later reduced-motion → animated transition eases
				// FROM mid-height (continuity with the pre-transform
				// behavior, which read this value back from the DOM).
				barHeightsRef.current[i] = REDUCED_MOTION_HEIGHT;
			}
		};

		const animate = () => {
			// Clear the frame handle so `wake()` can re-schedule.
			frameRef.current = null;

			// If either gate is closed, do NOT schedule the next frame.
			if (!visibleRef.current || !recordingRef.current) return;

			if (prefersReducedMotion()) {
				// `prefers-reduced-motion`: render bars ONCE at a fixed
				// mid-height and STOP the rAF loop. The loop re-arms via
				// `wake()` when a gate flips: the visibility-watching
				// effect calls `wakeRef.current?.()` on `isVisible -> true`,
				// and `bridge.on("show")` / `bridge.on("setState")` call
				// `wake()` on recording-mode transitions. `wake()` itself
				// calls `renderReducedMotion()` before scheduling, so a
				// re-arm produces exactly one frame (which then stops again)
				// -- the bars stay at the static mid-height without a
				// 60 fps spin.
				renderReducedMotion();
				return;
			}

			const dots = dotRefs.current;
			if (!dots) {
				frameRef.current = requestAnimationFrame(animate);
				return;
			}

			if (barColorRef.current === null) {
				refreshBarColor();
			}

			const level = rawLevelRef.current;

			for (let i = 0; i < DOT_COUNT; i++) {
				const el = dots[i];
				if (!el) continue;
				// A freshly (re)mounted element re-anchors its easing
				// state at MIN_HEIGHT, matching the pre-transform
				// behavior, which fell back to the element's 5px inline
				// height when no animated value had been written yet.
				if (prepareBarElement(el)) {
					barHeightsRef.current[i] = MIN_HEIGHT;
				}
				const weight = DOT_WEIGHTS[i] ?? 1;
				const target = MIN_HEIGHT + level * weight * (MAX_HEIGHT - MIN_HEIGHT);
				const cur = barHeightsRef.current[i] ?? MIN_HEIGHT;
				const next = cur + (target - cur) * 0.36;
				const height = Math.max(MIN_HEIGHT, next);
				barHeightsRef.current[i] = height;
				// Compositor-only writes, the per-frame set is
				// transform + the cap-radius var + opacity (no
				// layout-inducing geometry; see the module-level
				// transform-contract note).
				writeBarLevel(el, height, 0.35 + level * 0.65);
			}

			// Schedule the next frame ONLY if both gates are still open.
			if (visibleRef.current && recordingRef.current) {
				frameRef.current = requestAnimationFrame(animate);
			}
		};

		// `wake` function, idempotent (re)starter. Gated on
		// `prefers-reduced-motion` so a stale `onShow` callback cannot
		// re-arm the rAF loop behind the user's back. The reduced-motion
		// fallback render is re-applied here so the bars stay at the
		// fixed mid-height even after a hide → show cycle.
		const wake = () => {
			if (prefersReducedMotion()) {
				renderReducedMotion();
				// Fall through and schedule ONE frame so the gates /
				// change event are observable. `animate()` will call
				// `renderReducedMotion()` once more (a no-op since the
				// styles are already set) and then return WITHOUT
				// scheduling the next frame -- the loop dies after one
				// frame, which is the intended reduced-motion behavior.
			}
			if (frameRef.current !== null) return;
			if (!visibleRef.current || !recordingRef.current) return;
			frameRef.current = requestAnimationFrame(animate);
		};
		wakeRef.current = wake;

		// Re-arm the loop when the user toggles `prefers-reduced-motion`
		// at runtime. Without this listener, toggling reduced-motion ON
		// when the loop is already dead (after the initial
		// `renderReducedMotion()` call returned without scheduling the
		// next frame) would NOT re-render the bars -- a visual
		// downgrade. `wake()` calls `renderReducedMotion()` + schedules
		// ONE frame (which then stops again), so the toggle is handled
		// without spinning the loop at 60 fps.
		const reducedMotionMql =
			typeof window !== "undefined" && typeof window.matchMedia === "function"
				? window.matchMedia("(prefers-reduced-motion: reduce)")
				: null;
		const handleReducedMotionChange = () => {
			wake();
		};
		reducedMotionMql?.addEventListener("change", handleReducedMotionChange);

		const offShow = bridge.on("show", () => {
			// The bridge's mode ref is already updated for this event
			// (show → recording, unless transcribing), just re-sync
			// the recording gate + level subscription.
			sync();
			wake();
		});
		const offSetState = bridge.on("setState", () => {
			// Mode ref already reflects this setState transition (see
			// the single-source-of-truth comment above), re-sync, then re-arm the rAF
			// loop when the new mode is recording.
			sync();
			if (bridge.getMode() === "recording") wake();
		});

		// Establish the initial subscription state for `onLevel`.
		// The default `mode` is `"recording"`, so on mount this
		// subscribes immediately, preserving the pre-refactor
		// behavior where `onLevel` was always subscribed while the
		// bubble was visible.
		sync();

		wake();

		return () => {
			offShow();
			offSetState();
			offLevel();
			unsubscribeLevel();
			reducedMotionMql?.removeEventListener(
				"change",
				handleReducedMotionChange,
			);
			wakeRef.current = null;
			if (frameRef.current !== null) {
				cancelAnimationFrame(frameRef.current);
				frameRef.current = null;
			}
		};
	}, [bridge, dotRefs, refreshBarColor]);

	// Visibility-watching effect, cancel on hide, re-arm on show.
	useEffect(() => {
		if (!isVisible) {
			if (frameRef.current !== null) {
				cancelAnimationFrame(frameRef.current);
				frameRef.current = null;
			}
			return;
		}
		wakeRef.current?.();
	}, [isVisible]);
}
