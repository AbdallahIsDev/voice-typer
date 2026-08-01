/**
 * Bubble overlay package — public surface.
 *
 * The legacy `../bubble-components.tsx` module now just does
 * `export * from "./bubble";` so existing consumers (e.g. `Bubble.tsx`)
 * keep working without churn.
 *
 * Public API:
 *   - constants: `BubbleMode`, `AnimState`, `BubbleAction`,
 *     `DOT_COUNT`, `MIN_HEIGHT`, `MAX_HEIGHT`, `DOT_WEIGHTS`,
 *     `DOT_INDICES`, `TRANSCRIBING_DOT_COUNT`, `FADEOUT_DURATION_MS`,
 *     `BUBBLE_BUTTON_CLASS`
 *   - helpers: `tf`, `rmsToNorm`, `getBubbleAriaLabel`
 *   - hooks: `useThemeSync`, `useAudioLevels`, `useBubbleLifecycle`,
 *     `useBubbleStateMachine` (+ `BubbleStateMachine` interface)
 *   - components: `BubbleVisualizer`, `BubbleMicButton`,
 *     `BubbleStopButton`, `BubbleDismissButton`, `BubbleModeContent`
 */

export { BubbleDismissButton } from "./BubbleDismissButton";
export { BubbleMicButton } from "./BubbleMicButton";
export { BubbleModeContent } from "./BubbleModeContent";
export { BubbleStopButton } from "./BubbleStopButton";
// components
export { BubbleVisualizer } from "./BubbleVisualizer";
export type {
	AnimState,
	BubbleAction,
	BubbleMode,
} from "./constants";
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
// helpers — pure functions
export { getBubbleAriaLabel, rmsToNorm, tf } from "./helpers";
export { useAudioLevels } from "./useAudioLevels";
export { useBubbleLifecycle } from "./useBubbleLifecycle";
export type { BubbleStateMachine } from "./useBubbleStateMachine";
export { useBubbleStateMachine } from "./useBubbleStateMachine";
// hooks
export { useThemeSync } from "./useThemeSync";
