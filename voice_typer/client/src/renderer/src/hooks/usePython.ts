// src/renderer/src/hooks/usePython.ts

import { useCallback, useEffect, useRef } from "react";

type EventCallback = (event: {
	type: string;
	data?: Record<string, unknown>;
}) => void;

interface WindowWithPython {
	python?: {
		call: (msg: {
			type: string;
			data?: Record<string, unknown>;
		}) => Promise<unknown>;
		onEvent: (callback: EventCallback) => () => void;
	};
}
export function usePython() {
	const call = useCallback(
		async <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		): Promise<T> => {
			const api = (window as unknown as WindowWithPython).python;
			if (!api) throw new Error("Python bridge not available");
			const result = (await api.call({ type, data })) as Record<
				string,
				unknown
			>;
			// NEW-IPC-107 (d-review NEW-IPC-007): handle BOTH error
			// envelope shapes that can flow back over the Electron
			// path, surfacing each as a real JS Error so callers
			// using `try { await python.call(...) } catch (e) {}`
			// see failures instead of silently treating the error
			// envelope as a successful result (which previously left
			// callers reading `undefined` from data fields).
			//
			//   1. `{_error: "..."}` — Electron main-process synthetic
			//      errors (index.ts:1908/1911/1916): backend-not-
			//      connected and sendToPython exceptions. `_error` is
			//      a STRING in the actual Electron code; we also
			//      accept `{message: "..."}` defensively.
			//   2. `{type:"error", data:{code, message}}` — Python
			//      server unhandled-dispatch exceptions
			//      (ipc_server.py:1044-1050). The Electron main
			//      process resolves the pending request with this
			//      object verbatim (it does NOT translate it into
			//      `{_error: ...}`).
			//
			// On Tauri, NEITHER in-code check is reachable: the Rust
			// `dispatch` command (main.rs:954-965) rejects the
			// `invoke` promise on `type:"error"` (and never produces
			// `{_error:...}`), so `await api.call(...)` throws before
			// we ever inspect the resolved value. The checks below
			// are therefore Electron-path-only — DEAD CODE on Tauri,
			// but harmless (and the unified error shape keeps
			// caller-facing behavior consistent across both runtimes).
			// Errors on Tauri propagate as-is from the Rust rejection
			// (no double-wrapping) — the `await` throws and we never
			// reach the envelope inspection.
			if (result && typeof result === "object" && "_error" in result) {
				const e = (result as { _error?: unknown })._error;
				const msg =
					typeof e === "string"
						? e
						: ((e as { message?: string } | null)?.message ?? "unknown error");
				throw new Error(msg);
			}
			if (
				result &&
				typeof result === "object" &&
				(result as { type?: unknown }).type === "error"
			) {
				throw new Error(
					(result as { data?: { message?: string } }).data?.message ??
						"unknown error",
				);
			}
			return result as T;
		},
		[],
	);

	// NEW-TS-015: previously this hook also returned ``isReady: !!api``.
	// That flag was always ``true`` in production because the preload
	// script installs ``window.python`` before the React app mounts, so
	// every consumer's ``if (!isReady) return`` guard was dead code.
	// Worse, the name suggested "Python backend is ready" when it
	// actually meant "Python bridge exists" — callers that wanted real
	// readiness should track ``connectionStatus === 'connected'`` in
	// App.tsx (which probes the backend via ``get_config``).
	//
	// If a future caller needs to distinguish "bridge installed" from
	// "bridge missing" (e.g. running outside Electron), they can do
	// ``const api = (window as unknown as WindowWithPython).python`` and
	// check ``!!api`` directly.  We don't expose a misleading flag.
	return { call };
}

export function usePythonEvent(
	type: string,
	handler: (data?: Record<string, unknown>) => void,
) {
	const handlerRef = useRef(handler);
	handlerRef.current = handler;

	useEffect(() => {
		const api = (window as unknown as WindowWithPython).python;
		if (!api) return;

		return api.onEvent((event) => {
			if (event.type === type) {
				handlerRef.current(event.data);
			}
		});
	}, [type]);
}
