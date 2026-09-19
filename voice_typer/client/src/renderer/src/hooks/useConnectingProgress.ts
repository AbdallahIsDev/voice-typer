import { useEffect, useRef, useState } from "react";

import { usePythonEvent } from "@/hooks/usePython";
import type { ConnectionStatus } from "@/stores/appStore";

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
