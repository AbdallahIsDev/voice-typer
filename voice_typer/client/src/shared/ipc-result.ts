/**
 * Canonical IPC result envelope for host-side command handlers.
 *
 * The Tauri `dispatch` path and typed host commands return:
 *   `{ success: true, ...payload }` or `{ success: false, error?, code? }`.
 *
 * Wire-compatibility contract: the SUCCESS payload is spread at the
 * TOP level (`{ success: true, path, … }`), NOT nested under a `data`
 * key; the renderer reads `result.success` / `result.path` /
 * `result.error` directly.
 *
 * Known legacy divergences (documented, NOT migrated):
 *   - `{ ok, error }`, best-effort window pushes (`setLocale`,
 *     `restartBackend`, `logError`) whose renderer consumers read
 *     `result.ok` (e.g. `useCloudProviders.ts`).
 *   - `{ _error, _code }`, the python-call rejection envelope, a
 *     deliberate per-mechanism contract consumed by `usePython.ts`
 *     (see `python-call-error-code.ts`).
 */
export type IpcResult<T = Record<string, never>> =
	| ({ success: true } & T)
	| { success: false; error?: string };

/**
 * Wrap a host command body with the canonical error envelope.
 *
 * The handler's return is passed through unchanged; a THROW is
 * normalized to `{ success: false, error: <message> }`.
 */
export async function withIpcEnvelope<
	TReturn extends IpcResult<Record<string, unknown>>,
>(
	handler: () => Promise<TReturn> | TReturn,
): Promise<TReturn | { success: false; error: string }> {
	try {
		return await handler();
	} catch (e: unknown) {
		return {
			success: false,
			error: e instanceof Error ? e.message : String(e),
		};
	}
}
