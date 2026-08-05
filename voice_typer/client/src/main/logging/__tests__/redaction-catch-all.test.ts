// @vitest-environment node
/**
 * Tests for `redactPii`'s 20+ char bare-token catch-all.
 *
 * Background
 * ----------
 * The catch-all pattern `[A-Za-z0-9_-]{20,}` redacts ANY 20+ char
 * bare alphanumeric token, including hex-shaped tokens:
 *   - 32 hex chars — UUID v4 without dashes, random 128-bit secrets
 *     (API keys / session tokens can be emitted as bare 32-hex
 *     strings, so they MUST be redacted).
 *   - 40 hex chars — SHA-1 hashes / Git SHAs / container digests.
 *   - 64 hex chars — SHA-256 hashes / container image digests.
 *
 * A 32-hex string is 128 bits of randomness — the exact shape of a
 * random secret. Redacting all bare 20+ char tokens (with no
 * recognized prefix) is the safe default: an operator can still
 * distinguish commit SHAs by context, but a leaked 32-hex token in a
 * log cannot silently pass the redactor.
 *
 * The catch-all mirrors Python's `_KEY_PATTERNS[-1]` in
 * `voice_typer/server/_secrets.py`, which has no hex exemption.
 *
 * These tests verify:
 *   1. A 32-char hex token (UUID v4 without dashes) IS redacted.
 *   2. A 40-char hex token (SHA-1) IS redacted.
 *   3. A 64-char hex token (SHA-256) IS redacted.
 *   4. A 50-char hex token IS redacted (no length-range exemption).
 *   5. A 19-char bare token is NOT matched (below the 20-char
 *      threshold — unchanged behavior).
 *   6. GitHub / GitLab / Slack PATs ARE redacted (regression guard).
 *   7. Tokens inside `key=value` / `--flag value` ARE redacted by
 *      the earlier SEC-9 patterns.
 *   8. Path-delimiter lookarounds still spare 20+ char path
 *      components.
 *
 * Run alongside the existing `electron-log-redaction-parity.test.ts`,
 * which covers the same contract against the Python implementation.
 */
import { describe, expect, it } from "vitest";

import { redactPii } from "../rotation";

describe("redactPii catch-all: hex-shaped bare tokens are redacted", () => {
	// A canonical UUID v4 without dashes (32 hex chars) — 128 bits
	// of randomness, i.e. a possible random secret.
	const UUID_NO_DASHES = "e3e70682c2094cac1dac62bf75cbb5bd";

	// A canonical SHA-1 hash (40 hex chars).
	const SHA1_HASH = "a3d4e5f6789012345678901234567890abcdef12";

	// A canonical SHA-256 hash (64 hex chars).
	const SHA256_HASH =
		"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

	it("a 32-char hex token (UUID v4 without dashes) IS redacted", () => {
		const input = `commit ${UUID_NO_DASHES} pushed`;
		expect(redactPii(input)).toBe(`commit *** pushed`);
	});

	it("a 40-char hex token (SHA-1 commit hash) IS redacted", () => {
		const input = `git checkout ${SHA1_HASH}`;
		expect(redactPii(input)).toBe(`git checkout ***`);
	});

	it("a 64-char hex token (SHA-256 / container image digest) IS redacted", () => {
		const input = `pulled image sha256:${SHA256_HASH}`;
		expect(redactPii(input)).toBe(`pulled image sha256:***`);
	});

	it("a 50-char hex token (intermediate length) IS redacted", () => {
		// No length-range exemption exists — any 20+ char bare
		// token, hex or not, is redacted.
		const hash50 = "0123456789abcdef".repeat(3) + "01";
		expect(hash50.length).toBe(50);
		const input = `id ${hash50} referenced`;
		expect(redactPii(input)).toBe(`id *** referenced`);
	});

	it("a 19-char bare token is NOT matched by the catch-all (below 20)", () => {
		const input = "token 1234567890123456789 end";
		expect(redactPii(input)).toBe(input);
	});

	it("regression guard: GitHub PAT (ghp_ + 36 chars) IS redacted", () => {
		const githubPat = `ghp_${"a".repeat(36)}`;
		expect(redactPii(`using ${githubPat} for github`)).toBe(
			`using *** for github`,
		);
	});

	it("regression guard: GitLab PAT (glpat- + 20 chars) IS redacted", () => {
		const gitlabPat = "glpat-abcdefghijklmnopqrstuv";
		expect(redactPii(`using ${gitlabPat} for gitlab`)).toBe(
			`using *** for gitlab`,
		);
	});

	it("regression guard: Slack token (xoxb- + 24 chars) IS redacted", () => {
		const slackPat = `xoxb-${"a".repeat(24)}`;
		expect(redactPii(`using ${slackPat} for slack`)).toBe(
			`using *** for slack`,
		);
	});

	it("tokens inside `key=value` patterns are redacted by SEC-9 (before the catch-all)", () => {
		const input = `config key=${UUID_NO_DASHES} loaded`;
		expect(redactPii(input)).toBe(`config key=*** loaded`);
	});

	it("tokens inside `--flag value` patterns are redacted by SEC-9", () => {
		const input = `starting with --token ${UUID_NO_DASHES} and more`;
		expect(redactPii(input)).toBe(`starting with --token *** and more`);
	});

	it("does NOT redact 20+ char path components (forward slash lookaround)", () => {
		const input = `/home/user/${UUID_NO_DASHES}/logs`;
		expect(redactPii(input)).toBe(input);
	});

	it("does NOT redact 20+ char path components (backslash lookaround)", () => {
		const input = `C:\\Users\\${UUID_NO_DASHES}\\logs`;
		expect(redactPii(input)).toBe(input);
	});
});
