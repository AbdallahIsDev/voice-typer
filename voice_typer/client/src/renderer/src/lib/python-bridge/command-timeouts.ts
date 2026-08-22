// Per-command IPC timeout table for the renderer-side `call` wrapper.
//
// A blanket 120s `setTimeout` is applied to every IPC call by the
// Electron main process's `sendToPython` (client/src/main/index.ts:
// 507-644) and by the Rust `dispatch` command (src-tauri/src/commands/
// sidecar_cmds.rs:67-73 `dispatch_timeout_for` + util.rs:53
// `DISPATCH_TIMEOUT_SECS = 120`). A `get_status` call that hangs takes
// 120s to surface an error; the 120s timer is created even for trivial
// commands.
//
// The renderer's `call` function (see `./usePython.ts`) wraps the
// underlying bridge call in a `Promise.race` against a per-command
// timeout, so:
//   - `get_status` / `get_config` surface a hang in 5s instead of 120s.
//   - `download_model` is allowed a generous budget (but see the Rust
//     hard-cap note below).
//   - Unknown commands default to 30s (a reasonable middle ground).
//
// The underlying bridge promise may still resolve later (the Electron
// main / Rust host's timer is still active on their side), but the
// caller sees the renderer-side timeout rejection first.
//
// Rust hard cap on `download_model`:
//
// The Rust `dispatch` command enforces a hard timeout of 120s for the
// 6 model-lifecycle commands (`download_model`, `import_model`,
// `delete_model`, `cancel_model_download`, `pause_model_download`,
// `resume_model_download`) and 15s for everything else — see
// `src-tauri/src/commands/sidecar_cmds.rs:50-73` and
// `src-tauri/src/util.rs:53`.  The previous `download_model: 600_000`
// (10 min) entry in this table was effectively DEAD CODE: the Rust
// host always rejected first at 120s with the generic
// `"dispatch timeout (120s)"` error, so the renderer's 10-minute
// budget was never the binding constraint.
//
// The entry is now capped at 115_000ms (5s BELOW the Rust 120s hard
// cap) so the renderer surfaces a clearer, command-specific timeout
// error (`IPC command "download_model" timed out after 115000ms`)
// BEFORE the Rust side rejects with its generic message. This gives
// the user an actionable, contextual error instead of a host-side
// reject that doesn't identify which command timed out.
//
// Durable fix (out of scope for this module): extend the Rust
// `DispatchArgs` struct with a `timeout_secs` field so the renderer
// can request a longer budget for legitimate large downloads. Until
// then, downloads that exceed 120s will fail — users on slow links
// should use the `import_model` flow (downloads via browser/curl and
// imports the local file, bypassing the dispatch timeout entirely).
const COMMAND_TIMEOUTS: Record<string, number> = {
	get_status: 5_000,
	get_config: 5_000,
	get_history: 10_000,
	// capped at 115s — 5s below the Rust host's
	// 120s `DISPATCH_TIMEOUT_SECS` hard cap so the renderer surfaces
	// the timeout first with a command-specific error message
	// instead of letting the Rust side reject with the generic
	// "dispatch timeout (120s)" string. The previous 600_000ms
	// (10 min) value was dead code: the Rust dispatch always fired
	// first at 120s.
	download_model: 115_000,
	// `transcribe` was previously listed here at 120s but `transcribe`
	// is NOT a real IPC command (the actual control RPC is
	// `toggle_dictation`, which is a short control call that returns
	// immediately; the recording/transcription itself runs async on
	// the backend and pushes results via `transcription_final`
	// events). The dead `transcribe` entry was leftover from a
	// pre-rename era. Replaced with `toggle_dictation` at 30s so a
	// hung toggle call surfaces an error in 30s instead of falling
	// through to DEFAULT_COMMAND_TIMEOUT_MS (also 30s — explicit is
	// better than implicit so future contributors don't accidentally
	// remove the entry thinking it's the default).
	toggle_dictation: 30_000,
};

const DEFAULT_COMMAND_TIMEOUT_MS = 30_000;

/**
 * Returns the per-command timeout (ms) for the given IPC command name.
 * Falls back to {@link DEFAULT_COMMAND_TIMEOUT_MS} for unknown commands.
 *
 * Exported for unit testing (see `hooks/__tests__/command-timeouts.test.ts`).
 */
export function getTimeout(cmd: string): number {
	return COMMAND_TIMEOUTS[cmd] ?? DEFAULT_COMMAND_TIMEOUT_MS;
}

/**
 * Wraps a promise with a per-command timeout. If the promise does not
 * settle within `getTimeout(cmd)` ms, the returned promise rejects with
 * an `Error` of the form `IPC command "<cmd>" timed out after <ms>ms`.
 *
 * The timeout timer is cleared when the underlying promise settles first
 * (so we don't leak a `setTimeout` reference).
 */
export function withCommandTimeout<T>(
	promise: Promise<T>,
	cmd: string,
): Promise<T> {
	const timeoutMs = getTimeout(cmd);
	let timer: ReturnType<typeof setTimeout> | undefined;
	const timeoutPromise = new Promise<never>((_, reject) => {
		timer = setTimeout(() => {
			reject(new Error(`IPC command "${cmd}" timed out after ${timeoutMs}ms`));
		}, timeoutMs);
	});
	return Promise.race([promise, timeoutPromise]).finally(() => {
		if (timer) clearTimeout(timer);
	});
}
