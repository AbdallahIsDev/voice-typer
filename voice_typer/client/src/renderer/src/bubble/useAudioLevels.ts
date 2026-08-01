/**
 * Bubble overlay package — `useAudioLevels` hook (60fps direct-DOM
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
 * loop is short-circuited — bars are rendered ONCE at a fixed
 * mid-height and no further frames are scheduled. This matches the
 * CSS-side `@media (prefers-reduced-motion: reduce)` block in
 * `index.css` that disables CSS animations: the JS-driven bar
 * animation is the bubble's most motion-heavy element, so it gets the
 * same treatment. `wake()` is also gated so a stale `onShow` callback
 * can't re-arm the loop behind the user's back.
 */
import { type RefObject, useCallback, useEffect, useRef } from "react";
import {
	type BubbleMode,
	DOT_COUNT,
	DOT_WEIGHTS,
	MAX_HEIGHT,
	MIN_HEIGHT,
} from "./constants";
import { rmsToNorm } from "./helpers";

// Mid-height (in px) used for the reduced-motion fallback render.
// Picked as the midpoint between MIN_HEIGHT (5) and MAX_HEIGHT (22) so
// the bars are visible but static.
const REDUCED_MOTION_HEIGHT = (MIN_HEIGHT + MAX_HEIGHT) / 2;

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
	const rawLevelRef = useRef(0);
	const frameRef = useRef<number | null>(null);
	const visibleRef = useRef(isVisible);
	visibleRef.current = isVisible;
	const recordingRef = useRef(true);
	const barColorRef = useRef<string | null>(null);
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
			// No need to cancel the queued microtask — it's a no-op
			// after unmount because `refreshBarColor`'s `useCallback`
			// deps are stable, but the ref guard (`colorRefreshFrameRef`)
			// prevents duplicate scheduling on the next mount.
			colorRefreshFrameRef.current = null;
		};
	}, [refreshBarColor, scheduleColorRefresh]);

	// Combined recording-mode tracking + rAF setup + onLevel subscription.
	//
	// Known issue (duplicate mode tracker): the bubble's `mode` is
	// tracked TWICE — once here in a local `let mode` closure variable,
	// and once in `useBubbleStateMachine` (the source of truth that
	// drives the rendered pill content). The two trackers can drift if
	// an `onSetState` event arrives during a render commit boundary.
	// The closure tracker is necessary because `useAudioLevels` needs
	// synchronous access to the mode to gate the rAF loop without
	// re-subscribing on every mode change (which would cancel and
	// re-arm the loop, causing visible stutter). A proper fix would
	// lift the recording flag into a shared ref owned by
	// `useBubbleStateMachine` and consumed here via a ref getter —
	// deferred to a future refactor because it touches the
	// state-machine's public surface and would require coordinated
	// test updates across both hooks.
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api) return;

		let mode: BubbleMode = "recording";
		const sync = () => {
			recordingRef.current = mode === "recording";
		};

		const onLevel = (data: { rms: number; peak: number }) => {
			const norm = rmsToNorm(data.rms);
			const cur = rawLevelRef.current;
			if (norm > cur) {
				rawLevelRef.current = cur * 0.2 + norm * 0.8;
			} else {
				rawLevelRef.current = cur * 0.82 + norm * 0.18;
			}
		};
		const offLevel = api.onLevel?.(onLevel);

		// Render bars at a fixed mid-height (no animation) when the user
		// has reduced motion enabled. Skips all subsequent rAF scheduling.
		const renderReducedMotion = () => {
			const dots = dotRefs.current;
			if (!dots) return;
			for (let i = 0; i < DOT_COUNT; i++) {
				const el = dots[i];
				if (!el) continue;
				el.style.height = `${REDUCED_MOTION_HEIGHT}px`;
				el.style.opacity = "0.6";
			}
		};

		const animate = () => {
			// Clear the frame handle so `wake()` can re-schedule.
			frameRef.current = null;

			// `prefers-reduced-motion`: render bars ONCE at a fixed
			// mid-height and skip further rAF scheduling. The CSS-side
			// `@media (prefers-reduced-motion: reduce)` block in
			// `index.css` disables the wider animation policy; this JS
			// gate ensures the rAF loop itself stops spinning (the CSS
			// block can't reach into JS-driven direct-DOM writes).
			if (prefersReducedMotion()) {
				renderReducedMotion();
				return;
			}

			// If either gate is closed, do NOT schedule the next frame.
			if (!visibleRef.current || !recordingRef.current) return;

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
				const weight = DOT_WEIGHTS[i] ?? 1;
				const target = MIN_HEIGHT + level * weight * (MAX_HEIGHT - MIN_HEIGHT);
				const cur = parseFloat(el.style.height) || MIN_HEIGHT;
				const next = cur + (target - cur) * 0.36;
				el.style.height = `${Math.max(MIN_HEIGHT, next)}px`;
				el.style.opacity = `${0.35 + level * 0.65}`;
			}

			// Schedule the next frame ONLY if both gates are still open.
			if (visibleRef.current && recordingRef.current) {
				frameRef.current = requestAnimationFrame(animate);
			}
		};

		// `wake` function — idempotent (re)starter. Gated on
		// `prefers-reduced-motion` so a stale `onShow` callback cannot
		// re-arm the rAF loop behind the user's back. The reduced-motion
		// fallback render is re-applied here so the bars stay at the
		// fixed mid-height even after a hide → show cycle.
		const wake = () => {
			if (prefersReducedMotion()) {
				renderReducedMotion();
				return;
			}
			if (frameRef.current !== null) return;
			if (!visibleRef.current || !recordingRef.current) return;
			frameRef.current = requestAnimationFrame(animate);
		};
		wakeRef.current = wake;

		const offShow = api.onShow?.(() => {
			mode = mode === "transcribing" ? "transcribing" : "recording";
			sync();
			wake();
		});
		const offSetState = api.onSetState?.((state) => {
			if (mode === "fading") return;
			if (
				state === "transcribing" ||
				state === "idle" ||
				state === "recording" ||
				state === "error"
			) {
				mode = state;
				sync();
				if (mode === "recording") wake();
			}
		});

		wake();

		return () => {
			offShow?.();
			offSetState?.();
			offLevel?.();
			wakeRef.current = null;
			if (frameRef.current !== null) {
				cancelAnimationFrame(frameRef.current);
				frameRef.current = null;
			}
		};
	}, [dotRefs, refreshBarColor]);

	// Visibility-watching effect — cancel on hide, re-arm on show.
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
