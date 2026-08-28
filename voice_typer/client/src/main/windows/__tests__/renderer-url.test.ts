// @vitest-environment node
import { describe, expect, it } from "vitest";

import { sanitizeRendererUrl } from "../renderer-url";

describe("sanitizeRendererUrl", () => {
	it("accepts an http dev-server URL unchanged", () => {
		expect(sanitizeRendererUrl("http://localhost:5173")).toBe(
			"http://localhost:5173",
		);
	});

	it("accepts an https URL unchanged", () => {
		expect(sanitizeRendererUrl("https://example.test/app")).toBe(
			"https://example.test/app",
		);
	});

	it("preserves a path/query/hash suffix on the accepted URL", () => {
		const raw = "http://localhost:5173/renderer/index.html?theme=dark#top";
		expect(sanitizeRendererUrl(raw)).toBe(raw);
	});

	it("rejects file: URLs (the production loadFile path must not flow through loadURL)", () => {
		expect(sanitizeRendererUrl("file:///C:/app/index.html")).toBeUndefined();
	});

	it("rejects javascript: URLs", () => {
		expect(sanitizeRendererUrl("javascript:alert(1)")).toBeUndefined();
	});

	it("rejects data: URLs", () => {
		expect(sanitizeRendererUrl("data:text/html,<h1>x</h1>")).toBeUndefined();
	});

	it("rejects empty string", () => {
		expect(sanitizeRendererUrl("")).toBeUndefined();
	});

	it("rejects undefined", () => {
		expect(sanitizeRendererUrl(undefined)).toBeUndefined();
	});

	it("rejects non-URL garbage", () => {
		expect(sanitizeRendererUrl("not a url at all")).toBeUndefined();
	});
});
