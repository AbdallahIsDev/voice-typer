// src/renderer/src/hooks/usePython.ts

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { PythonPushEvent } from "@/types/ipc";

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

// ─── CR-18: per-command timeout table ────────────────────────────────
//
// A blanket 120s `setTimeout` is applied to every IPC call by the
// Electron main process's `sendToPython` (client/src/main/index.ts:
// 507-644) and by the Rust `dispatch` command (src-tauri/src/main.rs:
// 522-552). A `get_status` call that hangs takes 120s to surface an
// error; the 120s timer is created even for trivial commands.
//
// The renderer's `call` function (below) wraps the underlying bridge
// call in a `Promise.race` against a per-command timeout, so:
//   - `get_status` / `get_config` surface a hang in 5s instead of 120s.
//   - `download_model` is allowed up to 10 minutes (large model files
//     over slow links).
//   - Unknown commands default to 30s (a reasonable middle ground).
//
// The underlying bridge promise may still resolve later (the Electron
// main / Rust host's 120s timer is still active on their side), but the
// caller sees the renderer-side timeout rejection first.
const COMMAND_TIMEOUTS: Record<string, number> = {
        get_status: 5_000,
        get_config: 5_000,
        get_history: 10_000,
        download_model: 600_000, // 10 minutes
        transcribe: 120_000, // 2 minutes
};

const DEFAULT_COMMAND_TIMEOUT_MS = 30_000;

/**
 * Returns the per-command timeout (ms) for the given IPC command name.
 * Falls back to {@link DEFAULT_COMMAND_TIMEOUT_MS} for unknown commands.
 *
 * Exported for unit testing (see `__tests__/command-timeouts.test.ts`).
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
function withCommandTimeout<T>(promise: Promise<T>, cmd: string): Promise<T> {
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

// ─── CR-6: bridge-ready subscription via useSyncExternalStore ────────
//
// `usePythonEvent` previously returned early from its `useEffect` when
// `window.python` was undefined at mount, and the effect's only
// dependency was `[type]` — so if `window.python` was installed later
// (e.g. by the Tauri bridge's auto-install on first import, or by the
// Electron preload under slow HMR), the subscription was never
// re-attempted and events were silently dropped.
//
// `useBridgeReady` polls `window.python` presence every 100ms until it
// appears, then notifies React via the `useSyncExternalStore` callback.
// Including `bridgeReady` in the effect's dependency array causes the
// effect to re-run when `window.python` becomes available, so the
// subscription is created lazily on first bridge availability.
function subscribeBridgeReady(callback: () => void): () => void {
        // XV-159: short-circuit when the bridge is already installed at
        // subscribe time. `useSyncExternalStore` calls `subscribe` once per
        // component instance, so without this guard each mounted consumer
        // (`usePythonEvent` callers) would spin up its own 100ms polling
        // interval even though `window.python` is already present. In the
        // normal production path the preload/bridge install runs before
        // React mounts, so this short-circuit eliminates all 12+ polling
        // intervals that would otherwise tick forever (the snapshot never
        // flips back to `false`, so `clearInterval` only fires on unmount).
        // Returning a no-op cleanup matches the contract: subscribers must
        // return an unsubscribe function.
        if (getBridgeReadySnapshot()) return () => {};
        // Poll every 100ms until window.python is available, then stop.
        // The interval self-clears on first detection to avoid leaking a
        // timer once the bridge is installed.
        //
        // If `window.python` is already set at subscribe time, the first
        // tick (≤100ms later) detects it and calls `callback()`. React
        // re-renders, `getSnapshot()` returns the same `true`, and the
        // effect (which already ran with `bridgeReady=true` on the
        // initial render) does not re-run — so the no-op re-render is
        // harmless.
        const interval = setInterval(() => {
                if (typeof window.python !== "undefined") {
                        callback();
                        clearInterval(interval);
                }
        }, 100);
        return () => clearInterval(interval);
}

function getBridgeReadySnapshot(): boolean {
        return typeof window.python !== "undefined";
}

function getBridgeReadyServerSnapshot(): boolean {
        // During SSR (no `window`), the bridge is never ready. Vitest's
        // jsdom env always has `window`, so this only fires in true SSR.
        return false;
}

/**
 * Returns `true` once `window.python` is installed (by the Electron
 * preload script or by `installTauriBridge()`). Re-render-safe via
 * `useSyncExternalStore`: the snapshot is a stable boolean.
 *
 * Used by {@link usePythonEvent} to re-attempt the event subscription
 * when the bridge becomes available after mount (CR-6).
 */
export function useBridgeReady(): boolean {
        return useSyncExternalStore(
                subscribeBridgeReady,
                getBridgeReadySnapshot,
                getBridgeReadyServerSnapshot,
        );
}

export function usePython() {
        const call = useCallback(
                async <T = unknown>(
                        type: string,
                        data?: Record<string, unknown>,
                ): Promise<T> => {
                        const api = (window as unknown as WindowWithPython).python;
                        if (!api) throw new Error("Python bridge not available");
                        // CR-18: race the underlying bridge call against a per-command
                        // timeout so a hung trivial command (e.g. `get_status`) surfaces
                        // an error in seconds instead of the prior blanket 120s timeout
                        // imposed by the Electron main / Rust host. The underlying
                        // promise may still resolve later; the caller sees the timeout
                        // rejection first.
                        const result = (await withCommandTimeout(
                                api.call({ type, data }),
                                type,
                        )) as Record<string, unknown>;
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

/**
 * Subscribe to a Python push event of the given ``type``.
 *
 * The ``handler`` is called with the event ``data`` (if any) each time a
 * matching event arrives.  It may optionally return a cleanup function
 * (PVT-G5-019) which is invoked:
 *
 *   1. Before the **next** matching event's handler runs — so rapid
 *      successive events don't accumulate stale async work (e.g. the
 *      ``reloadHotkey`` chain in ``Home.tsx`` can cancel its in-flight
 *      ``get_config`` via a per-invocation ``cancelled`` flag).
 *   2. When the subscription is torn down (unmount, ``type`` change, or
 *      bridge going away) — so resources acquired by the most recent
 *      invocation are released.
 *
 * Handlers that return ``void`` (the common case) keep working unchanged:
 * ``typeof cleanup === "function"`` guards every call site, so a missing
 * return value is a no-op.
 *
 * The handler identity is mirrored into a ref so callers can pass an inline
 * closure without re-subscribing on every render (only ``type`` and
 * ``bridgeReady`` are effect deps).
 */
export function usePythonEvent(
        type: PythonPushEvent["type"],
        handler: (data?: Record<string, unknown>) => (() => void) | undefined,
): void;
/**
 * BG-84: overload accepting an arbitrary ``string`` for forward-compat
 * with backend-added events not yet in the ``PythonPushEvent`` union.
 *
 * The narrow first overload catches typos at compile time for the
 * events we know about (e.g. ``usePythonEvent("transcription_final",
 * ...)`` — ``"transcription_final"`` is in the union, so a typo like
 * ``"past_failed"`` would fail). This second overload accepts any
 * string so the renderer can subscribe to events the backend ships
 * before the renderer's type definitions catch up — at the cost of
 * losing compile-time typo detection for those new events. Callers
 * that pass a string literal matching the union hit the first overload
 * (TS picks the first matching overload); only unknown literals fall
 * through to this one.
 */
export function usePythonEvent(
        type: string,
        handler: (data?: Record<string, unknown>) => (() => void) | undefined,
): void;
export function usePythonEvent(
        type: string,
        handler: (data?: Record<string, unknown>) => (() => void) | undefined,
) {
        const handlerRef = useRef(handler);
        handlerRef.current = handler;

        // CR-6: track `window.python` presence so the effect re-runs when the
        // bridge becomes available after mount. Previously the effect's only
        // dependency was `[type]`, so if `window.python` was unset at mount
        // (e.g. slow preload / late Tauri bridge install), the subscription
        // was never re-attempted and events were silently dropped.
        const bridgeReady = useBridgeReady();

        useEffect(() => {
                // CR-6: short-circuit until the bridge is installed. Without this
                // guard the effect would call `api.onEvent` on a still-undefined
                // `window.python` and silently drop the subscription; including
                // `bridgeReady` in the dep array (below) is what makes React
                // re-run this effect once the bridge comes online.
                if (!bridgeReady) return;
                const api = (window as unknown as WindowWithPython).python;
                if (!api) return; // defensive double-check (bridgeReady mirrors window.python presence)

                // PVT-G5-019: capture the most recent handler-returned cleanup
                // function so we can invoke it before the next matching event's
                // handler runs (cancelling any in-flight async work from the
                // previous invocation) and on unsubscribe (releasing resources
                // acquired by the most recent invocation).
                let currentCleanup: (() => void) | undefined | undefined;

                const runCleanup = () => {
                        if (typeof currentCleanup !== "function") return;
                        const fn = currentCleanup;
                        currentCleanup = undefined;
                        try {
                                fn();
                        } catch (err) {
                                // A throwing cleanup must not break subsequent
                                // event delivery — log and continue.
                                console.error("usePythonEvent cleanup threw:", err);
                        }
                };

                const unsubscribe = api.onEvent((event) => {
                        if (event.type === type) {
                                // Invoke the previous cleanup BEFORE the next
                                // handler so concurrent invocations compose
                                // correctly (e.g. stale `reloadHotkey` chains
                                // are cancelled before a new one starts).
                                runCleanup();
                                currentCleanup = handlerRef.current(event.data);
                        }
                });

                return () => {
                        // On unsubscribe (unmount / type change / bridge going
                        // away), invoke the most recent cleanup so the handler
                        // can release its resources (e.g. flip its own
                        // `cancelled` flag).
                        runCleanup();
                        unsubscribe();
                };
                // `bridgeReady` is included so the effect re-subscribes when
                // `window.python` becomes available post-mount.
        }, [type, bridgeReady]);
}
