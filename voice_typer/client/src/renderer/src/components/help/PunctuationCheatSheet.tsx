/**
 * PunctuationCheatSheet.
 *
 * A small list of spoken-form → punctuation-character mappings,
 * rendered inside the existing "?" help overlay (App.tsx), and also
 * reachable on its own via {@link PunctuationCheatSheetButton} so it
 * can be surfaced from anywhere (Bubble window, microphone page,
 * onboarding, etc.) without going through the full help overlay.
 *
 * Source of truth: `voice_typer/server/text_cleanup.py:374`
 * (`_RE_SPACING_PUNCT_BEFORE = re.compile(r"\s+([,.;:!?])")`)
 * — these are the punctuation characters Voice Typer's cleanup
 * pipeline recognizes and preserves. Whisper / faster-whisper itself
 * turns the spoken forms ("comma", "period", "question mark", etc.)
 * into the literal characters; text_cleanup.py then normalizes
 * spacing around them without dropping them.
 *
 * We additionally surface "apostrophe", "open quote", "close quote",
 * "hyphen", "dash", "ellipsis", "open paren", "close paren", "tab",
 * "new line", "new paragraph", "capital [word]", and "all caps [word]"
 * — these are common Whisper voice commands that the cleanup pipeline
 * preserves verbatim (apostrophes/quotes are part of `[,.;:!?]`'s
 * sibling punctuation set; new line / new paragraph become `\n` which
 * the pipeline passes through untouched; "capital"/"all caps" are
 * capitalization directives Whisper applies to the next spoken word).
 *
 * The Bubble window does not yet render
 * {@link PunctuationCheatSheetButton} — that integration belongs to
 * the Bubble.tsx file scope. Until that wiring lands,
 * the cheat sheet is reachable from (a) the help overlay Modal in
 * App.tsx, and (b) any other surface that explicitly mounts the
 * button. The affordance itself is fully self-contained here.
 *
 * The component renders an inline {@link SearchField} so users can
 * filter entries by spoken form (e.g. "quote") or by character
 * (e.g. `?`). Filtering is case-insensitive and matches substrings.
 */
import { useState } from "react";
import { Kbd } from "@/components/common/Kbd";
import { Modal } from "@/components/common/Modal";
import { SearchField } from "@/components/common/SearchField";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/i18n";

/**
 * Canonical mapping of spoken-form label key → inserted character.
 *
 * The keys are i18n keys under `help.punctuation.*`. The values are
 * the literal characters / strings the speech model inserts when the
 * user speaks the corresponding word. For directives that operate on
 * a following word ("capital [word]", "all caps [word]"), the value
 * is a representative example showing the effect.
 *
 * If you change this list, also update:
 *   - translations/*.json (`help.punctuation.*` keys)
 *   - src/renderer/src/__tests__/punctuation-cheat-sheet.test.tsx
 *     (asserts the rendered set)
 */
export const PUNCTUATION_ENTRIES: ReadonlyArray<{
	readonly labelKey: string;
	readonly character: string;
}> = [
	{ labelKey: "help.punctuation.comma", character: "," },
	{ labelKey: "help.punctuation.period", character: "." },
	{ labelKey: "help.punctuation.questionMark", character: "?" },
	{ labelKey: "help.punctuation.exclamationPoint", character: "!" },
	{ labelKey: "help.punctuation.semicolon", character: ";" },
	{ labelKey: "help.punctuation.colon", character: ":" },
	{ labelKey: "help.punctuation.apostrophe", character: "'" },
	{ labelKey: "help.punctuation.openQuote", character: '"' },
	{ labelKey: "help.punctuation.closeQuote", character: '"' },
	{ labelKey: "help.punctuation.hyphen", character: "-" },
	{ labelKey: "help.punctuation.dash", character: "—" },
	{ labelKey: "help.punctuation.ellipsis", character: "…" },
	{ labelKey: "help.punctuation.openParen", character: "(" },
	{ labelKey: "help.punctuation.closeParen", character: ")" },
	{ labelKey: "help.punctuation.tab", character: "⇥" },
	{ labelKey: "help.punctuation.newLine", character: "↵" },
	{ labelKey: "help.punctuation.newParagraph", character: "¶" },
	{ labelKey: "help.punctuation.capital", character: "X" },
	{ labelKey: "help.punctuation.allCaps", character: "WORD" },
];

/**
 * Filter the canonical {@link PUNCTUATION_ENTRIES} list by a free-text
 * query. Matches case-insensitively against either the localized
 * spoken-form label or the literal character. Exported so unit tests
 * can verify the matching semantics without poking at DOM state.
 */
export function filterPunctuationEntries(
	entries: ReadonlyArray<{
		readonly labelKey: string;
		readonly character: string;
	}>,
	query: string,
	localize: (key: string) => string,
): typeof entries {
	const q = query.trim().toLowerCase();
	if (!q) return entries;
	return entries.filter((entry) => {
		const label = localize(entry.labelKey).toLowerCase();
		const ch = entry.character.toLowerCase();
		return label.includes(q) || ch.includes(q);
	});
}

export function PunctuationCheatSheet() {
	const t = useT();
	const [query, setQuery] = useState("");
	const filtered = filterPunctuationEntries(PUNCTUATION_ENTRIES, query, t);
	return (
		<section
			data-testid="punctuation-cheat-sheet"
			aria-labelledby="punctuation-cheat-sheet-title"
			className="space-y-2 border-t border-border pt-3"
		>
			<h3
				id="punctuation-cheat-sheet-title"
				className="text-sm font-semibold text-(--text-primary)"
			>
				{t("help.punctuationTitle")}
			</h3>
			<p className="text-xs text-(--text-muted)">{t("help.punctuationHint")}</p>
			<SearchField
				value={query}
				onChange={setQuery}
				placeholder={t("help.searchPlaceholder")}
				ariaLabel={t("help.searchPlaceholder")}
			/>
			{filtered.length === 0 ? (
				<p className="text-xs text-(--text-muted)">
					{t("help.searchNoMatch", { query })}
				</p>
			) : (
				<ul
					className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs"
					data-testid="punctuation-cheat-sheet-list"
				>
					{filtered.map((entry) => (
						<li
							key={entry.labelKey}
							className="flex items-center justify-between gap-2"
							data-testid="punctuation-cheat-sheet-entry"
							data-character={entry.character}
						>
							<span className="text-(--text-muted)">{t(entry.labelKey)}</span>
							{/*
							 * `<code>` is the correct semantic element here — these
							 * are voice-inserted characters, not keyboard shortcuts.
							 * `<kbd>` would imply the user pressed a physical key.
							 * Visual styling is shared with the HelpOverlay shortcut
							 * chips via the `<Kbd>` primitive (which renders `<code>`
							 */}
							<Kbd as="code" className="px-1.5">
								{entry.character}
							</Kbd>
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

/**
 * PunctuationCheatSheetButton — a compact `?` affordance that opens a
 * small Modal containing {@link PunctuationCheatSheet}.
 *
 * This lets the cheat sheet be triggered from anywhere
 * (Bubble window, microphone page, onboarding, etc.) without going
 * through the full help overlay. The Bubble window itself does not
 * yet render this button — that wiring is owned by the Bubble.tsx
 * file scope. Until then, the affordance is available to any
 * surface that explicitly mounts it.
 *
 * The button is a Radix-accessible icon button (`aria-haspopup="dialog"`)
 * with a localized `aria-label`. The Modal inherits the cheat sheet
 * title/description from the existing `help.punctuation*` i18n keys.
 */
export function PunctuationCheatSheetButton() {
	const t = useT();
	const [open, setOpen] = useState(false);
	return (
		<>
			<Button
				type="button"
				variant="ghost"
				size="icon-sm"
				aria-haspopup="dialog"
				aria-expanded={open}
				aria-label={t("help.openCheatSheet")}
				onClick={() => setOpen(true)}
				data-testid="punctuation-cheat-sheet-button"
			>
				{/* Decorative "?" — aria-hidden because the button's aria-label
				 * conveys the same meaning to assistive tech. */}
				<span aria-hidden="true" className="text-sm font-semibold leading-none">
					?
				</span>
			</Button>
			<Modal
				open={open}
				onClose={() => setOpen(false)}
				title={t("help.punctuationTitle")}
				description={t("help.punctuationHint")}
				size="sm"
				// Keep the cheat sheet scrollable on small viewports
				// (Bubble window can be ~320×240). Mirrors the help
				// overlay Modal's own scroll treatment.
				className="w-110 max-h-[85vh] overflow-y-auto"
			>
				<PunctuationCheatSheet />
			</Modal>
		</>
	);
}
