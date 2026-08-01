// OS-level microphone permission probe.
//
//(onchange leak fix): the previous implementation registered the
// change listener via status.onchange = handler but the cleanup only
// set cancelled = true — it did NOT clear status.onchange. The
// PermissionStatus object is owned by navigator.permissions cache and
// lives for the document lifetime, so the onchange closure was held
//until the next mount overwrote it.  switches to
// status.addEventListener("change", handler) + removes the listener in
// cleanup, and ALSO sets status.onchange = null defensively. The
// cancelled flag pattern is preserved.

import { useEffect, useState } from "react";

export type MicPermission = "granted" | "denied" | "prompt" | "unknown";

export interface UseMicrophonePermissionResult {
	micPermission: MicPermission;
}

export function useMicrophonePermission(): UseMicrophonePermissionResult {
	const [micPermission, setMicPermission] = useState<MicPermission>("unknown");

	useEffect(() => {
		let cancelled = false;
		//lift permStatus + changeHandler to the effect scope so
		// the cleanup can remove the listener and clear onchange.
		let permStatus: PermissionStatus | null = null;
		let changeHandler: (() => void) | null = null;

		const probe = async () => {
			try {
				const name = "microphone" as PermissionName;
				const status = await navigator.permissions.query({ name });
				if (cancelled) return;
				const state = status.state as "granted" | "denied" | "prompt";
				setMicPermission(state);
				changeHandler = () => {
					if (cancelled) return;
					setMicPermission(
						(status.state as "granted" | "denied" | "prompt") ?? "unknown",
					);
				};
				status.addEventListener("change", changeHandler);
				permStatus = status;
			} catch {
				if (cancelled) return;
				setMicPermission("unknown");
			}
		};
		void probe();

		return () => {
			cancelled = true;
			//clear the onchange closure + removeEventListener.
			if (permStatus && changeHandler) {
				try {
					permStatus.removeEventListener("change", changeHandler);
				} catch {
					/* best-effort */
				}
			}
			if (permStatus) {
				try {
					permStatus.onchange = null;
				} catch {
					/* best-effort */
				}
			}
		};
	}, []);

	return { micPermission };
}
