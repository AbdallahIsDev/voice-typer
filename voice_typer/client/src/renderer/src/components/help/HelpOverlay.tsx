/**
 * HelpOverlay — Modal-based keyboard-shortcut reference overlay.
 *
 * Extracted from App.tsx (Phase 4.5 spaghetti split) to keep App.tsx
 * a pure layout shell. Behaviour is byte-identical to the original inline
 * `<Modal>` block: renders the same shortcut list in the same order,
 * the same `PunctuationCheatSheet`, and the same "press Esc to close"
 * hint footer.
 *
 * The overlay is opened by App.tsx in response to the `?` keydown handler
 * (also extracted — kept inline in App.tsx because it's tightly coupled to
 * the `showHelpOverlay` state and the dialog-state querySelector guard).
 *
 * The `dictationLabel` / `repasteLabel` props are pre-formatted hotkey
 * strings (e.g. "F2", "Ctrl+Shift+V") computed by App.tsx from the user's
 * config — the overlay itself doesn't read config so it stays a pure
 * presentational component.
 *
 * In-app shortcuts (Ctrl+B, Ctrl+,, Ctrl+H, Ctrl+=, Ctrl+-) are rendered
 * from the `IN_APP_SHORTCUTS` constant exported by
 * `useGlobalKeyboardShortcuts` so the overlay always reflects the
 * bindings the hook actually handles — closing the loophole where the
 * overlay's text drifted from the implementation.
 */
import { memo } from "react";
import { Modal } from "@/components/common/Modal";
import { PunctuationCheatSheet } from "@/components/help/PunctuationCheatSheet";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { IN_APP_SHORTCUTS } from "@/hooks/useGlobalKeyboardShortcuts";
import { useT } from "@/i18n/i18n";

interface HelpOverlayProps {
	/** Whether the overlay Modal is open. */
	open: boolean;
	/** Called when the user dismisses the overlay (Esc, backdrop click). */
	onClose: () => void;
	/** Pre-formatted dictation hotkey label (e.g. "F2", "Caps Lock"). */
	dictationLabel: string;
	/** Pre-formatted repaste hotkey label (e.g. "Ctrl+Alt+V"). */
	repasteLabel: string;
}

function HelpOverlayInner({
	open,
	onClose,
	dictationLabel,
	repasteLabel,
}: HelpOverlayProps) {
	const t = useT();

	return (
		<Modal
			open={open}
			onClose={onClose}
			title={t("help.title")}
			description={t("help.description")}
			// Roomier panel — the old `sm` size was clamped to
			// max-w-xs (320px) at every breakpoint, leaving the
			// shortcut list + cheat sheet cramped.
			size="lg"
			// The PANEL clips (overflow-hidden) instead of scrolling:
			// an internal scrollbar on a rounded-4xl panel escapes the
			// corner radius on Windows classic scrollbars (Chromium
			// "scrollbars escaping border-radius"). The body scrolls
			// in the inner wrapper below instead. shadow-none: the
			// popup's drop shadow was removed per user request.
			className="max-h-[85vh] overflow-hidden shadow-none grid-rows-[auto_minmax(0,1fr)]"
		>
			{/* Scroll wrapper (grid row 2): the title row above stays
				    pinned. Negative horizontal/bottom margins cancel the
				    panel padding so the scrollbar sits flush at the panel
				    edge — where the panel's overflow-hidden + rounded-4xl
				    clips it to the corner curves (no more scrollbar
				    escaping the rounded shape). */}
			<div
				data-testid="help-overlay-scroll"
				className="-mx-6 -mb-6 min-h-0 overflow-y-auto px-6 pb-6"
			>
				{/* The punctuation cheat sheet lives ONCE in this overlay
				    (bottom section). The standalone PunctuationCheatSheetButton
				    (a second `?` that opened its own cheat-sheet popup) is
				    deliberately NOT mounted here — the only help affordance
				    is the title-bar `?` which opens exactly this overlay. */}
				<ul className="space-y-2 text-sm">
					{[
						{ keys: dictationLabel, desc: t("help.dictation") },
						{ keys: t("help.keys.cancel"), desc: t("help.cancel") },
						{ keys: repasteLabel, desc: t("help.repaste") },
						{ keys: t("help.keys.navigate"), desc: t("help.navigate") },
						{ keys: t("help.keys.toggle"), desc: t("help.toggle") },
						{ keys: t("help.keys.activate"), desc: t("help.activate") },
						{ keys: t("help.keys.openHelp"), desc: t("help.openHelp") },
						{ keys: t("help.keys.navBack"), desc: t("help.navBack") },
					].map((shortcut) => (
						<li
							key={shortcut.keys}
							className="flex items-center justify-between gap-4"
						>
							<span className="text-(--text-muted)">{shortcut.desc}</span>
							<HotkeyChips keys={shortcut.keys} />
						</li>
					))}
				</ul>

				{/* In-app keyboard shortcuts — sourced from the
				`IN_APP_SHORTCUTS` constant exported by
				`useGlobalKeyboardShortcuts` (the hook that implements
				them). Rendering from the same array keeps the overlay
				in lock-step with the actual key bindings. */}
				<h3 className="mt-4 text-sm font-medium text-(--text-primary)">
					{t("help.shortcuts.title")}
				</h3>
				<ul className="mt-2 space-y-2 text-sm">
					{IN_APP_SHORTCUTS.map((shortcut) => (
						<li
							key={`${shortcut.category}-${shortcut.keys}`}
							className="flex items-center justify-between gap-4"
						>
							<span className="text-(--text-muted)">
								{t(shortcut.labelKey)}
							</span>
							<HotkeyChips keys={shortcut.keys} />
						</li>
					))}
				</ul>

				<PunctuationCheatSheet />
				<p className="text-xs text-(--text-muted)">
					{t("help.closeHint", { key: "Esc" })}
				</p>
			</div>
		</Modal>
	);
}

// Wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed.
export const HelpOverlay = memo(HelpOverlayInner);
