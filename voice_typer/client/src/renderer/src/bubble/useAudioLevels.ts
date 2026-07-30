/**
 * Bubble overlay package — useAudioLevels hook (60fps direct-DOM
 * animation, paused when hidden).
 *
 * AB-39 (rAF scheduling gate): the next-frame requestAnimationFrame
 * call has moved to the END of the callback and is gated on
 * visibleRef.current && recordingRef.current. When either gate is
 * closed, the loop STOPS scheduling new frames entirely. The loop
 * is re-armed (via wake()) from the initial mount, the api.onShow
 * callback, the api.onSetState callback, and a separate useEffect
 * that watches the isVisible prop. The visibility-watching effect
 * also cancels the in-flight frame when isVisible becomes false.
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
	// AB-39: wake function ref.
	const wakeRef = useRef<(() => void) | null>(null);

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

	useEffect(() => {
		refreshBarColor();
		if (typeof MutationObserver === "undefined") return;
		const observer = new MutationObserver(() => refreshBarColor());
		observer.observe(document.documentElement, {
			attributes: true,
			attributeFilter: ["class", "style"],
		});
		return () => observer.disconnect();
	}, [refreshBarColor]);

	// AB-39: combined recording-mode tracking + rAF setup + onLevel subscription.
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

		const animate = () => {
			// AB-39: clear the frame handle so wake() can re-schedule.
			frameRef.current = null;
			// AB-39: if either gate is closed, do NOT schedule the next frame.
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

			// AB-39: schedule the next frame ONLY if both gates are still open.
			if (visibleRef.current && recordingRef.current) {
				frameRef.current = requestAnimationFrame(animate);
			}
		};

		// AB-39: wake function — idempotent (re)starter.
		const wake = () => {
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

	// AB-39: visibility-watching effect — cancel on hide, re-arm on show.
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
