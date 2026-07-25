/**
 * Send an IPC message to the Python backend over the authenticated TCP
 * socket, returning a Promise that resolves with the reply data.
 *
 * Extracted from `index.ts` (REF-2).
 *
 * R6-F10 (session-1): the `ALLOWED_COMMANDS` set is now imported
 * directly from `../allowed-commands` (a dependency-free leaf module)
 * instead of `../index`. The previous `from "../index"` import created
 * a circular dependency (`index.ts` → `python/` → `send-to-python.ts`
 * → `index.ts`) that forced Node's CJS resolver to evaluate `index.ts`
 * partially before `sendToPython` was callable, producing
 * hard-to-trace load-order bugs. The canonical declaration lives in
 * `src/main/allowed-commands.ts` so that the Python
 * `test_allowlist_matches_server_commands` test and the vitest Section
 * 13 port can slice the literal `ALLOWED_COMMANDS = new Set([...])`
 * substring from that file.
 */
import { ALLOWED_COMMANDS } from "../allowed-commands";
import {
        MAX_PENDING_REQUESTS,
        RATE_LIMIT_MAX_CALLS,
        RATE_LIMIT_WINDOW_MS,
        state,
} from "../state";

/**
 * Per-renderer sliding-window rate limiter. Keyed by the
 * Electron `WebContents.id` so each renderer window gets its own
 * budget. A renderer that fires `python-call` faster than
 * `RATE_LIMIT_MAX_CALLS` per `RATE_LIMIT_WINDOW_MS` is rejected
 * with a structured error before the message ever reaches the TCP
 * socket — preventing both unbounded `pendingRequests` growth and
 * backend-side overload.
 *
 * `null` key (used by main-process-internal callers that don't have
 * a sender) skips the rate limit. The global `MAX_PENDING_REQUESTS`
 * cap still applies.
 */
const _rendererCallTimestamps: Map<number, number[]> = new Map();

function _rendererRateLimited(senderId: number | null): boolean {
        if (senderId === null) {
                return false;
        }
        const now = Date.now();
        const cutoff = now - RATE_LIMIT_WINDOW_MS;
        const prev = _rendererCallTimestamps.get(senderId) ?? [];
        // Drop timestamps outside the sliding window.
        const kept = prev.filter((t) => t >= cutoff);
        if (kept.length >= RATE_LIMIT_MAX_CALLS) {
                _rendererCallTimestamps.set(senderId, kept);
                return true;
        }
        kept.push(now);
        _rendererCallTimestamps.set(senderId, kept);
        return false;
}

/**
 * Reset the per-renderer rate-limit state. Called from tests
 * to isolate cases and from `stopPython` / `relaunchApp` to give a
 * freshly-booted backend a clean slate.
 */
export function _resetIpcBackpressureForTests(): void {
        _rendererCallTimestamps.clear();
}

/**
 * Commands whose handlers are documented to block for the
 * full duration of a long operation (model download / import, large
 * vocabulary save). These keep the legacy 120s timeout so legitimate
 * slow responses don't false-positive. Every other command gets the
 * shorter 15s timeout so a stuck handler releases its pending-map
 * entry (and the closure it pins) 8x sooner.
 */
const _LONG_RUNNING_COMMANDS: ReadonlySet<string> = new Set([
        "download_model",
        "import_model",
        "delete_model",
        "cancel_download",
        "pause_download",
        "transcribe_audio",
]);

function _isLongRunningCommand(cmd: string): boolean {
        return _LONG_RUNNING_COMMANDS.has(cmd);
}

export function sendToPython(
        msg: Record<string, unknown>,
        senderId: number | null = null,
): Promise<unknown> {
        return new Promise((resolve, reject) => {
                // If a full app relaunch is in flight, reject immediately so
                // pending IPC calls don't sit in pendingRequests until the
                // 5s timeout — the process is about to exit anyway.
                if (state._relaunching) {
                        reject(new Error("Application is restarting"));
                        return;
                }
                if (!state.tcpSocket) {
                        reject(new Error("Python backend is not connected"));
                        return;
                }
                // SEC-019: validate the command against an allowlist before
                // forwarding to the Python backend. Combined with SEC-018
                // (unauth TCP), this prevents a compromised renderer from
                // calling arbitrary IPC commands like set_config / quit_app.
                //
                // ERR-IPC-002 (fix): previously missing `quit_app` and `restart_app`,
                // which broke tray Quit/Restart (stopPython sends `quit_app`).
                // ERR-IPC-003 (fix): removed 6 dead/mismatched entries (`quit`,
                // `restart`, `save_config`, `save_vocabulary_with_diff`,
                // `repaste_last`, `complete_onboarding`) — none exist as server
                // IPC commands. The list now matches the server's actual command
                // names exactly (cross-checked against ipc_server.py _dispatch).
                //
                // NOTE: the canonical ALLOWED_COMMANDS declaration lives in
                // `src/main/allowed-commands.ts` (not here, and no longer in
                // `src/main/index.ts` since R6-F10) so that the Python
                // `test_allowlist_matches_server_commands` test and the
                // vitest Section 13 port can slice the literal
                // `ALLOWED_COMMANDS = new Set([` ... `]);` substring from
                // the source. Do NOT move the declaration into this file.
                const cmd = String(msg?.type ?? "").trim();
                if (!ALLOWED_COMMANDS.has(cmd)) {
                        reject(new Error(`Disallowed IPC command: ${cmd}`));
                        return;
                }
                // Per-renderer rate limit. Rejects a flood from
                // a single sender before it can pin `MAX_PENDING_REQUESTS`
                // entries.
                if (_rendererRateLimited(senderId)) {
                        reject(new Error(`Rate limit exceeded for command: ${cmd}`));
                        return;
                }
                // Global cap on simultaneously-pending requests.
                // Each entry pins a `setTimeout` closure that captures
                // `msg` + `resolve` + `reject` + `timer`; without a cap a
                // compromised renderer could retain tens of MB of live
                // closures for 2 minutes. Reject fast with a structured
                // error rather than letting the Map grow.
                if (state.pendingRequests.size >= MAX_PENDING_REQUESTS) {
                        reject(new Error(`Pending IPC request limit reached (${MAX_PENDING_REQUESTS})`));
                        return;
                }
                const id = state.nextId++;
                (msg as Record<string, unknown>).id = id;
                const line = `${JSON.stringify(msg)}\n`;
                state.tcpSocket.write(line);
                // Non-download commands use a shorter 15s timeout so
                // a stuck handler doesn't pin a 120s closure. Long-running
                // commands (model download, model import, transcription)
                // keep the 120s timeout because they're documented as
                // blocking. The Python-side heartbeat watchdog is at 120s
                // (ipc_server.py) for the same reason — both timeouts must
                // stay in sync for long-running commands.
                const timeoutMs = _isLongRunningCommand(cmd) ? 120000 : 15000;
                //
                // CR-17: capture the timer handle and clearTimeout in BOTH the
                // success and reject paths so the timer doesn't leak after
                // a prompt reply. Previously the timer held a strong reference
                // to `reject` (and the captured `msg`) for the full duration
                // even after the reply arrived — handle-message.ts deletes the
                // pendingRequests entry but never cleared the timer, so the
                // closure stayed in the Node.js timer wheel until it fired and
                // no-op'd the `has(id)` check. Wrapping resolve/reject here lets
                // handle-message.ts transparently clear the timer when it
                // resolves or rejects the entry, releasing the closure early.
                const timer = setTimeout(() => {
                        if (state.pendingRequests.has(id)) {
                                state.pendingRequests.delete(id);
                                const cmd = String(msg?.type ?? "unknown").trim();
                                reject(new Error(`Timeout after ${timeoutMs / 1000}s for command: ${cmd}`));
                        }
                }, timeoutMs);
                state.pendingRequests.set(id, {
                        resolve: (value: unknown) => {
                                clearTimeout(timer);
                                resolve(value);
                        },
                        reject: (reason: unknown) => {
                                clearTimeout(timer);
                                reject(reason);
                        },
                });
        });
}
