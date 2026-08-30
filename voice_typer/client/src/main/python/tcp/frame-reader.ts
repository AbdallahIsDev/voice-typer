/**
 * TCP frame reader: newline-framed JSON dispatch over the socket.
 *
 * Split out of `tcp-connect.ts`. Owns the `data` handler: Buffer
 * reassembly, the SEC-023 overflow cap, byte-exact line scanning, and
 * PII-safe invalid-frame logging before `handleMessage` dispatch.
 */
import type { Socket } from "node:net";
import { TCP_FRAME_MAX_BYTES } from "../../constants";
import { log } from "../../logging";
import { state } from "../../state";
import { handleMessage } from "../handle-message";

export function handleTcpData(client: Socket, chunk: Buffer): void {
	// SEC-023: cap tcpBuffer at 1 MiB (TCP_FRAME_MAX_BYTES) to prevent
	// unbounded memory growth from malformed frames (e.g. a chunk with
	// no newline that never gets split). Drop the connection on overflow.
	state.tcpBuffer = state.tcpBuffer
		? Buffer.concat([state.tcpBuffer as Buffer, chunk])
		: chunk;
	if (state.tcpBuffer.length > TCP_FRAME_MAX_BYTES) {
		const capMiB = TCP_FRAME_MAX_BYTES / (1024 * 1024);
		log.error(
			`[TCP] tcpBuffer exceeded ${capMiB} MiB without a newline - dropping connection (possible malformed frame or oversized Python reply)`,
		);
		// surface a structured "reply too large"
		// error to the renderer BEFORE destroying the socket.
		// Without this, the close handler would reject
		// pending requests with the generic "Python socket
		// closed" message — the renderer would log a
		// confusing socket-closed error and the user would
		// never learn the real cause (a too-large Python
		// reply, e.g. get_history / export_diagnostics on a
		// power-user dataset). Pre-rejecting here means the
		// close handler's `state.pendingRequests` loop finds
		// an empty map (we delete each entry as we reject it)
		// and skips its own rejection.
		const overflowErr = new Error(
			`Python reply exceeded ${capMiB} MiB limit (possible malformed frame or oversized reply)`,
		);
		for (const [id, entry] of state.pendingRequests) {
			state.pendingRequests.delete(id);
			entry.reject(overflowErr);
		}
		state.tcpBuffer = Buffer.alloc(0);
		client.destroy();
		return;
	}
	let newlineIdx: number;
	// biome-ignore lint/suspicious/noAssignInExpressions: classic buffer-scan idiom — assign + test in one expression
	while ((newlineIdx = state.tcpBuffer.indexOf(0x0a)) !== -1) {
		const lineBuf = state.tcpBuffer.subarray(0, newlineIdx);
		state.tcpBuffer = state.tcpBuffer.subarray(newlineIdx + 1);
		const line = lineBuf.toString("utf8");
		if (!line.trim()) continue;
		try {
			//JSON.parse returns
			// `any`; cast to `unknown` and
			// narrow before passing to
			// handleMessage. A non-object
			// payload (array, primitive)
			// would otherwise satisfy the
			// Record<string, unknown> type
			// but break runtime access to
			// .type / .id / .data.
			const msg = JSON.parse(line) as unknown;
			if (typeof msg !== "object" || msg === null) {
				log.warn("[TCP] non-object frame from Python, skipping");
				continue;
			}
			handleMessage(msg as Record<string, unknown>);
		} catch {
			//never log the raw
			// TCP line — invalid-JSON lines
			// may contain transcription_final
			// events with user speech (PII).
			// Log only the length and, when
			// VOICE_TYPER_DEBUG is explicitly
			// enabled, a redacted preview
			// (first 80 chars with control
			// chars stripped) so a developer
			// can still triage framing bugs.
			log.error(
				"[TCP] invalid JSON from Python, skipping line (len=%d)",
				line.length,
			);
			if (process.env.VOICE_TYPER_DEBUG === "1") {
				// biome-ignore lint/suspicious/noControlCharactersInRegex: intentional — strip control chars for safe console preview
				const preview = line.slice(0, 80).replace(/[\x00-\x1f\x7f]/g, "?");
				log.error("[TCP] invalid JSON preview: %s", preview);
			}
		}
	}
}
