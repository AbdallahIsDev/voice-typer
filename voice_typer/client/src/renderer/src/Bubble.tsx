import { useCallback, useEffect, useRef, useState } from "react";

// ── Constants ────────────────────────────────────────────────────

const DOT_COUNT = 7;
const MIN_HEIGHT = 5; // px — resting bar height (was 4, bumped so bars are always subtly visible)
const MAX_HEIGHT = 32; // px — peak bar height (was 30, slightly more range)

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

		// ── Level listener ──────────────────────────────────────────    // Asymmetric smoothing: fast attack (reacts the instant the user
		// speaks), slower release (graceful decay back to rest).  This is
		// what makes a visualizer feel "live" rather than laggy.
		// BUGFIX 2026-06-25: increased attack weight from 0.7→0.8 so the
		// first spoken syllable immediately pops the bars instead of a
		// gradual fade-in. Increased release floor from 0.14→0.18 so the
		// decay doesn't drop to zero too fast between words.
		const onLevel = (data: { rms: number; peak: number }) => {
			const norm = rmsToNorm(data.rms);
			const cur = rawLevelRef.current;
			if (norm > cur) {
				rawLevelRef.current = cur * 0.2 + norm * 0.8; // fast attack (was 0.3/0.7)
			} else {
				rawLevelRef.current = cur * 0.82 + norm * 0.18; // slower release (was 0.86/0.14)
			}
		};

		const off = api.onLevel(onLevel);

		// ── Animation loop ───────────────────────────────────────────
		const animate = () => {
			const dots = dotRefs.current;
			if (!dots) return;

			const level = rawLevelRef.current;

			// Bar colour tracks the current theme so the direct-DOM
			// mutations stay in sync with Tailwind's dark: variants.
			const isDark = document.documentElement.classList.contains("dark");
			const barColor = isDark ? "#fff" : "#18181b"; // zinc-900

			for (let i = 0; i < DOT_COUNT; i++) {
				const el = dots[i];
				if (!el) continue;
				const weight = DOT_WEIGHTS[i] ?? 1;
				// Target height = resting + (level × weight) × dynamic range.
				// Loud voice → bars climb toward MAX; quiet/low → bars sit near MIN.
				const target = MIN_HEIGHT + level * weight * (MAX_HEIGHT - MIN_HEIGHT);
				// Ease the rendered bar toward the target so motion is smooth.
				const cur = parseFloat(el.style.height) || MIN_HEIGHT;
				const next = cur + (target - cur) * 0.36;
				el.style.height = `${Math.max(MIN_HEIGHT, next)}px`;
				el.style.backgroundColor = barColor;
				// Opacity tracks level: dim at rest, fully visible when speaking.
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
//    app's theme so Tailwind dark: variants resolve correctly. ───────

function useThemeSync() {
	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

		const apply = () => {
			// The bubble has no config of its own, so we follow the OS
			// preference directly.  When the main app sets theme_mode to
			// 'light' or 'dark', the OS-level nativeTheme was already
			// changed by Electron, so prefers-color-scheme reflects it.
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

	useThemeSync();
	useAudioLevels(dotRefs);

	// ── Enter / exit animation handlers ──────────────────────────────
	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		const offShow = api.onShow(() => {
			setAnimState("enter");
		});

		const offHide = api.onHide(() => {
			setAnimState("exit");
		});

		return () => {
			offShow();
			offHide();
		};
	}, []);

	// ── Listen for draggable state ───────────────────────────────────
	// NEW-DEAD-025: the initial state defaults to ``true`` (line 124).
	// The main process sends ``bubble:draggable`` on every ``show()``
	// call (main/index.ts:605), so the correct state is always pushed
	// before the user sees the bubble.  The race window between mount
	// and the first ``onDraggable`` callback is < 1 frame and the
	// bubble is hidden by default, so the user never observes a stale
	// ``true`` value.  If a future change makes the bubble visible on
	// mount (e.g. ``bubble_show_on_startup``), we'd need to add an
	// initial query — but the preload doesn't expose ``getDraggable()``
	// and the show-time sync covers the current use case.
	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		const off = api.onDraggable((d) => setDraggable(d));
		return off;
	}, []);

	// NEW-A11Y-006: keyboard-based bubble repositioning.
	// Arrow keys move the bubble by 10px (or 1px with Shift for fine control).
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

	// ── Auto-resize the BrowserWindow to fit the pill ──────────────
	// The BrowserWindow starts at 74x27.  We measure the actual pill
	// scroll dimensions after mount and after each show, then resize
	// the OS window exactly to that size.  This eliminates the
	// transparent dead zone that blocks clicks to underlying windows.
	const pillRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (animState !== "" && animState !== "exit") return;
		if (animState === "") {
			// Stable (enter complete or idle) — measure and resize.
			const el = pillRef.current;
			if (!el) return;
			const rect = el.getBoundingClientRect();
			const w = Math.ceil(rect.width);
			const h = Math.ceil(rect.height);
			// Add 1px safety margin so the window fully contains the pill.
			window.bubble?.resizeTo?.(w + 1, h + 1);
		}
	}, [animState]);

	// ── Animation-end callback ──────────────────────────────────────
	// When the exit CSS transition completes, tell the main process
	// it's safe to actually hide() the BrowserWindow.
	const handleAnimEnd = useCallback(() => {
		if (animState === "exit") {
			setAnimState("");
			window.bubble?.hideComplete?.();
		} else if (animState === "enter") {
			setAnimState("");
		}
	}, [animState]);

	// ── Build bar spans ──────────────────────────────────────────────
	const dots = Array.from({ length: DOT_COUNT }, (_, i) => i);

	// ── Drag approach ──────────────────────────────────────────────
	// We use the same CSS `-webkit-app-region: drag` / `no-drag` approach
	// as the main window's custom title bar (TitleBar.tsx + .drag-region
	// class in index.css).  This is stateless and handled at the
	// Chromium/OS level, so it survives window hide/show cycles — unlike
	// JavaScript pointer-capture which breaks after BrowserWindow.hide().
	//
	// The bubble window is transparent.  `-webkit-app-region: drag` only
	// works on non-transparent pixels (OS-level hit-testing fails on
	// transparent areas).  Therefore `drag-region` goes on the visible
	// pill itself — the only opaque element in the window.  The visualizer
	// bars are purely decorative (no user interaction).

	return (
		<output
			aria-live="polite"
			aria-label="Voice Typer recording indicator"
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
				{/* ── Voice level visualiser ──────────────────────────── */}
				<div className="flex items-center gap-[3px]">
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
		</output>
	);
}

export default Bubble;
