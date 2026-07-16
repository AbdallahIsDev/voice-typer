/**
 * Tests for the PunctuationCheatSheet component (NEW-UX-026).
 *
 * Verifies that the cheat sheet:
 *   1. Renders with the expected testid container.
 *   2. Surfaces every punctuation entry from `PUNCTUATION_ENTRIES`
 *      (the canonical list — see the component file for the source
 *      of truth link to text_cleanup.py).
 *   3. Includes the canonical punctuation characters that
 *      text_cleanup.py:374 recognizes: `, . ; : ! ?`.
 *   4. Surfaces "new line" (the directive's required example).
 *
 * This is a vitest unit test, not a full App integration test — the
 * App.test.tsx mock of `@/components/common/Modal` returns a stub
 * that swallows children, so we mount the component directly.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
	PUNCTUATION_ENTRIES,
	PunctuationCheatSheet,
} from "@/components/help/PunctuationCheatSheet";

describe("PunctuationCheatSheet (NEW-UX-026)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the cheat-sheet section with a stable testid", () => {
		render(<PunctuationCheatSheet />);
		const section = screen.getByTestId("punctuation-cheat-sheet");
		expect(section).toBeTruthy();
	});

	it("renders one entry per canonical PUNCTUATION_ENTRIES item", () => {
		render(<PunctuationCheatSheet />);
		const entries = screen.getAllByTestId("punctuation-cheat-sheet-entry");
		expect(entries.length).toBe(PUNCTUATION_ENTRIES.length);
	});

	it("includes all six text_cleanup.py punctuation characters: , . ; : ! ?", () => {
		// text_cleanup.py:374 — `_RE_SPACING_PUNCT_BEFORE = re.compile(r"\s+([,.;:!?])")`
		// is the canonical source of truth for the punctuation Voice Typer
		// preserves. The cheat sheet must surface each of these so users
		// know how to produce them by voice.
		render(<PunctuationCheatSheet />);
		const renderedChars = screen
			.getAllByTestId("punctuation-cheat-sheet-entry")
			.map((li) => li.getAttribute("data-character"));
		// The six characters covered by the regex `[,.;:!?]`:
		for (const ch of [",", ".", ";", ":", "!", "?"]) {
			expect(renderedChars).toContain(ch);
		}
	});

	it('includes "new line" — the directive-required example', () => {
		render(<PunctuationCheatSheet />);
		// The localized label for help.punctuation.newLine in en.json is
		// "New line" — assert it surfaces in the document text.
		const section = screen.getByTestId("punctuation-cheat-sheet");
		expect(section.textContent).toContain("New line");
	});

	it("renders the title and hint text", () => {
		render(<PunctuationCheatSheet />);
		// help.punctuationTitle in en.json is "Punctuation Cheat Sheet".
		expect(screen.getByText("Punctuation Cheat Sheet")).toBeTruthy();
		// help.punctuationHint contains the phrase "Say these words".
		const section = screen.getByTestId("punctuation-cheat-sheet");
		expect(section.textContent).toContain("Say these words");
	});

	it("exposes the spoken-form → character mapping via data-character", () => {
		render(<PunctuationCheatSheet />);
		const entries = screen.getAllByTestId("punctuation-cheat-sheet-entry");
		// Each entry must expose its target character via data-character
		// (used by the assertions above and by potential future axe/e2e
		// tests).
		for (const li of entries) {
			const ch = li.getAttribute("data-character");
			expect(ch).toBeTruthy();
			expect(typeof ch).toBe("string");
		}
	});
});
