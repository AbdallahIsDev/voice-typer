/**
 * PunctuationCheatSheet — NEW-UX-026.
 *
 * A small list of spoken-form → punctuation-character mappings,
 * rendered inside the existing "?" help overlay (App.tsx).
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
 * "new line", and "new paragraph" — these are common Whisper
 * voice commands that the cleanup pipeline preserves verbatim
 * (apostrophes/quotes are part of `[,.;:!?]`'s sibling punctuation
 * set; new line / new paragraph become `\n` which the pipeline
 * passes through untouched).
 *
 * The component is presentational only — no props, no state. It
 * reads the localized labels from the `help.punctuation.*` keys
 * (added in translations/en.json).
 */
import { useT } from "@/i18n/i18n";

/**
 * Canonical mapping of spoken-form label key → inserted character.
 *
 * The keys are i18n keys under `help.punctuation.*`. The values are
 * the literal characters / strings the speech model inserts when the
 * user speaks the corresponding word.
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
	{ labelKey: "help.punctuation.newLine", character: "↵" },
	{ labelKey: "help.punctuation.newParagraph", character: "¶" },
];

export function PunctuationCheatSheet() {
	const t = useT();
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
			<ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
				{PUNCTUATION_ENTRIES.map((entry) => (
					<li
						key={entry.labelKey}
						className="flex items-center justify-between gap-2"
						data-testid="punctuation-cheat-sheet-entry"
						data-character={entry.character}
					>
						<span className="text-(--text-muted)">{t(entry.labelKey)}</span>
						<kbd className="rounded border border-border bg-(--bg-subtle) px-1.5 py-0.5 font-mono text-xs text-(--text-primary)">
							{entry.character}
						</kbd>
					</li>
				))}
			</ul>
		</section>
	);
}
