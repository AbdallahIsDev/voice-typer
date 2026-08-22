// `usePython` — the renderer's IPC `call` hook.
//
// Extracted from `hooks/usePython.ts` (now a public barrel) so the
// bridge modules live by concern under `lib/python-bridge/`. The
// public API is unchanged: consumers keep importing
// `{ usePython }` from `@/hooks/usePython`.

import { useCallback } from "react";
// Import the `PythonCallErrorCode` union so the renderer can narrow
// `result._code` against the typed union. The canonical declaration
// lives in the Electron main process's `python-call-handler.ts`
// (outside the web tsconfig's `include` scope — a cross-boundary import
// would fail `tsc --noEmit` with `TS6307`). The renderer-side mirror
// lives in `types/ipc/enums.ts` (this file's import below); the two
// declarations MUST stay in sync — both files carry a comment pointing
// to the other.
import type { PythonCallErrorCode } from "@/types/ipc/enums";
import type { PythonRequest } from "@/types/ipc/requests";
import { withCommandTimeout } from "./command-timeouts";
import { parseTauriErrorEnvelope } from "./error-envelope";

/**
 * Type of the ``call`` function returned by {@link usePython}.
 *
 * Two overloads — a strict one that narrows the ``data`` parameter
 * against the {@link PythonRequest} discriminated union (so a typo in
 * the command name or a wrong data shape surfaces at compile time for
 * known commands), and a loose one that accepts any string +
 * ``Record<string, unknown>`` for forward-compat with backend-added
 * commands that haven't made it into the union yet. TypeScript picks
 * the first matching overload, so known commands hit the strict
 * overload and unknown commands fall through to the loose one.
 *
 * The strict overload's ``data`` parameter uses a conditional type so
 * requests without a ``data`` field (e.g. ``GetConfigRequest``,
 * ``GetStatusRequest``) resolve to ``undefined`` instead of erroring
 * on the ``["data"]`` index — TypeScript can't index a union where
 * some members lack the key.
 */
export type PythonCall = {
	<T = unknown, K extends PythonRequest["type"] = PythonRequest["type"]>(
		type: K,
		data?: "data" extends keyof Extract<PythonRequest, { type: K }>
			? Extract<PythonRequest, { type: K }>["data"]
			: undefined,
	): Promise<T>;
	<T = unknown>(type: string, data?: Record<string, unknown>): Promise<T>;
};

export function usePython() {
	const call = useCallback(
		async <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		): Promise<T> => {
			const api = window.python;
			if (!api) throw new Error("Python bridge not available");
			// Race the underlying bridge call against a per-command
			// timeout so a hung trivial command (e.g. `get_status`) surfaces
			// an error in seconds instead of the prior blanket 120s timeout
			// imposed by the Electron main / Rust host. The underlying
			// promise may still resolve later; the caller sees the timeout
			// rejection first.
			//
			// Tauri/Electron error-envelope normalization. On
			// Tauri v2, `invoke` rejects with a RAW STRING (not an Error)
			// when the Rust `dispatch` command returns an Err — the host's
			// `e.to_string()` becomes the rejection value verbatim. Callers
			// that guard with `err instanceof Error ? err.message : String(err)`
			// work, but callers that do `err.message` directly
			// (e.g. `Microphone.tsx:278`, `lib/utils/models.ts:252`) read
			// `undefined` and lose the server error message. We wrap the
			// `await withCommandTimeout` call in try/catch and re-throw:
			//   - Error instances propagate unchanged (no double-wrapping);
			//   - string rejections are normalized into `new Error(string)`;
			//   - other shapes (numbers, objects) become `new Error("unknown IPC error")`.
			// The catch ALSO swallows the post-rejection envelope checks
			// below — on Tauri the await throws before we ever inspect the
			// resolved value (the in-code `_error`/`type:"error"` checks
			// are Electron-path-only, see the comment below).
			let result: Record<string, unknown>;
			try {
				result = (await withCommandTimeout(
					api.call({ type, data }),
					type,
				)) as Record<string, unknown>;
			} catch (err) {
				if (err instanceof Error) throw err;
				// On Tauri the Rust `dispatch` command rejects the
				// invoke promise with a raw STRING — for structured errors
				// it's the JSON-serialized `{type:"error", data:{code,
				// message}}` envelope (sidecar_cmds/dispatch.rs). Parse it
				// so `err.code` is stamped and callers that branch on the
				// failure class work on Tauri exactly as they do on
				// Electron (previously the whole JSON string became the
				// message and `code` was dropped, so
				// `err.code === "command_timeout"` checks silently fell
				// through on Tauri).
				if (typeof err === "string") {
					const parsed = parseTauriErrorEnvelope(err);
					if (parsed) throw parsed;
					throw new Error(err);
				}
				throw new Error("unknown IPC error");
			}
			// Handle BOTH error
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
				// Surface the structured ``_code`` field
				// (e.g. ``command_timeout``,
				// ``backend_not_connected``,
				// ``backend_exited_early``) so callers can branch
				// on retry / surface-toast / escalate. Pre-fix,
				// the envelope's ``_code`` was dropped on the
				// floor and every error became a plain
				// ``new Error(msg)`` — consumers could not
				// distinguish transient timeouts from fatal
				// backend-exited errors.
				const code = (result as { _code?: PythonCallErrorCode })._code;
				const err = new Error(msg);
				if (typeof code === "string" && code.length > 0) {
					(err as { code?: string }).code = code;
				}
				throw err;
			}
			if (
				result &&
				typeof result === "object" &&
				(result as { type?: unknown }).type === "error"
			) {
				// Surface the FULL ``data.errors`` list when
				// present so multi-field validation failures (e.g.
				// batched Settings → Audio saves with 3 invalid
				// fields) don't require 3 fix-and-resubmit cycles.
				// ``data.message`` is kept as ``errors[0]`` for
				// backward compat with older renderers; new
				// renderers (useSettingsConfig) prefer
				// ``err.errors`` (joined) when present.
				const data = (
					result as {
						data?: {
							message?: string;
							errors?: string[];
							code?: string;
							// Structured consent fields carried by
							// ``client.consent_required`` envelopes (see
							// HandlerBase._respond_with_error +
							// ConsentRequiredError.to_dict). Preserved onto
							// the thrown Error so the renderer can
							// deep-link to the EXACT Settings toggle.
							consent_field?: unknown;
							engine_name?: unknown;
							model_id?: unknown;
						};
					}
				).data;
				const msg = data?.message ?? "unknown error";
				const errs = Array.isArray(data?.errors)
					? (data?.errors as string[])
					: undefined;
				// Preserve the structured ``code`` (e.g.
				// ``client.consent_required``) onto the thrown Error
				// so callers can branch on the failure class instead
				// of substring-matching the message. Mirrors the
				// ``_error``/``_code`` handling above — without this,
				// the ``client.consent_required`` envelope from the
				// level-monitor / mic-test handlers is indistinguishable
				// from a generic ``internal_error`` and the renderer
				// shows a misleading generic toast.
				const code = data?.code;
				const err = new Error(msg);
				if (typeof code === "string" && code.length > 0) {
					(err as { code?: string }).code = code;
				}
				if (errs && errs.length > 0) {
					(err as { errors?: string[] }).errors = errs;
				}
				// Preserve the structured consent fields (consent_field /
				// engine_name / model_id) the backend attaches to
				// ``client.consent_required`` envelopes so callers can
				// deep-link to the exact Settings toggle (e.g. the
				// level-monitor / mic-test handlers raise
				// ``ConsentRequiredError`` with
				// ``consent_field="voice_biometric_consent"``). Only
				// ``consent_field`` is consumed by the deep-link, but the
				// siblings ride along for completeness / diagnostics.
				const consentField = data?.consent_field;
				const engineName = data?.engine_name;
				const modelId = data?.model_id;
				if (typeof consentField === "string" && consentField.length > 0) {
					(err as { consent_field?: string }).consent_field = consentField;
				}
				if (typeof engineName === "string" && engineName.length > 0) {
					(err as { engine_name?: string }).engine_name = engineName;
				}
				if (typeof modelId === "string" && modelId.length > 0) {
					(err as { model_id?: string }).model_id = modelId;
				}
				throw err;
			}
			return result as T;
		},
		[],
	) as PythonCall;

	// Previously this hook also returned ``isReady: !!api``.
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
	// ``const api = window.python`` and
	// check ``!!api`` directly.  We don't expose a misleading flag.
	return { call };
}
