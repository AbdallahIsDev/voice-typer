import { type RefObject, useEffect, useState } from "react";
import { useAudioLevels } from "./useAudioLevels";
import { useBubbleBridge } from "./useBubbleBridge";
import { useThemeSync } from "./useThemeSync";

export function useBubbleLifecycle(
	dotRefs: RefObject<(HTMLSpanElement | null)[]>,
): boolean {
	const [isVisible, setIsVisible] = useState(true);

	useThemeSync();
	useAudioLevels(dotRefs, isVisible);

	const bridge = useBubbleBridge();
	useEffect(() => {
		if (!bridge) return;
		const offShow = bridge.on("show", () => setIsVisible(true));
		const offHide = bridge.on("hide", () => setIsVisible(false));
		return () => {
			offShow();
			offHide();
		};
	}, [bridge]);

	return isVisible;
}
