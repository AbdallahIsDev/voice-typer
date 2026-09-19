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
// constants, types + tuning knobs + shared button className
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
// helpers, pure functions
export { getBubbleAriaLabel, rmsToNorm, tf } from "./helpers";
export { useAudioLevels } from "./useAudioLevels";
export type { BubbleBridge, BubbleBridgeOff } from "./useBubbleBridge";
// bridge, centralises all window.bubble IPC subscriptions into one
// listener per event channel; consumers register handlers via
// `bridge.on(event, handler)`.
export { BubbleBridgeProvider, useBubbleBridge } from "./useBubbleBridge";
export { useBubbleLifecycle } from "./useBubbleLifecycle";
export type { BubbleStateMachine } from "./useBubbleStateMachine";
export { useBubbleStateMachine } from "./useBubbleStateMachine";
// hooks
export { useThemeSync } from "./useThemeSync";
