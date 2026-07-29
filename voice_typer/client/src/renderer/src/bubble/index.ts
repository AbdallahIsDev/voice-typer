/**
 * Bubble overlay package — public surface.
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16). The legacy `../bubble-components.tsx` module now just does
 * `export * from "./bubble";` so existing consumers (e.g. `Bubble.tsx`)
 * keep working without churn.
 *
 * Public API:
 *   - constants: `BubbleMode`, `AnimState`, `BubbleAction`,
 *     `DOT_COUNT`, `MIN_HEIGHT`, `MAX_HEIGHT`, `DOT_WEIGHTS`,
 *     `DOT_INDICES`, `TRANSCRIBING_DOT_COUNT`, `FADEOUT_DURATION_MS`,
 *     `BUBBLE_BUTTON_CLASS`
 *   - helpers: `tf`, `rmsToNorm`
 *   - hooks: `useThemeSync`, `useAudioLevels`, `useBubbleLifecycle`,
 *     `useBubbleStateMachine` (+ `BubbleStateMachine` interface)
 *   - components: `BubbleVisualizer`, `BubbleMicButton`,
 *     `BubbleStopButton`, `BubbleDismissButton`
 */

// constants — types + tuning knobs + shared button className
export {
	BUBBLE_BUTTON_CLASS,
	DOT_COUNT,
	DOT_INDICES,
	DOT_WEIGHTS,
	FADEOUT_DURATION_MS,
	MAX_HEIGHT,
	MIN_HEIGHT,
	TRANSCRIBING_DOT_COUNT,
} from "./constants";
export type {
	AnimState,
	BubbleAction,
	BubbleMode,
} from "./constants";

// helpers — pure functions
export { rmsToNorm, tf } from "./helpers";

// hooks
export { useThemeSync } from "./useThemeSync";
export { useAudioLevels } from "./useAudioLevels";
export { useBubbleLifecycle } from "./useBubbleLifecycle";
export { useBubbleStateMachine } from "./useBubbleStateMachine";
export type { BubbleStateMachine } from "./useBubbleStateMachine";

// components
export { BubbleVisualizer } from "./BubbleVisualizer";
export { BubbleMicButton } from "./BubbleMicButton";
export { BubbleStopButton } from "./BubbleStopButton";
export { BubbleDismissButton } from "./BubbleDismissButton";
