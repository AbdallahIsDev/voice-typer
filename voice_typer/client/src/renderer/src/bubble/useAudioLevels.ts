/**
 * Bubble overlay package — `useAudioLevels` hook (60fps direct-DOM
 * animation, paused when hidden).
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16).
 *
 * React state is intentionally NOT used for the per-frame bar heights;
 * we grab a ref to each <span> and mutate style directly from
 * requestAnimationFrame — zero React re-render overhead at 60 Hz.
 *
 * PVT (rAF pause): the rAF loop now early-returns when the bubble is
 * hidden. Previously, even when the BrowserWindow was hidden, the loop
 * kept computing bar heights and calling getComputedStyle 60 times a
 * second. We keep the loop alive (so it resumes instantly on show())
 * but skip the DOM work while `visibleRef.current === false`.
 *
 * rAF recording-gate + barColor cache: the loop now ALSO early-
 * returns when the bubble is not in `mode === "recording"` — the
 * visualizer bars aren't mounted in idle/transcribing/error mode, so
 * the per-frame `getComputedStyle` + style writes were pure waste
 * (1.8–3 % of one core continuously in `always_visible` idle). The
 * recording flag is mirrored locally from the same `window.bubble`
 * events that drive `useBubbleStateMachine` (Bubble.tsx owns the
 * authoritative state machine; this hook keeps a parallel boolean so
 * it can gate without a round-trip through props).
 *
 * barColor cache: the `--text-primary` / `--foreground` CSS
 * vars are now read ONCE on first frame + whenever the document's
 * `class` / `style` attribute changes (theme switch, dark/light
 * toggle, OS prefers-color-scheme flip). The cached color is applied
 * to every dot via a `useEffect`-driven helper, NOT per-frame. The
 * previous implementation re-read `getComputedStyle` and re-wrote
 * `el.style.backgroundColor` 60 times a second even though the value
 * only changes on theme switch.
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

	// mirror of `useBubbleStateMachine`'s `mode === "recording"`
	// flag. Updated by the same `window.bubble` events so the rAF loop
	// can pause per-frame DOM work in idle/transcribing/error mode
	// without an extra prop drill. Default `true` matches the state
	// machine's initial `mode = "recording"`.
	const recordingRef = useRef(true);

	// cached bar color. `null` until the first frame reads it;
	// afterwards refreshed by the MutationObserver effect below.
	const barColorRef = useRef<string | null>(null);

	// apply the cached bar color to every currently-mounted dot.
	// Called on first frame, on theme change (via MutationObserver), and
	// whenever new dots mount (the rAF loop catches them on the next
	// frame).
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

	// read the bar color from CSS vars. Hoisted out of the rAF
	// loop — only called on first frame (from inside the loop, when
	// `barColorRef.current === null`) and from the MutationObserver
	// below (when the document's class/style changes).
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

	// invalidate the barColor cache when the document's class or
	// style attribute changes. This covers every theme-switch path:
	// `useThemeSync` toggles `.dark` + calls `applyThemeVars` (which
	// writes CSS vars to `document.documentElement.style`), and the OS
	// prefers-color-scheme listener (also in `useThemeSync`) toggles
	// `.dark` on OS-level flips. `attributeFilter: ["class", "style"]`
	// avoids firing on unrelated attribute changes (e.g. `dir`).
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

	// mirror the bubble mode from the same events that drive
	// `useBubbleStateMachine` (Bubble.tsx). We only need the boolean
	// "is recording" flag — the rAF loop doesn't care about idle vs
	// transcribing vs error (all pause the loop). The transition rules
	// mirror the state machine's `onShow` / `onSetState` handlers.
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onShow || !api?.onSetState) return;

		// Local mirror of the state machine's `mode`. Default matches
		// `useBubbleStateMachine`'s `useState<BubbleMode>("recording")`.
		let mode: BubbleMode = "recording";
		const sync = () => {
			recordingRef.current = mode === "recording";
		};

		const offShow = api.onShow(() => {
			// State machine: `prev === "transcribing" ? prev : "recording"`.
			mode = mode === "transcribing" ? "transcribing" : "recording";
			sync();
		});
		const offSetState = api.onSetState((state) => {
			// State machine: ignore while fading.
			if (mode === "fading") return;
			if (
				state === "transcribing" ||
				state === "idle" ||
				state === "recording" ||
				state === "error"
			) {
				mode = state;
				sync();
			}
		});
		return () => {
			offShow();
			offSetState();
		};
	}, []);

	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api) return;

		// Asymmetric smoothing: fast attack, slower release.
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

		const animate = () => {
			// Always schedule the next frame so the loop resumes instantly
			// when the bubble becomes visible / re-enters recording mode.
			frameRef.current = requestAnimationFrame(animate);

			// Pause DOM work while the bubble window is hidden.
			if (!visibleRef.current) return;
			// also pause while not in recording mode — the
			// visualizer bars aren't mounted in idle/transcribing/error
			// mode, so the per-frame getComputedStyle + style writes
			// would be pure waste (1.8–3 % of one core in
			// `always_visible` idle).
			if (!recordingRef.current) return;

			const dots = dotRefs.current;
			if (!dots) return;

			// ensure the barColor cache is initialised on the
			// first frame. The MutationObserver effect runs after
			// paint, so the very first frame may not have a cached
			// color yet.
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
				// backgroundColor is no longer set per-frame —
				// `applyBarColor` writes it on first frame + on theme
				// change. Re-writing the same string 60 times a second
				// was wasted work (the value only changes on theme
				// switch).
				el.style.opacity = `${0.35 + level * 0.65}`;
			}
		};

		frameRef.current = requestAnimationFrame(animate);

		return () => {
			off();
			if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
		};
	}, [dotRefs, refreshBarColor]);
}
