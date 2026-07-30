// @vitest-environment node
/**
 * XE-20-1 + XE-20-2 regression coverage:
 *
 * 1. Cross-layer parity: feeds a corpus of known-secret shapes through
 *    `redactPii` and asserts the output matches the expected redaction.
 *    The expected values are verified to match Python's `redact_secret`
 *    (which is what Python's `redact_pii` delegates to for the API-key /
 *    SEC-9 portion) — see the companion Rust parity test in
 *    `src-tauri/src/platform/logging.rs::test_xe_20_1_cross_layer_parity_rust`
 *    for the Rust side, and the worklog F17 entry for the manual Python
 *    verification.
 *
 * 2. XE-20-2: `gsk_` pattern + widened `sk-`/`pk-`/`key-` charset.
 *    Asserts `redactPii("groq key gsk_1234567890abcdef")` returns
 *    `"groq key gsk_***"` and `redactPii("openai sk-proj-1234567890abcdef")`
 *    returns `"openai sk-***"`.
 *
 * 3. XE-20-1: SEC-9 flag / key=value patterns + generic 20+ char bare
 *    token + XE-5-A path-delimiter lookarounds.
 */
import { describe, expect, it } from "vitest";

import { redactPii } from "../rotation";

describe("XE-20-1: cross-layer parity (TS side)", () => {
	// Each tuple: [input, expected_redacted_output].
	// The expected outputs are verified to match Python `redact_secret`
	// (which is what the cross-layer parity test feeds into on the
	// Python side). Tokens are built via string concatenation so the
	// literal token strings don't appear in source (avoids tripping
	// secret scanners that match `ghp_` / `xoxb-` prefixes).
	const githubPat = `ghp_${"a".repeat(36)}`;
	const gitlabPat = "glpat-abcdefghijklmnopqrstuv";
	const slackPat = `xoxb-${"a".repeat(24)}`;

	const cases: Array<[string, string]> = [
		[githubPat, "***"],
		[gitlabPat, "***"],
		[slackPat, "***"],
		["--token=abc123", "--token=***"],
		["api_key=xyz123", "api_key=***"],
	];

	for (const [input, expected] of cases) {
		it(`redacts ${input.length > 30 ? `${input.slice(0, 30)}…` : input} → ${expected}`, () => {
			const out = redactPii(input);
			expect(out).toBe(expected);
		});
	}
});

describe("XE-20-2: gsk_ pattern + widened sk- charset", () => {
	it("redacts gsk_ Groq API keys", () => {
		expect(redactPii("groq key gsk_1234567890abcdef")).toBe("groq key gsk_***");
	});

	it("redacts sk-proj- OpenAI project keys (with dashes)", () => {
		// XE-20-2: pre-fix the charset [A-Za-z0-9]{10,} did NOT
		// include `-`, so `sk-proj-1234567890abcdef` (contains
		// dashes) was NOT redacted. The widened charset
		// [A-Za-z0-9_\-]{8,} matches Python and Rust.
		expect(redactPii("openai sk-proj-1234567890abcdef")).toBe("openai sk-***");
	});

	it("redacts pk- public keys (XE-20-7 typo fix: was pk_)", () => {
		// XE-20-7: the old comment said `pk_...` but the regex
		// matched `pk-` (with a dash). The comment is now fixed.
		expect(redactPii("stripe pk-live-1234567890abcdef")).toBe("stripe pk-***");
	});
});

describe("XE-20-1: SEC-9 flag / key=value patterns", () => {
	it("redacts --token=abc long-flag form", () => {
		expect(redactPii("starting with --token=abc123 and more")).toBe(
			"starting with --token=*** and more",
		);
	});

	it("redacts --token abc long-flag space form", () => {
		expect(redactPii("starting with --token abc123 and more")).toBe(
			"starting with --token *** and more",
		);
	});

	it("redacts token=abc bare key=value form", () => {
		expect(redactPii("config token=abc123 loaded")).toBe(
			"config token=*** loaded",
		);
	});

	it("redacts api_key=xyz (longer keyword wins over key)", () => {
		expect(redactPii("config api_key=xyz loaded")).toBe(
			"config api_key=*** loaded",
		);
	});

	it("redacts password=hunter2", () => {
		expect(redactPii("auth password=hunter2 rejected")).toBe(
			"auth password=*** rejected",
		);
	});

	it("is case-insensitive (Python (?i) flag)", () => {
		expect(redactPii("CONFIG TOKEN=ABC123 LOADED")).toBe(
			"CONFIG TOKEN=*** LOADED",
		);
	});

	it("does NOT match keyword inside larger word (monkey=abc)", () => {
		const input = "monkey=abc stays unchanged";
		expect(redactPii(input)).toBe(input);
	});

	it("value runs until whitespace (key=sk-... more)", () => {
		expect(redactPii("key=sk-1234567890abcdef more")).toBe("key=*** more");
	});
});

describe("XE-20-1: generic 20+ char bare-token pattern", () => {
	it("redacts GitHub PAT (ghp_ + 36 chars)", () => {
		const token = `ghp_${"a".repeat(36)}`;
		expect(redactPii(`using ${token} for github`)).toBe("using *** for github");
	});

	it("redacts GitLab PAT (glpat- + 20 chars)", () => {
		expect(redactPii("using glpat-abcdefghijklmnopqrstuv for gitlab")).toBe(
			"using *** for gitlab",
		);
	});

	it("redacts Slack token (xoxb- + 24 chars)", () => {
		const token = `xoxb-${"a".repeat(24)}`;
		expect(redactPii(`using ${token} for slack`)).toBe("using *** for slack");
	});

	it("redacts exactly 20-char bare token", () => {
		expect(redactPii("token 12345678901234567890 end")).toBe("token *** end");
	});

	it("does NOT redact 19-char bare token", () => {
		const input = "token 1234567890123456789 end";
		expect(redactPii(input)).toBe(input);
	});
});

describe("XE-5-A: path-delimiter lookarounds", () => {
	it("does NOT redact 20+ char path component (forward slash)", () => {
		const input = "config dir: /home/username_with_long_name/logs";
		expect(redactPii(input)).toBe(input);
	});

	it("does NOT redact 20+ char path component (backslash)", () => {
		const input = "C:\\Users\\username_with_long_name\\logs";
		expect(redactPii(input)).toBe(input);
	});

	it("redacts 20+ char token NOT in a path", () => {
		const token = `ghp_${"a".repeat(36)}`;
		expect(redactPii(`key is ${token} here`)).toBe("key is *** here");
	});
});
