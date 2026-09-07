/**
 * useConnectingProgress — tracks backend ``download_progress`` while
 * the app is still connecting.
 *
 * Extracted from App.tsx (the entry component stays pure wiring) using
 * the same extraction pattern as the other use* push-event hooks.
 * Behaviour is byte-identical to the original inline block.
 *
 * ``connectingProgress`` is ONLY consumed by
 * ``<ConnectionStatusScreen>``, which App renders exclusively when
 * ``connectionStatus !== "connected"``. Updating it while connected is
 * therefore wasted work — it would trigger an App re-render for a
 * state value nobody reads. The status is mirrored into a ref and the
 * handler short-circuits while connected. (We can't conditionally call
 * ``usePythonEvent`` — that would violate the rules of hooks — so the
 * dispatcher-level subscriber stays registered, but the actual
 * ``setConnectingProgress`` call is gated. The dispatcher fan-out for
 * an unmatched type is a single Map lookup + early return, so the
 * residual cost is negligible.)
 */

import { useEffect, useRef, useState } from "react";

import { usePythonEvent } from "@/hooks/usePython";
import type { ConnectionStatus } from "@/stores/appStore";

/**
 * Subscribe to ``download_progress`` push events and surface the
 * current percentage while the app is connecting.
 *
 * @param connectionStatus The live connection status; while
 *   ``"connected"`` the progress updates are skipped (the screen that
 *   reads the value is not rendered), and any transition away from
 *   ``"connecting"`` clears the value so a stale percentage can't
 *   persist across a brief disconnect/reconnect flap and mislead the
 *   user into thinking the download is still ongoing.
 * @returns The latest progress percentage, or ``null`` when there is
 *   nothing to show.
 */
export function useConnectingProgress(
	connectionStatus: ConnectionStatus,
): number | null {
	const [connectingProgress, setConnectingProgress] = useState<number | null>(
		null,
	);
	const connectionStatusRef = useRef(connectionStatus);
	connectionStatusRef.current = connectionStatus;
	usePythonEvent("download_progress", (data): (() => void) | undefined => {
		// Skip the state update while connected —
		// ConnectionStatusScreen isn't rendered, so the value would
		// never be read and the re-render would be wasted.
		if (connectionStatusRef.current === "connected") return undefined;
		const progress = (data as Record<string, unknown> | undefined)?.progress;
		if (typeof progress === "number") setConnectingProgress(progress);
		return undefined;
	});

	// Clear the connecting progress value whenever we leave the
	// "connecting" state. Without this, a stale progress percentage
	// (e.g. 73%) would persist across a brief disconnect/reconnect
	// flap and mislead the user into thinking the download was still
	// ongoing after the backend had already reconnected. The next
	// "connecting" phase re-seeds the value via the download_progress
	// handler above.
	useEffect(() => {
		if (connectionStatus !== "connecting") {
			setConnectingProgress(null);
		}
	}, [connectionStatus]);

	return connectingProgress;
}
