// `usePython`, the renderer's IPC `call` hook.
//
// Extracted from `hooks/usePython.ts` (now a public barrel) so the
// bridge modules live by concern under `lib/python-bridge/`. The
// public API is unchanged: consumers keep importing
// `{ usePython }` from `@/hooks/usePython`.

import { useCallback } from "react";
// Import the `PythonCallErrorCode` union so the renderer can narrow
// `result._code` against the typed union. The canonical declaration
// lives in the predecessor main process's `python-call-handler.ts`
// (outside the web tsconfig's `include` scope, a cross-boundary import
// would fail `tsc --noEmit` with `TS6307`). The renderer-side mirror
// lives in `types/ipc/enums.ts` (this file's import below); the two
// declarations MUST stay in sync, both files carry a comment pointing
// to the other.
import type { PythonCallErrorCode } from "@/types/ipc/enums";
import type { PythonRequest } from "@/types/ipc/requests";
import { withCommandTimeout } from "./command-timeouts";
import { parseTauriErrorEnvelope } from "./error-envelope";

/**
 * Single-flight registry for concurrent identical IPC reads.
 *
 * Startup mounts several independent readers at once (connection probe,
 * theme reload, Home initial load, Models lifecycle), and dev StrictMode
 * double-invokes mount effects, so identical `get_*` reads overlap
 * in flight within the same second. Each overlap previously issued its
 * own backend round-trip. Concurrent calls with the same command + payload
 * now share one underlying `window.python.call` promise; entries
 * self-remove on settle so sequential calls still re-fetch fresh data.
 * Writes are never shared: only idempotent reads are eligible, so two
 * concurrent mutations (e.g. toggles) still issue twice.
 */
const _inFlightReads = new Map<string, Promise<unknown>>();

function _stableStringify(value: unknown): string {
	if (value === null || value === undefined) return "null";
	if (typeof value !== "object") return JSON.stringify(value) ?? "null";
	if (Array.isArray(value))
		return `[${value.map((entry) => _stableStringify(entry)).join(",")}]`;
	const entries = Object.entries(value as Record<string, unknown>).sort(
		([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
	);
	return `{${entries
		.map(([k, v]) => `${JSON.stringify(k)}:${_stableStringify(v)}`)
		.join(",")}}`;
}

function _isSingleFlightEligible(type: string): boolean {
	return type.startsWith("get_") || type === "onboarding_is_first_run";
}

function _singleFlightKey(
	type: string,
	data?: Record<string, unknown>,
): string {
	return `${type}|${_stableStringify(data ?? null)}`;
}

/** Clear the single-flight registry (tests only). */
export function __resetPythonSingleFlightForTests(): void {
	_inFlightReads.clear();
}

type PythonBridgeApi = NonNullable<typeof window.python>;

/**
 * Type of the ``call`` function returned by {@link usePython}.
 *
 * Two overloads, a strict one that narrows the ``data`` parameter
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
 * on the ``["data"]`` index, TypeScript can't index a union where
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
		<T = unknown>(type: string, data?: Record<string, unknown>): Promise<T> => {
			const api: PythonBridgeApi | undefined = window.python;
			if (!api) return Promise.reject(new Error("Python bridge not available"));
			const execute = async (): Promise<T> => {
				// Race the underlying bridge call against a per-command
				// timeout so a hung trivial command (e.g. `get_status`) surfaces
				// an error in seconds instead of the prior blanket 120s timeout
				// imposed by the predecessor main / Rust host. The underlying
				// promise may still resolve later; the caller sees the timeout
				// rejection first.
				//
				// Tauri/predecessor error-envelope normalization. On
				// Tauri v2, `invoke` rejects with a RAW STRING (not an Error)
				// when the Rust `dispatch` command returns an Err, the host's
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
				// below, on Tauri the await throws before we ever inspect the
				// resolved value (the in-code `_error`/`type:"error"` checks
				// are predecessor-path-only, see the comment below).
				let result: Record<string, unknown>;
				try {
					result = (await withCommandTimeout(
						api.call({ type, data }),
						type,
					)) as Record<string, unknown>;
				} catch (err) {
					if (err instanceof Error) throw err;
					// On Tauri the Rust `dispatch` command rejects the
					// invoke promise with a raw STRING, for structured errors
					// it's the JSON-serialized `{type:"error", data:{code,
					// message}}` envelope (sidecar_cmds/dispatch.rs). Parse it
					// so `err.code` is stamped and callers that branch on the
					// failure class work on Tauri exactly as they do on
					// predecessor (previously the whole JSON string became the
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
				// envelope shapes that can flow back over the predecessor
				// path, surfacing each as a real JS Error so callers
				// using `try { await python.call(...) } catch (e) {}`
				// see failures instead of silently treating the error
				// envelope as a successful result (which previously left
				// callers reading `undefined` from data fields).
				//
				//   1. `{_error: "..."}`, predecessor main-process synthetic
				//      errors (index.ts:1908/1911/1916): backend-not-
				//      connected and sendToPython exceptions. `_error` is
				//      a STRING in the actual predecessor code; we also
				//      accept `{message: "..."}` defensively.
				//   2. `{type:"error", data:{code, message}}`, Python
				//      server unhandled-dispatch exceptions
				//      (ipc_server.py:1044-1050). The predecessor main
				//      process resolves the pending request with this
				//      object verbatim (it does NOT translate it into
				//      `{_error: ...}`).
				//
				// On Tauri, NEITHER in-code check is reachable: the Rust
				// `dispatch` command (main.rs:954-965) rejects the
				// `invoke` promise on `type:"error"` (and never produces
				// `{_error:...}`), so `await api.call(...)` throws before
				// we ever inspect the resolved value. The checks below
				// are therefore predecessor-path-only, DEAD CODE on Tauri,
				// but harmless (and the unified error shape keeps
				// caller-facing behavior consistent across both runtimes).
				// Errors on Tauri propagate as-is from the Rust rejection
				// (no double-wrapping), the `await` throws and we never
				// reach the envelope inspection.
				if (result && typeof result === "object" && "_error" in result) {
					const e = (result as { _error?: unknown })._error;
					const msg =
						typeof e === "string"
							? e
							: ((e as { message?: string } | null)?.message ??
								"unknown error");
					// Surface the structured ``_code`` field
					// (e.g. ``command_timeout``,
					// ``backend_not_connected``,
					// ``backend_exited_early``) so callers can branch
					// on retry / surface-toast / escalate. Pre-fix,
					// the envelope's ``_code`` was dropped on the
					// floor and every error became a plain
					// ``new Error(msg)``, consumers could not
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
					// ``_error``/``_code`` handling above, without this,
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
			};
			if (!_isSingleFlightEligible(type)) return execute();
			const key = _singleFlightKey(type, data);
			const existing = _inFlightReads.get(key);
			if (existing) return existing as Promise<T>;
			const pending = (execute() as Promise<T>).finally(() => {
				_inFlightReads.delete(key);
			}) as Promise<T>;
			_inFlightReads.set(key, pending as Promise<unknown>);
			return pending;
		},
		[],
	) as PythonCall;

	// Previously this hook also returned ``isReady: !!api``.
	// That flag was always ``true`` in production because the preload
	// script installs ``window.python`` before the React app mounts, so
	// every consumer's ``if (!isReady) return`` guard was dead code.
	// Worse, the name suggested "Python backend is ready" when it
	// actually meant "Python bridge exists", callers that wanted real
	// readiness should track ``connectionStatus === 'connected'`` in
	// App.tsx (which probes the backend via ``get_config``).
	//
	// If a future caller needs to distinguish "bridge installed" from
	// "bridge missing" (e.g. running outside predecessor), they can do
	// ``const api = window.python`` and
	// check ``!!api`` directly.  We don't expose a misleading flag.
	return { call };
}
