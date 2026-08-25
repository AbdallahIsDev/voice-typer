/**
 * Unit tests for the Tauri rejection-string envelope parser
 * (`lib/python-bridge/error-envelope.ts`).
 *
 * The Rust host's `dispatch` command passes the sidecar's error
 * envelope through VERBATIM (the `VoiceTyperError` passthrough — see
 * `src-tauri/src/error.rs`): the invoke promise rejects with a STRING
 * whose contents are `{"type":"error","data":<sidecar data verbatim>}`.
 * These tests pin the renderer-side parsing contract:
 *
 * - `err.code` / `err.message` extraction (the pre-existing behavior).
 * - `err.errors[]` stamping for multi-field validation failures.
 * - `err.consent_field` / `err.engine_name` / `err.model_id` stamping
 *   for `client.consent_required` envelopes (deep-link data), with the
 *   SAME guards the Electron path in `usePython.ts` applies (non-empty
 *   strings / arrays only; JSON `null` model_id stays `undefined`).
 * - `err.legacy_code` — the documented Tauri-only superset (the
 *   transitional alias the server emits alongside the canonical
 *   namespaced `code`).
 * - Byte-level fixture: the EXACT string serde_json produces for the
 *   Rust passthrough (map keys sort alphabetically — `"data"` before
 *   `"type"`) parses into the expected fields, pinning the end-to-end
 *   Rust-serialize → renderer-parse contract.
 */
import { describe, expect, it } from "vitest";

import { parseTauriErrorEnvelope } from "../error-envelope";

type ParsedError = Error & {
	code?: string;
	errors?: string[];
	consent_field?: string;
	engine_name?: string;
	model_id?: string;
	legacy_code?: string;
};

function parse(raw: unknown): ParsedError | null {
	if (typeof raw !== "string") throw new Error("fixture must be a string");
	return parseTauriErrorEnvelope(raw) as ParsedError | null;
}

describe("parseTauriErrorEnvelope — code + message (pre-existing behavior)", () => {
	it("stamps err.code + extracts message from a structured error envelope", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: { code: "command_timeout", message: "IPC timed out" },
			}),
		);
		expect(err).toBeInstanceOf(Error);
		expect(err?.message).toBe("IPC timed out");
		expect(err?.code).toBe("command_timeout");
	});

	it("falls back to the raw string for non-envelope rejections", () => {
		expect(parse("dispatch timeout (120s)")).toBeNull();
		expect(parse("sidecar not connected")).toBeNull();
		expect(parse("{not json")).toBeNull();
		expect(parse("42")).toBeNull();
		expect(parse('{"type":"ok"}')).toBeNull();
		expect(parse('{"type":"error"}')).toBeNull();
		expect(parse('{"data":{"code":"x"}}')).toBeNull();
	});
});

describe("parseTauriErrorEnvelope — errors[] stamping (validation parity)", () => {
	it("stamps a non-empty errors array verbatim", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.invalid_field",
					message: "invalid fields",
					errors: ["sample_rate must be > 0", "channels must be 1 or 2"],
				},
			}),
		);
		expect(err?.code).toBe("client.invalid_field");
		expect(err?.message).toBe("invalid fields");
		expect(err?.errors).toEqual([
			"sample_rate must be > 0",
			"channels must be 1 or 2",
		]);
	});

	it("does NOT stamp an empty errors array (Electron-path guard)", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: { code: "client.invalid_field", message: "x", errors: [] },
			}),
		);
		expect(err?.errors).toBeUndefined();
	});

	it("does NOT stamp a non-array errors value", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.invalid_field",
					message: "x",
					errors: "not a list",
				},
			}),
		);
		expect(err?.errors).toBeUndefined();
	});
});

describe("parseTauriErrorEnvelope — consent fields (client.consent_required)", () => {
	it("stamps consent_field / engine_name / model_id when non-empty strings", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.consent_required",
					message: "consent required",
					consent_field: "voice_biometric_consent",
					engine_name: "whisper",
					model_id: "large-v3",
				},
			}),
		);
		expect(err?.code).toBe("client.consent_required");
		expect(err?.consent_field).toBe("voice_biometric_consent");
		expect(err?.engine_name).toBe("whisper");
		expect(err?.model_id).toBe("large-v3");
	});

	it("maps a JSON null model_id to undefined (Electron-path normalization)", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.consent_required",
					message: "consent required",
					consent_field: "cloud_transcription_consent",
					engine_name: "openai",
					model_id: null,
				},
			}),
		);
		expect(err?.consent_field).toBe("cloud_transcription_consent");
		expect(err?.model_id).toBeUndefined();
	});

	it("does NOT stamp empty-string consent fields", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.consent_required",
					message: "consent required",
					consent_field: "",
					engine_name: "",
				},
			}),
		);
		expect(err?.consent_field).toBeUndefined();
		expect(err?.engine_name).toBeUndefined();
	});
});

describe("parseTauriErrorEnvelope — legacy_code (Tauri-only superset)", () => {
	it("stamps the transitional alias alongside the canonical code", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: {
					code: "client.rate_limited",
					message: "too many requests",
					legacy_code: "rate_limited",
				},
			}),
		);
		expect(err?.code).toBe("client.rate_limited");
		expect(err?.legacy_code).toBe("rate_limited");
	});

	it("does NOT stamp legacy_code when absent or empty", () => {
		const err = parse(
			JSON.stringify({
				type: "error",
				data: { code: "server.internal_error", message: "internal error" },
			}),
		);
		expect(err?.legacy_code).toBeUndefined();

		const errEmpty = parse(
			JSON.stringify({
				type: "error",
				data: { code: "server.internal_error", message: "x", legacy_code: "" },
			}),
		);
		expect(errEmpty?.legacy_code).toBeUndefined();
	});
});

describe("parseTauriErrorEnvelope — Rust passthrough byte fixtures", () => {
	it("parses the exact serde_json output of the Rust Server passthrough (sorted keys)", () => {
		// Byte-for-byte what `VoiceTyperError::Server` serializes to:
		// serde_json (no preserve_order) emits map keys in sorted order —
		// `"data"` before `"type"` — and passes the sidecar's `data`
		// through verbatim. This pins the end-to-end contract: whatever
		// fields the Python backend puts in `data` reach the renderer.
		const rustWire =
			'{"data":{"code":"client.consent_required","consent_field":' +
			'"voice_biometric_consent","engine_name":"whisper","errors":' +
			'["field a","field b"],"message":"consent needed","model_id":null},' +
			'"type":"error"}';
		const err = parse(rustWire);
		expect(err).toBeInstanceOf(Error);
		expect(err?.message).toBe("consent needed");
		expect(err?.code).toBe("client.consent_required");
		expect(err?.errors).toEqual(["field a", "field b"]);
		expect(err?.consent_field).toBe("voice_biometric_consent");
		expect(err?.engine_name).toBe("whisper");
		expect(err?.model_id).toBeUndefined();
	});

	it("parses the exact Rust pending_full / data_too_large envelope bytes", () => {
		// Golden strings pinned by the Rust unit tests in
		// `src-tauri/src/error_tests.rs` — the renderer must parse the
		// exact bytes the host puts on the wire.
		const pendingFull =
			'{"data":{"code":"pending_full","message":' +
			'"Sidecar dispatch queue is full; please retry"},"type":"error"}';
		const err = parse(pendingFull);
		expect(err?.code).toBe("pending_full");
		expect(err?.message).toBe("Sidecar dispatch queue is full; please retry");

		const dataTooLarge =
			'{"data":{"code":"data_too_large","message":' +
			'"dispatch data payload exceeds size cap"},"type":"error"}';
		const err2 = parse(dataTooLarge);
		expect(err2?.code).toBe("data_too_large");
	});
});

describe("parseTauriErrorEnvelope — cross-transport code parity", () => {
	// The SAME fixture envelopes the Electron path resolves as values,
	// the Tauri path rejects as strings. Whatever the transport, the
	// thrown Error must surface the SAME `.code` / `.errors` so callers
	// branch identically on both runtimes.
	const fixtures: Array<{
		code: string;
		message: string;
		errors?: string[];
	}> = [
		{ code: "client.consent_required", message: "consent needed" },
		{
			code: "client.invalid_field",
			message: "invalid fields",
			errors: ["a is bad", "b is bad"],
		},
		{ code: "client.rate_limited", message: "slow down" },
		{ code: "command_timeout", message: "IPC timed out" },
	];

	it.each(fixtures)("surfaces .code and .errors for $code", (fixture) => {
		const err = parse(JSON.stringify({ type: "error", data: fixture }));
		expect(err?.code).toBe(fixture.code);
		expect(err?.message).toBe(fixture.message);
		if (fixture.errors) {
			expect(err?.errors).toEqual(fixture.errors);
		} else {
			expect(err?.errors).toBeUndefined();
		}
	});
});
