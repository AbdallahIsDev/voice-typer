/**
 * @vitest-environment node
 *
 *  regression coverage: the structured logger's `formatLine`
 * (and `appendLifecycleLine`) redact PII / API-key / URL-credential
 * content before persisting to disk.
 *
 * Background
 * ----------
 * The pre-fix `formatLine` in `structuredLogger.ts` JSON-stringified
 * the args + message and wrote the result to `electron-main.log`
 * without any PII redaction. The companion `printfLogger.ts` already
 * ran args through `redactPii` (via `formatArgsForFile`), but the
 * structured logger bypassed it — an asymmetric leak where the
 * printf-style logger was safe but the message-first `logger.warn` /
 * `logger.error` path could persist user-spoken text, bearer tokens,
 * API keys, and URL credentials to disk.
 *
 * The fix ports the same `redactPii` (re-exported from
 * `./rotation.ts`) into `formatLine` and `appendLifecycleLine` so
 * both code paths apply the same redaction.
 *
 * These tests verify:
 *   (a) `logger.warn(message_with_email)` — the line captured by the
 *       mocked `appendLogLine` has the email replaced with `[EMAIL]`.
 *   (b) `logger.error(msg, { token: "Bearer ..." })` — the JSON-
 *       stringified arg has the bearer token replaced with
 *       `Bearer ***`.
 *   (c) `logger.warn` with a URL containing userinfo
 *       (`https://user:pass@host`) has the userinfo stripped.
 *   (d) The opt-in `appendLifecycleLine` path (via `logger.info` under
 *       `PERSIST_INFO=1`) ALSO redacts ( lifecycle path).
 *   (e) The redaction is idempotent on already-redacted text (so
 *       callers that pre-redact via `cleanConsoleMsg` chains don't
 *       double-redact).
 */
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock `electron` so `app.isPackaged` returns `false` so the dev-mode
// `electron-main.log` write fires (required for `logger.warn` /
// `logger.error` to reach `appendLogLine`).
vi.mock("electron", () => ({
	app: {
		isPackaged: false,
	},
}));

// O1: log paths resolve via the dependency-free `computeConfigDir`
// leaf + `/logs` (NOT `app.getPath("userData")` anymore).
const MOCK_USERDATA = "/tmp/vt-xz-log-03-test-userdata";

vi.mock("../config-dir", () => ({
	computeConfigDir: () => MOCK_USERDATA,
}));

const MAIN_LOG_PATH = path.join(MOCK_USERDATA, "logs", "electron-main.log");
const LIFECYCLE_LOG_PATH = path.join(
	MOCK_USERDATA,
	"logs",
	"electron-lifecycle.log",
);

// Track calls to the mocked `appendLogLine`. The mock implementation
// is a no-op (does not touch the filesystem) so the test can assert
// purely on the call args. The REAL `redactPii` is preserved (not
// mocked) so the integration is exercised end-to-end.
const appendLogLineMock = vi.fn();
vi.mock("../logging/rotation", async () => {
	// Use the REAL redactPii so the test exercises the actual
	// PII / API-key / URL-credential patterns (not a stub). The
	// importActual is awaited inside the factory so vi.mock's
	// hoisting doesn't break the binding.
	const actual = await vi.importActual<typeof import("../logging/rotation")>(
		"../logging/rotation",
	);
	return {
		appendLogLine: (...args: unknown[]) => appendLogLineMock(...args),
		rotateIfNeeded: vi.fn(),
		cleanConsoleMsg: vi.fn(),
		redactPii: actual.redactPii,
		ts: vi.fn(() => "12:00:00"),
	};
});

/**
 * Dynamically import `logging.ts` AFTER setting the env var, so the
 * module-level `PERSIST_INFO` constant binds to `true`. Returns a fresh
 * module namespace.
 */
async function importLoggingFresh(): Promise<typeof import("../logging")> {
	vi.resetModules();
	return await import("../logging");
}

describe("XZ-LOG-03: structuredLogger formatLine redacts PII / API keys / URL credentials", () => {
	const originalEnv = process.env.VOICE_TYPER_ELECTRON_INFO_LOG;

	beforeEach(async () => {
		appendLogLineMock.mockClear();
		await importLoggingFresh();
	});

	afterEach(() => {
		appendLogLineMock.mockReset();
		if (originalEnv === undefined) {
			delete process.env.VOICE_TYPER_ELECTRON_INFO_LOG;
		} else {
			process.env.VOICE_TYPER_ELECTRON_INFO_LOG = originalEnv;
		}
		vi.resetModules();
	});

	it("logger.warn redacts email addresses in the message", async () => {
		const { logger } = await importLoggingFresh();
		logger.warn("user logged in as alice@example.com");

		// Find the call that targeted the main log (the lifecycle
		// log is only used for INFO under PERSIST_INFO=1).
		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[WARN]");
		expect(line).toContain("[EMAIL]");
		expect(line).not.toContain("alice@example.com");
	});

	it("logger.error redacts Bearer tokens in JSON-stringified args", async () => {
		const { logger } = await importLoggingFresh();
		logger.error("api call failed", {
			authorization: "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIx",
		});

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[ERROR]");
		expect(line).toContain("Bearer ***");
		expect(line).not.toContain("eyJhbGciOiJIUzI1NiJ9");
	});

	it("logger.warn redacts URL userinfo (user:pass@host)", async () => {
		const { logger } = await importLoggingFresh();
		// Use a host that doesn't double as an email domain
		// (the email pattern would otherwise redact first,
		// masking the URL-userinfo strip). `internal-host` has
		// no dot, so the email regex (which requires a dot in
		// the domain) doesn't match — leaving the URL-userinfo
		// pattern to strip `alice:secretpass@`.
		logger.warn("fetching from https://alice:secretpass@internal-host/api");

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		// The userinfo (alice:secretpass@) must be stripped.
		expect(line).not.toContain("alice:secretpass@");
		// The scheme + host must be preserved (so the operator can
		// still see which endpoint was being fetched).
		expect(line).toContain("https://");
		expect(line).toContain("internal-host");
	});

	it("logger.warn redacts US-style phone numbers", async () => {
		const { logger } = await importLoggingFresh();
		logger.warn("callback requested for 555-123-4567");

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[PHONE]");
		expect(line).not.toContain("555-123-4567");
	});

	it("logger.warn redacts credit-card-like numbers", async () => {
		const { logger } = await importLoggingFresh();
		logger.warn("payment method 4111 1111 1111 1111 rejected");

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[CC]");
		expect(line).not.toContain("4111 1111 1111 1111");
	});

	it("logger.error redacts Error.stack contents (email in stack)", async () => {
		const { logger } = await importLoggingFresh();
		const err = new Error("failed for user bob@example.com");
		logger.error("operation failed", err);

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[EMAIL]");
		expect(line).not.toContain("bob@example.com");
	});

	it("redaction is idempotent on already-redacted text (no double-redact)", async () => {
		const { logger } = await importLoggingFresh();
		// A pre-redacted message — calling redactPii on this
		// should be a no-op (the `[EMAIL]` token doesn't match
		// any PII pattern). The line should pass through with
		// the redaction token intact.
		logger.warn("user logged in as [EMAIL]");

		const mainCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === MAIN_LOG_PATH,
		) as unknown[] | undefined;
		expect(mainCall).toBeDefined();
		const line = String(mainCall?.[1]);
		expect(line).toContain("[EMAIL]");
	});

	it("appendLifecycleLine (PERSIST_INFO=1) also redacts PII in the INFO stream", async () => {
		// Set the env var BEFORE importing so PERSIST_INFO binds to true.
		process.env.VOICE_TYPER_ELECTRON_INFO_LOG = "1";
		const { logger } = await importLoggingFresh();
		logger.info("login event for carol@example.com");

		// Under PERSIST_INFO=1, logger.info calls appendLogLine
		// twice: once for the dev-mode main log, once for the
		// lifecycle log. The lifecycle call should ALSO be
		//redacted (the  fix ports redactPii into
		// appendLifecycleLine too).
		const lifecycleCall = appendLogLineMock.mock.calls.find(
			(c: unknown[]) => c[0] === LIFECYCLE_LOG_PATH,
		) as unknown[] | undefined;
		expect(lifecycleCall).toBeDefined();
		const line = String(lifecycleCall?.[1]);
		expect(line).toContain("[INFO]");
		expect(line).toContain("[EMAIL]");
		expect(line).not.toContain("carol@example.com");
	});
});
