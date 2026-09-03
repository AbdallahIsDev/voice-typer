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
//   - `download_model` / `import_model` get a 1h download-scale budget
//     (see the Rust hard-cap note below).
//   - Unknown commands default to 30s (a reasonable middle ground).
//
// The underlying bridge promise may still resolve later (the Electron
// main / Rust host's timer is still active on their side), but the
// caller sees the renderer-side timeout rejection first.
//
// Rust hard cap on the download-scale commands:
//
// The Rust `dispatch` command routes per-command timeouts: 15s for
// everything outside the model-lifecycle set (`DISPATCH_SHORT_TIMEOUT_SECS`),
// 120s for delete/cancel/pause/resume (`DISPATCH_TIMEOUT_SECS`), and 1h
// for the multi-GB transfer commands `download_model` / `import_model`
// (`DISPATCH_DOWNLOAD_TIMEOUT_SECS`) — see
// `src-tauri/src/commands/sidecar_cmds/dispatch.rs` (`dispatch_timeout_for`)
// and `src-tauri/src/util.rs`. The renderer table below keeps its
// `download_model` / `import_model` entries 5s BELOW the host's 1h cap so
// the renderer surfaces a command-specific timeout error first (the same
// "renderer surfaces first" convention as every other entry).
//
// The earlier 115s renderer cap (just under a 120s host cap) was a bug,
// not a fix: the host cap aborted multi-GB downloads mid-flight while the
// sidecar kept downloading — the UI showed a false failure + Retry over a
// download that was still progressing, and Retry started a duplicate
// backend download.
const COMMAND_TIMEOUTS: Record<string, number> = {
	get_status: 5_000,
	get_config: 5_000,
	get_history: 10_000,
	// Download-scale transfers (multi-GB model files) — 5s BELOW the Rust
	// host's 1h `DISPATCH_DOWNLOAD_TIMEOUT_SECS` hard cap so the renderer
	// surfaces a command-specific timeout error before the host's generic
	// reject. The previous 115s cap fired DURING legitimate large
	// downloads: the backend kept downloading (progress events kept
	// flowing) while the renderer showed a false failure + Retry, and
	// clicking Retry started a duplicate backend download.
	//
	// A genuinely hung download is recovered by the user via Cancel (a
	// separate short-timeout command) — not by a timer.
	download_model: 3_595_000,
	import_model: 3_595_000,
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
