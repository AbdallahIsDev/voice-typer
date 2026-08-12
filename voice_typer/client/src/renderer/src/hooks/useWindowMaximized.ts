/**
 * useWindowMaximized — tracks the OS window maximized state via the
 * native bridge and mirrors it onto ``<html class="is-maximized">``.
 *
 * Extracted from App.tsx (EO-28, Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the
 * original inline effect:
 *
 *   - Queries ``bridge.isMaximized()`` on mount and subscribes to
 *     ``bridge.onMaximizedChanged`` for subsequent changes.
 *   - Mirrors each value onto ``document.documentElement``'s
 *     ``is-maximized`` class so CSS can drop window-chrome rounding /
 *     shadow when maximized.
 *   - The subscription is torn down on unmount (and the async
 *     isMaximized response is ignored after unmount via a cancelled
 *     flag).
 *
 * Returns the current maximized boolean so the caller can style its
 * own chrome (e.g. ``rounded-lg`` only when NOT maximized).
 */
import { useEffect, useState } from "react";
import type { WindowBridge } from "@/types/ipc";

/**
 * Subscribe to the native window's maximized state.
 *
 * @param bridge The ``window.window_`` bridge; may be undefined in
 *   non-native (e.g. pure-browser) environments — the hook then
 *   stays ``false`` and does nothing.
 * @returns The current maximized state.
 */
export function useWindowMaximized(bridge: WindowBridge | undefined): boolean {
	const [isMaximized, setIsMaximized] = useState(false);

	useEffect(() => {
		if (!bridge) return;
		let cancelled = false;
		bridge
			.isMaximized()
			.then((v) => {
				if (!cancelled) {
					setIsMaximized(v);
					document.documentElement.classList.toggle("is-maximized", v);
				}
			})
			.catch((err) =>
				console.warn(
					"[renderer:useWindowMaximized] window isMaximized failed:",
					err,
				),
			);
		const unsub = bridge.onMaximizedChanged((v) => {
			if (!cancelled) {
				setIsMaximized(v);
				document.documentElement.classList.toggle("is-maximized", v);
			}
		});
		return () => {
			cancelled = true;
			unsub();
		};
	}, [bridge]);

	return isMaximized;
}
