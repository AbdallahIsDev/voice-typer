/**
 * Canonical IPC result envelope for Electron main-process handlers.
 *
 * Every `ipcMain.handle` body previously hand-rolled a
 * `try { … } catch (e) { return { success: false, error: (e as Error).message } }`
 * wrapper (4× in `export-handlers.ts` alone), and the project had
 * THREE divergent envelope shapes (`{success, error}` for exports /
 * window helpers, `{ok, error}` for best-effort window pushes, and
 * `{_error, _code}` for the python-call channel). New handlers had to
 * guess which shape to use.
 *
 * This module is the single source of truth for the `success`-based
 * envelope and the boilerplate wrapper. New handlers SHOULD use
 * `withIpcEnvelope` and return the canonical shape:
 *
 *     ipcMain.handle(MyChannel.x, () =>
 *       withIpcEnvelope(async () => {
 *         const path = await doWork();
 *         return { success: true, path };
 *       }),
 *     );
 *
 * Wire-compatibility contract: the SUCCESS payload is spread at the
 * TOP level (`{ success: true, path, … }`), NOT nested under a `data`
 * key — the renderer (`useHistoryExport`, `useTemplateImportExport`,
 * `useVocabularyImportExport`, model hooks) reads `result.success` /
 * `result.path` / `result.error` directly, and the Tauri bridge
 * (`lib/tauri-bridge/window-namespace.ts`) mirrors the same shape.
 * A future `data`-nesting migration must update ALL consumers + the
 * Tauri mirror in lockstep.
 *
 * Known legacy divergences (documented, NOT migrated — changing them
 * would break pinned contracts):
 *   - `{ ok, error }` — best-effort window pushes (`setLocale`,
 *     `restartBackend`, `logError`) whose renderer consumers read
 *     `result.ok` (e.g. `useCloudProviders.ts`).
 *   - `{ _error, _code }` — the python-call rejection envelope, a
 *     deliberate per-mechanism contract consumed by `usePython.ts`
 *     (see `python-call-error-code.ts`).
 */
export type IpcResult<T = Record<string, never>> =
	| ({ success: true } & T)
	| { success: false; error?: string };

/**
 * Wrap an `ipcMain.handle` body with the canonical error envelope.
 *
 * Removes the ~60 lines of `try { … } catch { return { success: false,
 * error: (e as Error).message } }` boilerplate that was duplicated
 * across `export-handlers.ts` (4×) and `window-handlers.ts`. The
 * handler's return is passed through unchanged (it may be either a
 * `{ success: true, … }` success or a `{ success: false, … }` early
 * return such as dialog-canceled / invalid-format); a THROW is
 * normalized to `{ success: false, error: <message> }` (string
 * coercion via `String(e)` so non-Error throws — numbers, strings —
 * still produce a usable message, matching the previous
 * `(e as Error).message` intent for Errors).
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
