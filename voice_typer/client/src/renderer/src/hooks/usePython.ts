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
			// NEW-IPC-107: the Electron main process returns
			// `{ _error: "..." }` when (a) the backend isn't
			// connected (index.ts:1911) or (b) any sendToPython
			// exception fires (index.ts:1916). Surface those as
			// JS errors so callers see real failures.
			if (result && typeof result === "object" && "_error" in result) {
				throw new Error(result._error as string);
			}
			// NEW-IPC-107 (the actual fix): the Python server
			// returns structured error envelopes
			// `{type:"error", data:{code, message}}` on unhandled
			// dispatch exceptions (ipc_server.py:1044-1050). The
			// Electron main process resolves the pending request
			// with that object verbatim, so `result.type ===
			// "error"` was silently treated as a successful
			// result and callers downstream got `undefined` when
			// they tried to access real data fields.
			//
			// Under Tauri, the Rust host's `dispatch` command
			// already surfaces `type:"error"` as a Rust error
			// (which rejects the invoke() promise on the JS
			// side), so this guard is primarily for the Electron
			// path. It's safe to apply on both paths because the
			// success envelope is `type:"result"` — `type:"error"`
			// is unambiguously a failure.
			if (
				result &&
				typeof result === "object" &&
				"type" in result &&
				(result as { type: unknown }).type === "error"
			) {
				const errData = (
					result as { data?: { code?: string; message?: string } }
				).data;
				const code = errData?.code ?? "unknown";
				const message = errData?.message ?? "server error";
				throw new Error(`server error [${code}]: ${message}`);
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
