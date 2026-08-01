// @vitest-environment node
/**
 * Parity tests for the Electron-side `csvEscape` (RFC 4180 + SEC-015).
 *
 * These tests pin the byte-output of `csvEscape` for the edge cases
 * that the Rust host's `csv_escape` (`src-tauri/src/commands/export.rs`)
 * must match byte-for-byte. The same cases are covered by the Rust
 * `test_csv_escape_*` unit tests in `commands::export::tests`, so any
 * divergence between the two implementations surfaces as a failing
 * test in EITHER layer.
 *
 * Canonical behavior (RFC 4180, chosen on the Rust side):
 *   - Empty string           → ""           (no surrounding quotes)
 *   - Simple string          → unchanged    (no surrounding quotes)
 *   - Contains comma         → wrapped in `"..."` with embedded `"` doubled
 *   - Contains double-quote  → wrapped in `"..."` with embedded `"` doubled
 *   - Contains newline       → wrapped in `"..."` (newline preserved)
 *   - Contains carriage return → wrapped in `"..."` (CR preserved)
 *   - Leading/trailing whitespace → unchanged (whitespace is NOT a quoting trigger)
 *   - Starts with = + - @ \t \r → prefixed with `'` (SEC-015 formula-injection
 *     defense) BEFORE the quoting decision is made. So a cell starting with
 *     `=` that ALSO contains a comma is both prefixed AND quoted.
 *
 * The acceptance criteria for the TS/Rust parity fix lists six input
 * categories: empty, simple, comma, quote, newline, leading/trailing
 * whitespace. All six are covered here.
 */
import { describe, expect, it } from "vitest";

import { csvEscape } from "../ipc/export-handlers";

describe("csvEscape (RFC 4180 + SEC-015 — parity with Rust csv_escape)", () => {
	it("returns an empty string for an empty input (no surrounding quotes)", () => {
		expect(csvEscape("")).toBe("");
	});

	it("returns a simple string unchanged (no surrounding quotes)", () => {
		expect(csvEscape("hello")).toBe("hello");
		expect(csvEscape("123")).toBe("123");
		expect(csvEscape("Hello World")).toBe("Hello World");
	});

	it("wraps a string containing a comma in double quotes", () => {
		expect(csvEscape("hello,world")).toBe('"hello,world"');
		expect(csvEscape("a,b,c")).toBe('"a,b,c"');
	});

	it("wraps a string containing a double-quote and doubles the embedded quote", () => {
		expect(csvEscape('hello"world')).toBe('"hello""world"');
		expect(csvEscape('"')).toBe('""""');
	});

	it("wraps a string containing a newline (newline preserved, not escaped)", () => {
		expect(csvEscape("hello\nworld")).toBe('"hello\nworld"');
	});

	it("wraps a string containing a carriage return (CR preserved, not escaped)", () => {
		expect(csvEscape("hello\rworld")).toBe('"hello\rworld"');
	});

	it("leaves leading/trailing whitespace UNQUOTED (whitespace is not a quoting trigger)", () => {
		// RFC 4180 only requires quoting for comma, double-quote,
		// newline, or CR. Leading/trailing spaces do NOT trigger
		// quoting. This matches the Rust host's `csv_escape`.
		expect(csvEscape("  hello  ")).toBe("  hello  ");
		expect(csvEscape("\thello")).toBe("'\thello");
		// Note: a leading TAB triggers the SEC-015 formula-injection
		// prefix (the cell would otherwise be parsed as a TAB-led
		// formula in some spreadsheets), so it gets the `'` prefix
		// but is NOT wrapped in double quotes (TAB is not a quoting
		// trigger).
	});

	it("combines prefix + quoting when a formula-prefixed cell also contains a comma", () => {
		// SEC-015 prefix applies FIRST, then RFC 4180 quoting on
		// the prefixed value. So `=a,b` → `'=a,b` (prefix) →
		// `"'=a,b"` (quoting, because the prefixed value contains
		// a comma).
		expect(csvEscape("=a,b")).toBe('"\'=a,b"');
		expect(csvEscape("@a,b")).toBe('"\'@a,b"');
	});

	it("combines prefix + quoting when a formula-prefixed cell also contains a quote", () => {
		expect(csvEscape('=a"b')).toBe('"\'=a""b"');
	});

	it("handles all-special-chars in one cell", () => {
		// comma, double-quote, newline, AND carriage return.
		expect(csvEscape('a,b"c\nd\re')).toBe('"a,b""c\nd\re"');
	});

	// ── SEC-015 formula-injection defense (single-quote prefix) ──────

	it("prefixes cells starting with `=` to defend against formula injection", () => {
		expect(csvEscape("=cmd|'/C calc'!A1")).toBe("'=cmd|'/C calc'!A1");
		// Note: the prefixed value contains neither comma nor quote
		// nor newline nor CR, so it is NOT wrapped in double quotes.
	});

	it("prefixes cells starting with `+`", () => {
		expect(csvEscape("+1+1")).toBe("'+1+1");
	});

	it("prefixes cells starting with `-`", () => {
		expect(csvEscape("-2+3")).toBe("'-2+3");
	});

	it("prefixes cells starting with `@`", () => {
		expect(csvEscape("@SUM(A1:A2)")).toBe("'@SUM(A1:A2)");
	});

	it("prefixes cells starting with a TAB", () => {
		expect(csvEscape("\tcmd")).toBe("'\tcmd");
	});

	it("prefixes cells starting with a CR (and then quotes, since CR is a quoting trigger)", () => {
		// CR is BOTH a SEC-015 prefix trigger AND an RFC 4180
		// quoting trigger. So `\rcmd` → `'\rcmd` (prefix) →
		// `"'rcmd"` — wait, no: the prefixed value is `'\rcmd`
		// which still contains a CR, so it gets wrapped in
		// double quotes.
		expect(csvEscape("\rcmd")).toBe('"\'\rcmd"');
	});

	// ── non-string coercion ──────────────────────────────────────────

	it("coerces null/undefined to an empty string", () => {
		expect(csvEscape(null)).toBe("");
		expect(csvEscape(undefined)).toBe("");
	});

	it("coerces numbers via String()", () => {
		expect(csvEscape(42)).toBe("42");
		expect(csvEscape(0)).toBe("0");
		expect(csvEscape(-1)).toBe("'-1"); // SEC-015: leading `-` → prefix
		expect(csvEscape(3.14)).toBe("3.14");
	});

	it("coerces booleans via String()", () => {
		expect(csvEscape(true)).toBe("true");
		expect(csvEscape(false)).toBe("false");
	});

	it("coerces bigints via String()", () => {
		expect(csvEscape(0n)).toBe("0");
		expect(csvEscape(123n)).toBe("123");
	});

	it("coerces objects via JSON.stringify (no quoting unless the JSON contains a comma/quote/newline/CR)", () => {
		// `JSON.stringify({a:1})` → `{"a":1}` which contains a
		// double-quote → triggers RFC 4180 quoting.
		expect(csvEscape({ a: 1 })).toBe('"{""a"":1}"');
		// Arrays: `[1,2]` → `[1,2]` contains a comma → quoting.
		expect(csvEscape([1, 2])).toBe('"[1,2]"');
	});
});

/**
 * Sanity-check: the exact set of inputs the acceptance criteria calls
 * out. Each input is asserted against the byte-identical Rust output.
 * (The Rust side's `test_csv_escape_*` cases in `commands::export::tests`
 * mirror these exact assertions; if either layer changes, the other
 * layer's test fails.)
 */
describe("csvEscape acceptance-criteria parity (UE-42 part c)", () => {
	const cases: Array<{ name: string; input: unknown; expected: string }> = [
		{ name: "empty string", input: "", expected: "" },
		{ name: "simple string", input: "hello", expected: "hello" },
		{ name: "string with comma", input: "a,b", expected: '"a,b"' },
		{
			name: "string with quote",
			input: 'a"b',
			expected: '"a""b"',
		},
		{
			name: "string with newline",
			input: "a\nb",
			expected: '"a\nb"',
		},
		{
			name: "leading whitespace (no quoting)",
			input: "  hello",
			expected: "  hello",
		},
		{
			name: "trailing whitespace (no quoting)",
			input: "hello  ",
			expected: "hello  ",
		},
	];

	for (const { name, input, expected } of cases) {
		it(`matches Rust byte-for-byte: ${name}`, () => {
			expect(csvEscape(input)).toBe(expected);
		});
	}
});
