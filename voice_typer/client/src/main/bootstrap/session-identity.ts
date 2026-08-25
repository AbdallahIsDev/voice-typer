/**
 * SEC-029 per-session identity derivation.
 *
 * Split out of `bootstrap.ts`. Owns `generateSessionNonce()`:
 * the renderer-facing session nonce AND the cross-process
 * `VOICE_TYPER_SESSION_ID` log-correlation env var.
 */

//prefer the static ``node:crypto`` import over the prior
// defensive dynamic ``require("node:crypto")`` — ``node:crypto`` is a
// guaranteed-built-in module (built into Node since v0.1.92), so the
// dynamic require added ~0 safety at the cost of one extra require
// resolution per ``generateSessionNonce()`` call. The static import
// also lets the bundler tree-shake unused exports.
//
//``randomUUID`` is used both for the SEC-029 session
// nonce AND for the 8-char ``VOICE_TYPER_SESSION_ID`` env var that
// the Rust host / Python sidecar / Electron main process share for
// cross-process log correlation.
import { randomUUID } from "node:crypto";
import { state } from "../state";

/**
 * SEC-029: generate a per-session nonce. Use crypto.randomUUID()
 * when available (Node 14.17+/Electron 12+), fall back to a
 * timestamp+random string. Stored in `state.sessionNonce` and tagged
 * onto every python-event so the renderer can reject replayed frames.
 *
 * : also derives the per-process `VOICE_TYPER_SESSION_ID`
 * (8-char lowercase-hex) used by the cross-process log-correlation
 * bracket. If the env var is already set (e.g. by a parent process
 * like a test harness), the existing value is preserved — otherwise
 * a fresh ID is minted via `crypto.randomUUID()` truncated to 8 hex
 * chars (mirrors the Rust host's `generate_or_load_session_id` and
 * the Python sidecar's `uuid.uuid4().hex[:8]`). The Python sidecar
 * (spawned via `python/index.ts`) inherits the env var via Node's
 * default `child_process` env propagation, so its file log carries
 * the SAME `[session_id]` bracket — operators can grep a single
 * bracket across Rust / Python / Electron log files.
 */
export function generateSessionNonce(): void {
	try {
		//``randomUUID`` is a top-level binding imported from
		// ``node:crypto`` (see the import block above) — no dynamic
		// require needed.
		//
		// ``randomUUID`` is available on Node 14.17+ / Electron 12+
		// (both well below our minimum supported versions — see
		// ``package.json``'s ``engines.node`` field).
		const uuid = randomUUID();
		state.sessionNonce = uuid;
		//derive the 8-char session ID from the UUID's
		// first 8 hex chars (the ``uuid`` is already lowercase-hex
		// with dashes; strip dashes and take the first 8). This
		// matches the shape minted by the Rust host's
		// ``generate_or_load_session_id`` and the Python sidecar's
		// ``uuid.uuid4().hex[:8]``.
		if (!process.env.VOICE_TYPER_SESSION_ID) {
			process.env.VOICE_TYPER_SESSION_ID = uuid.replace(/-/g, "").slice(0, 8);
		}
	} catch {
		state.sessionNonce = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
		//best-effort fallback — if ``randomUUID``
		// threw (truly broken Crypto module), mint a less-random
		// 8-char ID from ``Date.now()`` + ``Math.random`` so the
		// bracket is still present for cross-process correlation.
		if (!process.env.VOICE_TYPER_SESSION_ID) {
			process.env.VOICE_TYPER_SESSION_ID =
				`${Date.now().toString(16).slice(-8)}${Math.random()
					.toString(16)
					.slice(2, 6)}`.slice(0, 8);
		}
	}
}
