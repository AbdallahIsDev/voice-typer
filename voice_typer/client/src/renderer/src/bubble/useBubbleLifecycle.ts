/**
 * Bubble overlay package — `useBubbleLifecycle` hook.
 *
 * Composes theme sync + audio levels + visibility tracking.
 *
 * Owns the bubble's "always-on" lifecycle concerns: theme sync (so the
 * sandboxed bubble renderer inherits the main app's theme_mode /
 * theme_preset / custom_theme / locale), the 60 fps audio-level rAF
 * loop (paused when the bubble is hidden), and visibility tracking
 * (subscribes to `api.onShow` / `api.onHide`).
 *
 * Returns the current visibility flag — callers use it to gate any
 * side-effects that should be idle while the BrowserWindow is hidden.
 */
import { type RefObject, useEffect, useState } from "react";
import { useAudioLevels } from "./useAudioLevels";
import { useThemeSync } from "./useThemeSync";

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
