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
	useMemo,
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

export type BubbleAction = "mic" | "dismiss";

export const DOT_COUNT = 7;
export const MIN_HEIGHT = 5;
// BUBBLE-FIX-5.1: reduced from 32 → 22 to fit inside the h-6 (24px)
// wrapper with 2px vertical headroom.
export const MAX_HEIGHT = 22;

/** Per-bar response weights — gentle bell so the spectrum looks organic. */
export const DOT_WEIGHTS = [0.5, 0.75, 1.0, 0.95, 1.0, 0.75, 0.5];

/**
 * Pre-computed `[0, 1, … DOT_COUNT-1]` index array. Previously
 * `BubbleVisualizer` allocated a fresh `Array.from({ length: DOT_COUNT },
 * (_, i) => i)` on every render — small but unnecessary garbage. Hoisted
 * to module scope so the JSX `.map` uses a stable reference.
 */
export const DOT_INDICES: readonly number[] = Array.from(
	{ length: DOT_COUNT },
	(_, i) => i,
);

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
		} catch (e) {
			// A corrupted custom_theme payload could throw inside
			// applyThemeVars; swallow so the bubble doesn't crash over
			// a cosmetic error. The .dark class is already toggled.
			console.warn("[bubble-components] applyThemeVars failed:", e);
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
//
// rAF recording-gate + barColor cache: the loop now ALSO early-
// returns when the bubble is not in `mode === "recording"` — the
// visualizer bars aren't mounted in idle/transcribing/error mode, so
// the per-frame `getComputedStyle` + style writes were pure waste
// (1.8–3 % of one core continuously in `always_visible` idle). The
// recording flag is mirrored locally from the same `window.bubble`
// events that drive `useBubbleStateMachine` (Bubble.tsx owns the
// authoritative state machine; this hook keeps a parallel boolean so
// it can gate without a round-trip through props).
//
// barColor cache: the `--text-primary` / `--foreground` CSS
// vars are now read ONCE on first frame + whenever the document's
// `class` / `style` attribute changes (theme switch, dark/light
// toggle, OS prefers-color-scheme flip). The cached color is applied
// to every dot via a `useEffect`-driven helper, NOT per-frame. The
// previous implementation re-read `getComputedStyle` and re-wrote
// `el.style.backgroundColor` 60 times a second even though the value
// only changes on theme switch.

function useAudioLevels(
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
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
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
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
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
 *
 * The per-dot ref setters are memoised once per `dotRefs`
 * instance via `useMemo` so React doesn't call ref-cleanup + re-attach
 * on every render (the previous inline arrow was a fresh closure every
 * render, which React treats as a ref change). The index list is the
 * module-level `DOT_INDICES` constant (no per-render `Array.from`).
 */
export function BubbleVisualizer({
	dotRefs,
}: {
	dotRefs: RefObject<(HTMLSpanElement | null)[]>;
}) {
	// build the 7 stable ref setters once per `dotRefs` instance.
	// The array identity is stable across renders (only changes if
	// `dotRefs` changes, which it doesn't in practice), so React's
	// reconciler sees the same ref callback on every render and skips
	// the detach/attach cycle.
	const refSetters = useMemo(
		() =>
			Array.from(
				{ length: DOT_COUNT },
				(_, i) => (el: HTMLSpanElement | null) => {
					dotRefs.current[i] = el;
				},
			),
		[dotRefs],
	);
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
				{DOT_INDICES.map((i) => (
					<span
						key={i}
						ref={refSetters[i]}
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
 *
 * BG-31 A11Y TRADE-OFF (focusable:false — keyboard inaccessible):
 * The bubble `BrowserWindow` is created with `focusable: false` in
 * `main/windows/bubble-window.ts` (intentional — prevents the bubble
 * from stealing keyboard focus from the user's active text field).
 * Because the window is non-focusable, this real `<button>` element
 * is UNREACHABLE via Tab and cannot be activated via Enter/Space in
 * the shipped app. It is effectively mouse-only.
 *
 * Decision (BG-31 option a — keep focusable:false, document trade-off):
 * we accept the mouse-only limitation for now because making the
 * bubble focusable would harm the primary UX (dictation into the
 * user's active text field). The recommended future solution is a
 * MAIN-PROCESS global hotkey (e.g. Ctrl+Shift+M) that routes to the
 * same `bubble:toggle-dictation` channel — see the BG-31 handoff
 * note for F11 (bubble-window.ts) in the F10 return report. When
 * that hotkey lands, the BubbleMicButton's `aria-label` and `title`
 * will already be correct; only the wiring changes.
 *
 * Note: this button still renders with `type="button"` and an
 * `aria-label` so AT users navigating via screen-reader cursor (not
 * keyboard focus) can still discover it, and so automated a11y
 * audits (axe-core) see a properly-labelled control.
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

// ── BubbleStopButton (XA-6-1 / XA-6-13) ─────────────────────────────

/**
 * The stop '■' / retry '↻' affordance shown at the trailing edge of
 * the pill. In `recording` mode it is always rendered (independent of
 * `always_visible`) and clicking it sends `bubble:toggle-dictation`,
 * which the main process forwards to the Python `toggle_dictation`
 * command — the same channel `BubbleMicButton` uses. When recording,
 * `toggle_dictation` stops the recording and triggers transcription.
 *
 * This is the highest-impact XA-6 sub-fix: previously the only way to
 * stop a recording was the global hotkey, which is invisible to a
 * user who has forgotten the binding. The pill's `focusable: false`
 * BrowserWindow means a keyboard handler is impossible (PVT-048), so
 * a visible mouse-only button is the only viable in-bubble
 * affordance.
 *
 * In `error` mode the same component is rendered with a refresh icon
 * and a different aria-label so the user can retry the failed
 * transcription. The i18n keys fall back to English when the
 * dictionaries have not yet been updated (`tf` helper).
 *
 * A11Y: same `focusable: false` trade-off as `BubbleDismissButton` —
 * the button is mouse-only in the shipped app; `aria-label` and
 * `title` are populated so AT users navigating via screen-reader
 * cursor can still discover it.
 */
export function BubbleStopButton({
	onClick,
	mode,
}: {
	onClick: () => void;
	mode: "recording" | "error";
}) {
	// `tf` (translation-with-fallback) so a missing i18n key falls back
	// to a sensible English label instead of the raw key string.
	const label =
		mode === "error"
			? tf("bubble.retryAria", "Retry transcription")
			: tf("bubble.stopRecordingAria", "Stop recording");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			// `ms-1` (margin-inline-start) replaces `ml-1` for RTL safety.
			// Same sizing/styling as BubbleDismissButton so the three
			// affordances (mic / stop / dismiss) look like siblings.
			className="no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white"
		>
			{mode === "error" ? (
				// Retry: a circular arrow (Material-style "refresh").
				<svg
					width="12"
					height="12"
					viewBox="0 0 24 24"
					fill="none"
					stroke="currentColor"
					strokeWidth="2.5"
					strokeLinecap="round"
					strokeLinejoin="round"
					aria-hidden="true"
				>
					<polyline points="23 4 23 10 17 10" />
					<path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
				</svg>
			) : (
				// Stop: a filled square (media "stop" iconography).
				<svg
					width="10"
					height="10"
					viewBox="0 0 24 24"
					fill="currentColor"
					stroke="none"
					aria-hidden="true"
				>
					<rect x="5" y="5" width="14" height="14" rx="2" ry="2" />
				</svg>
			)}
		</button>
	);
}

// ── BubbleDismissButton (BG-96) ──────────────────────────────────────

/**
 * The dismiss '×' button. Shown whenever the bubble is in
 * `always_visible` mode (gated by the parent via the `dismissable`
 * prop, which mirrors the bubble_behavior config). Clicking sends a
 * `bubble:dismiss` IPC to the main process, which hides the bubble
 * window until the next show() (typically the next dictation start).
 *
 * BG-96 A11Y: same focusable:false trade-off as BubbleMicButton (see
 * the comment above that component) — the button is mouse-only in the
 * shipped app. The `aria-label` and `title` are populated so AT users
 * navigating via screen-reader cursor can still discover it.
 *
 * The `bubble:dismiss` IPC handler in `main/ipc/bubble-handlers.ts`
 * is owned by F11 — see the F10 return report for the handoff note.
 * Until F11 adds the handler, the IPC send is a no-op (Electron's
 * default ipcMain behavior is to silently drop messages with no
 * registered handler).
 */
export function BubbleDismissButton({ onClick }: { onClick: () => void }) {
	const label = t("bubble.dismissAria");
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={label}
			title={label}
			// `ms-1` (margin-inline-start) replaces `ml-1` for RTL safety.
			// Matches the BubbleMicButton sizing/styling so the two
			// affordances look like siblings.
			className="no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white"
		>
			<svg
				width="10"
				height="10"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				strokeWidth="3"
				strokeLinecap="round"
				strokeLinejoin="round"
				aria-hidden="true"
			>
				<line x1="6" y1="6" x2="18" y2="18" />
				<line x1="18" y1="6" x2="6" y2="18" />
			</svg>
		</button>
	);
}

// Re-export the icon so consumers that import from this module can use
// it without a second import. (Currently used by Bubble.tsx for the
// idle-mode "Ready" label.)
export { HugeiconsIcon, Mic02Icon };
