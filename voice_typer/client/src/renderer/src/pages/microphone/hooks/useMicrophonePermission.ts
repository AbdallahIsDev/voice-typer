// OS-level microphone permission probe.
//
// PVT-036 (Fix 3): probes the OS-level microphone permission state on
// mount via ``navigator.permissions.query({name: "microphone"})``. The
// standard Chromium API works in Electron's renderer and in Tauri's
// WebView2 (Windows) / WKWebView (macOS) when the host exposes it. On
// Linux Tauri (WebKitGTK) it typically rejects; we catch and treat the
// result as "unknown" (no banner shown — better to be silent than to
// show a false-positive "permission denied" banner).
//
// The returned ``micPermission`` is consumed by
// ``MicrophonePermissionBanner``, which only renders when the state is
// ``"denied"``. ``"prompt"`` (first-run, user hasn't decided yet) and
// ``"unknown"`` (API unavailable) both suppress the banner.

import { useEffect, useState } from "react";

export type MicPermission = "granted" | "denied" | "prompt" | "unknown";

export interface UseMicrophonePermissionResult {
	micPermission: MicPermission;
}

export function useMicrophonePermission(): UseMicrophonePermissionResult {
	const [micPermission, setMicPermission] = useState<MicPermission>("unknown");

	useEffect(() => {
		let cancelled = false;
		const probe = async () => {
			try {
				// Some TypeScript DOM lib versions don't include
				// "microphone" in the PermissionName union. Cast to
				// the wider string type so the call compiles without
				// mutating the global lib typings.
				const name = "microphone" as PermissionName;
				const status = await navigator.permissions.query({ name });
				if (cancelled) return;
				const state = status.state as "granted" | "denied" | "prompt";
				setMicPermission(state);
				// Listen for changes (e.g. user grants permission from
				// the OS settings dialog while the app is open).
				status.onchange = () => {
					if (cancelled) return;
					setMicPermission(
						(status.state as "granted" | "denied" | "prompt") ?? "unknown",
					);
				};
			} catch {
				if (cancelled) return;
				setMicPermission("unknown");
			}
		};
		void probe();
		return () => {
			cancelled = true;
		};
	}, []);

	return { micPermission };
}
