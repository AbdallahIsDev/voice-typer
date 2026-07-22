/**
 * PVT-067: extracted subcomponents and hooks for the Bubble overlay.
 *
 * Previously `Bubble.tsx` was a 671-line monolith containing the
 * visualizer bars, mic button, theme sync, audio-level rAF loop, and
 * the show/hide/set-state state machine all in one component. This
 * module splits out the four reusable units so `Bubble.tsx` itself
 * stays under 300 lines and each concern can be reasoned about (and
 * tested) in isolation:
 *
 *   - `useBubbleLifecycle` — owns theme sync (PVT-017), the 60 fps
 *     audio-level rAF loop (paused when the bubble is hidden), and
 *     visibility tracking (subscribes to `api.onShow` / `api.onHide`).
 *   - `useBubbleStateMachine` — owns `mode` / `animState` / `exitTick`
 *     and subscribes to `api.onShow` / `api.onHide` / `api.onSetState`.
 *   - `BubbleVisualizer` — the recording-mode REC indicator + 7-bar
 *     spectrum visualiser.
 *   - `BubbleMicButton` — the always-visible mic toggle button.
 *
 * The main `Bubble` component in `Bubble.tsx` orchestrates these and
 * owns only the auto-resize `useLayoutEffect`, the fading → exit timer,
 * and the final render tree.
 */
import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	type Dispatch,
	type RefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { t } from "@/i18n/i18n";
import { applyThemeVars, CUSTOM_THEME_ID } from "@/themes";

// ── Constants & types ────────────────────────────────────────────────

export type BubbleMode =
	| "recording"
	| "transcribing"
	| "idle"
	| "fading"
	| "error";

export type AnimState = "enter" | "exit" | "";

export const DOT_COUNT = 7;
export const MIN_HEIGHT = 5;
// BUBBLE-FIX-5.1: reduced from 32 → 22 to fit inside the h-6 (24px)
// wrapper with 2px vertical headroom.
export const MAX_HEIGHT = 22;

/** Per-bar response weights — gentle bell so the spectrum looks organic. */
export const DOT_WEIGHTS = [0.5, 0.75, 1.0, 0.95, 1.0, 0.75, 0.5];

/** Transcribing dots animation count. */
export const TRANSCRIBING_DOT_COUNT = 3;

/** Duration (ms) for the transcribing content fade-out before bubble exits. */
export const FADEOUT_DURATION_MS = 150;

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Translation-with-fallback helper. The i18n `t()` returns the raw key
 * string when the key is missing from every locale dictionary. We fall
 * back to a sensible English label instead of rendering the raw key.
 */
export function tf(key: string, fallback: string): string {
	const v = t(key);
	return v === key ? fallback : v;
}

/**
 * RMS → normalised level [0, 1]. Speech RMS typically lives in
 * [0, ~0.3]; we apply a soft compressor so loud transients don't peg
 * every bar. Multiplier 8 (was 5) so quiet speech visibly animates.
 */
function rmsToNorm(rms: number): number {
	return Math.min(1, rms * 8);
}

// ── useThemeSync — keeps the bubble's <html> in sync with the main
//     app's theme so Tailwind dark: variants resolve correctly.
//
// PVT-017: previously this hook honored ONLY the OS
// `prefers-color-scheme` media query (and, since CR-056, an optional
// `theme_mode` field from `bubble:config`). It never learned the
// user's `theme_preset` (e.g. "nord", "dracula") or `custom_theme`
// CSS-var map, so the bubble rendered with the default palette while
// the main app rendered with the user's chosen preset. Now the hook
// also reads `theme_preset` and `custom_theme` from the `bubble:config`
// payload and calls `applyThemeVars()` after toggling `.dark` so the
// bubble inherits the same preset-derived CSS vars as the main app.
//
// Forward-compatible: until the Python backend's `_push_bubble_config`
// (in voice_typer/server/waveform_bubble_wiring.py) is updated to
// include `theme_preset` / `custom_theme` in the payload, both refs
// stay `null`/`"default"` and the bubble keeps deferring to the
// stylesheet defaults — preserving the pre-fix behavior.

function useThemeSync() {
	const themeModeRef = useRef<"light" | "dark" | "system" | null>(null);
	const themePresetRef = useRef<string | null>(null);
	const customThemeRef = useRef<{
		light?: Record<string, string>;
		dark?: Record<string, string>;
	} | null>(null);

	const applyTheme = useCallback(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
		const mode = themeModeRef.current;
		const isDark =
			mode === "dark" ? true : mode === "light" ? false : prefersDark.matches; // mode === "system" || mode === null
		document.documentElement.classList.toggle("dark", isDark);
		// PVT-017: re-apply theme-preset CSS vars AFTER toggling `.dark`
		// so the bubble picks up the correct light/dark variant of the
		// preset. `applyThemeVars` is a no-op for the "default" preset.
		const preset = themePresetRef.current ?? "default";
		const customVars =
			preset === CUSTOM_THEME_ID && customThemeRef.current
				? ((isDark
						? customThemeRef.current.dark
						: customThemeRef.current.light) ?? null)
				: null;
		try {
			applyThemeVars(preset, isDark, customVars);
		} catch {
			// A corrupted custom_theme payload could throw inside
			// applyThemeVars; swallow so the bubble doesn't crash over
			// a cosmetic error. The .dark class is already toggled.
		}
	}, []);

	// OS prefers-color-scheme listener (preserved from CR-056).
	useEffect(() => {
		const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
		applyTheme();
		prefersDark.addEventListener("change", applyTheme);
		return () => prefersDark.removeEventListener("change", applyTheme);
	}, [applyTheme]);

	// bubble:config listener for theme_mode / theme_preset / custom_theme.
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onConfig) return;

		const off = api.onConfig((cfg) => {
			const mode = cfg.theme_mode;
			if (mode === "light" || mode === "dark" || mode === "system") {
				themeModeRef.current = mode;
			} else {
				themeModeRef.current = null;
			}
			// PVT-017: accept theme_preset (id string) and custom_theme
			// ({light, dark} map). The backend doesn't push these yet,
			// but the bubble can react when it does.
			const preset = cfg.theme_preset;
			if (typeof preset === "string") {
				themePresetRef.current = preset;
			}
			const custom = cfg.custom_theme;
			if (custom && typeof custom === "object") {
				customThemeRef.current = custom as {
					light?: Record<string, string>;
					dark?: Record<string, string>;
				};
			}
			applyTheme();
		});
		return off;
	}, [applyTheme]);
}

// ── useAudioLevels — 60fps direct-DOM animation, paused when hidden ──
//
// React state is intentionally NOT used for the per-frame bar heights;
// we grab a ref to each <span> and mutate style directly from
// requestAnimationFrame — zero React re-render overhead at 60 Hz.
//
// PVT (rAF pause): the rAF loop now early-returns when the bubble is
// hidden. Previously, even when the BrowserWindow was hidden, the loop
// kept computing bar heights and calling getComputedStyle 60 times a
// second. We keep the loop alive (so it resumes instantly on show())
// but skip the DOM work while `visibleRef.current === false`.

function useAudioLevels(
	dotRefs: RefObject<(HTMLSpanElement | null)[]>,
	isVisible: boolean,
) {
	const rawLevelRef = useRef(0);
	const frameRef = useRef<number | null>(null);
	const visibleRef = useRef(isVisible);
	visibleRef.current = isVisible;

	useEffect(() => {
		const api = window.bubble;
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
			// when the bubble becomes visible again.
			frameRef.current = requestAnimationFrame(animate);

			// Pause DOM work while the bubble window is hidden.
			if (!visibleRef.current) return;

			const dots = dotRefs.current;
			if (!dots) return;

			const level = rawLevelRef.current;
			// Read bar color from the --text-primary CSS var (falls back
			// to --foreground, then to a hardcoded last-resort) instead
			// of hardcoding #fff / #18181b. This makes the bars honor
			// theme presets (PVT-017) automatically.
			const rootStyle = getComputedStyle(document.documentElement);
			const barColor =
				rootStyle.getPropertyValue("--text-primary").trim() ||
				rootStyle.getPropertyValue("--foreground").trim() ||
				(document.documentElement.classList.contains("dark")
					? "#fff"
					: "#18181b");

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
		};

		frameRef.current = requestAnimationFrame(animate);

		return () => {
			off();
			if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
		};
	}, [dotRefs]);
}

// ── useBubbleLifecycle — composes theme sync + audio levels + visibility ─

/**
 * Owns the bubble's "always-on" lifecycle concerns: theme sync (so the
 * sandboxed bubble renderer inherits the main app's theme_mode /
 * theme_preset / custom_theme), the 60 fps audio-level rAF loop
 * (paused when the bubble is hidden), and visibility tracking
 * (subscribes to `api.onShow` / `api.onHide`).
 *
 * Returns the current visibility flag — callers use it to gate any
 * side-effects that should be idle while the BrowserWindow is hidden.
 */
export function useBubbleLifecycle(
	dotRefs: RefObject<(HTMLSpanElement | null)[]>,
): boolean {
	const [isVisible, setIsVisible] = useState(true);

	useThemeSync();
	useAudioLevels(dotRefs, isVisible);

	useEffect(() => {
		const api = window.bubble;
		if (!api) return;
		const offShow = api.onShow(() => setIsVisible(true));
		const offHide = api.onHide(() => setIsVisible(false));
		return () => {
			offShow();
			offHide();
		};
	}, []);

	return isVisible;
}

// ── useBubbleStateMachine ────────────────────────────────────────────

export interface BubbleStateMachine {
	mode: BubbleMode;
	setMode: Dispatch<SetStateAction<BubbleMode>>;
	animState: AnimState;
	setAnimState: Dispatch<SetStateAction<AnimState>>;
	exitTick: number;
	setExitTick: Dispatch<SetStateAction<number>>;
}

/**
 * Owns the bubble's mode/animation state machine.
 *
 *   - `mode` is one of `"recording" | "transcribing" | "idle" |
 *     "fading" | "error"`. The first four mirror the original Bubble
 *     behavior; `"error"` (PVT fix) is set when the backend pushes
 *     `set_state("error")` so the overlay can surface a red "⚠ Error"
 *     label instead of silently keeping the last mode.
 *   - `animState` is `"enter" | "exit" | ""` and drives the CSS
 *     `animate-bubble-enter` / `animate-bubble-exit` classes.
 *   - `exitTick` is incremented on each hide request to force the
 *     exit effect (in `Bubble.tsx`) to re-run even when mode doesn't
 *     change (e.g. recording → recording).
 *
 * Subscribes to `api.onShow`, `api.onHide`, and `api.onSetState`.
 */
export function useBubbleStateMachine(): BubbleStateMachine {
	const [mode, setMode] = useState<BubbleMode>("recording");
	const [animState, setAnimState] = useState<AnimState>("enter");
	const [exitTick, setExitTick] = useState(0);

	useEffect(() => {
		const api = window.bubble;
		if (!api) return;

		const offShow = api.onShow(() => {
			setExitTick(0); // Cancel any pending exit
			setAnimState("enter");
			// BUBBLE-FIX: don't override transcribing/fading mode if a
			// state change arrived before our show() event. This prevents
			// a race where the backend calls set_state("transcribing")
			// and then show() is re-triggered.
			setMode((prev) => {
				if (prev === "transcribing") return prev;
				return "recording";
			});
		});

		const offHide = api.onHide(() => {
			// Two-stage transition when leaving transcribing state:
			// first fade the transcribing content out smoothly, then
			// trigger the bubble exit animation.
			setMode((prev) => (prev === "transcribing" ? "fading" : prev));
			setExitTick((t) => t + 1);
		});

		return () => {
			offShow();
			offHide();
		};
	}, []);

	// Listen for state changes from the Python backend. When recording
	// stops, Python sends "transcribing" so the bubble hides the
	// visualizer and shows "Transcribing..." text. When transcription
	// completes, it sends "idle" (for always_visible mode) or hide().
	// "error" surfaces a red ⚠ Error label (PVT fix).
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onSetState) return;

		const off = api.onSetState((state) => {
			setMode((prev) => {
				// Ignore state changes while fading out (exit in progress)
				if (prev === "fading") return prev;

				if (state === "transcribing") return "transcribing";
				if (state === "idle") return "idle";
				if (state === "recording") return "recording";
				if (state === "error") return "error";
				return prev;
			});
		});
		return off;
	}, []);

	return { mode, setMode, animState, setAnimState, exitTick, setExitTick };
}

// ── BubbleVisualizer (recording mode) ────────────────────────────────

/**
 * The recording-mode pill content: a destructive-token "● REC"
 * indicator + 7-bar spectrum visualiser. The bars are animated by
 * `useBubbleLifecycle`'s rAF loop via the shared `dotRefs`.
 *
 * The 7 bar <span>s live inside a `<div class="gap-[3px]">` wrapper
 * — this preserves the `Bubble.test.tsx` selector
 * `.gap-[3px] > span` which expects exactly 7 bars.
 */
export function BubbleVisualizer({
	dotRefs,
}: {
	dotRefs: RefObject<(HTMLSpanElement | null)[]>;
}) {
	const dots = Array.from({ length: DOT_COUNT }, (_, i) => i);
	return (
		<div className="flex h-6 items-center gap-1.5">
			{/* REC indicator — destructive token, not hardcoded red. */}
			<span
				className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
				aria-hidden
			/>
			<span className="text-[10px] font-medium text-destructive">
				{tf("bubble.recordingLabel", "REC")}
			</span>
			{/* `ms-1` is the RTL-safe logical replacement for the old
			    physical `ml-1`. In LTR it renders as margin-left; in RTL
			    (ar locale) it flips to margin-right automatically. */}
			<div className="flex h-6 items-center gap-0.75 ms-1" aria-hidden>
				{dots.map((i) => (
					<span
						key={i}
						ref={(el) => {
							dotRefs.current[i] = el;
						}}
						className="inline-block w-0.75 rounded-full bg-zinc-900 dark:bg-white"
						style={{ height: MIN_HEIGHT, opacity: 0.3 }}
					/>
				))}
			</div>
		</div>
	);
}

// ── BubbleMicButton ──────────────────────────────────────────────────

/**
 * The always-visible mic toggle button. Shown only when the bubble is
 * in always_visible mode AND both `bubble_mic_button` and
 * `bubble_click_to_toggle` are on (gated by the parent). When
 * recording, shows a stop affordance; otherwise a mic. Clicking
 * toggles dictation via the sandboxed `bubble:toggle-dictation`
 * channel.
 */
export function BubbleMicButton({
	mode,
	onClick,
}: {
	mode: BubbleMode;
	onClick: () => void;
}) {
	const isRecording = mode === "recording";
	const label = isRecording
		? t("bubble.micButtonStopAria")
		: t("bubble.micButtonStartAria");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			// `ms-1` (margin-inline-start) replaces `ml-1` for RTL safety.
			className="no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white"
		>
			{isRecording ? (
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
	);
}

// Re-export the icon so consumers that import from this module can use
// it without a second import. (Currently used by Bubble.tsx for the
// idle-mode "Ready" label.)
export { HugeiconsIcon, Mic02Icon };
