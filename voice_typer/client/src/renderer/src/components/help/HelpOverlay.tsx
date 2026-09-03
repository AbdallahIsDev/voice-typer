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
 * All static key strings (Esc, Tab / Shift+Tab, Space, Enter, ?, the
 * Alt+←/Alt+→ nav pair, and the in-app Ctrl+* shortcuts) come from the
 * `SHORTCUTS` catalog in `components/hotkey/shortcuts.ts` — the single
 * source of truth shared with TitleBar and Sidebar — so the overlay
 * always reflects the bindings the hooks actually handle and can never
 * drift from the tooltips.
 *
 * Scroll structure: the DialogContent is capped at `max-h-[85vh]` and
 * clips (`overflow-hidden`) — an internal scrollbar on a rounded
 * panel escapes the corner radius on Windows classic scrollbars
 * (Chromium "scrollbars escaping border-radius"). The TITLE, the
 * description, and the whole body therefore live inside ONE inner
 * scroll wrapper (grid row `minmax(0,1fr)`), so the header scrolls
 * naturally with the content instead of being pinned above a scrolled
 * body. Negative horizontal/bottom margins cancel the panel padding so
 * the scrollbar sits flush at the panel edge, where the panel's
 * overflow-hidden + rounded corners clip it to the corner curves.
 */
import { memo } from "react";
import { Modal } from "@/components/common/Modal";
import { PunctuationCheatSheet } from "@/components/help/PunctuationCheatSheet";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { IN_APP_SHORTCUTS, SHORTCUTS } from "@/components/hotkey/shortcuts";
import {
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
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
			// Roomier panel — the old `sm` size was clamped to
			// max-w-xs (320px) at every breakpoint, leaving the
			// shortcut list + cheat sheet cramped.
			size="lg"
			// The PANEL clips (overflow-hidden) instead of scrolling:
			// an internal scrollbar on a rounded panel escapes the
			// corner radius on Windows classic scrollbars (Chromium
			// "scrollbars escaping border-radius"). The title, body,
			// and footer scroll together in the inner wrapper below —
			// there is no pinned header row (grid row 1 is the single
			// scrollable region). shadow-none: the popup's drop shadow
			// was removed per user request.
			className="max-h-[85vh] overflow-hidden shadow-none grid-rows-[minmax(0,1fr)]"
		>
			{/* Scroll wrapper: the single grid row holds the header
				    (title + description) AND the body so the whole modal
				    scrolls naturally as one unit. Negative horizontal/bottom
				    margins cancel the panel padding so the scrollbar sits
				    flush at the panel edge — where the panel's
				    overflow-hidden + rounded corners clip it to the corner
				    curves (no more scrollbar escaping the rounded shape). */}
			<div
				data-testid="help-overlay-scroll"
				className="-mx-6 -mb-6 flex min-h-0 flex-col gap-6 overflow-y-auto px-6 pb-6"
			>
				{/* Dialog header lives INSIDE the scroll area (no pinned
				    header row): the title/description scroll away with the
				    content. Radix wires aria-labelledby/aria-describedby to
				    these as long as they render inside DialogContent, and
				    DialogContent's onOpenAutoFocus still targets the title. */}
				<DialogHeader>
					<DialogTitle>{t("help.title")}</DialogTitle>
					<DialogDescription>{t("help.description")}</DialogDescription>
				</DialogHeader>

				{/* The punctuation cheat sheet lives ONCE in this overlay
				    (bottom section). The standalone PunctuationCheatSheetButton
				    (a second `?` that opened its own cheat-sheet popup) is
				    deliberately NOT mounted here — the only help affordance
				    is the title-bar `?` which opens exactly this overlay. */}
				<ul className="flex flex-col gap-2 text-sm">
					{[
						{ keys: dictationLabel, desc: t("help.dictation") },
						{
							keys: SHORTCUTS.cancel.keys,
							desc: t(SHORTCUTS.cancel.labelKey),
						},
						{ keys: repasteLabel, desc: t("help.repaste") },
						{
							keys: SHORTCUTS.navigate.keys,
							desc: t(SHORTCUTS.navigate.labelKey),
						},
						{ keys: SHORTCUTS.toggle.keys, desc: t(SHORTCUTS.toggle.labelKey) },
						{
							keys: SHORTCUTS.activate.keys,
							desc: t(SHORTCUTS.activate.labelKey),
						},
						{
							// Renderer keyboard binding — toggles dictation through
							// the same `toggle_dictation` IPC the mic button uses.
							keys: SHORTCUTS.toggleDictation.keys,
							desc: t(SHORTCUTS.toggleDictation.labelKey),
						},
						{
							// OS-global bubble-dismiss accelerator — registered in
							// the Electron main process, rendered from the same
							// catalog entry so the overlay can't drift from it.
							keys: SHORTCUTS.dismissBubble.keys,
							desc: t(SHORTCUTS.dismissBubble.labelKey),
						},
						{
							keys: SHORTCUTS.openHelp.keys,
							desc: t(SHORTCUTS.openHelp.labelKey),
						},
						{
							// Combined back/forward row — built from the two
							// catalog entries so it can't drift from the
							// TitleBar tooltips.
							keys: `${SHORTCUTS.navBack.keys} / ${SHORTCUTS.navForward.keys}`,
							desc: t(SHORTCUTS.navBack.labelKey),
						},
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
				`IN_APP_SHORTCUTS` array in `components/hotkey/shortcuts.ts`
				(the same catalog TitleBar and Sidebar render from).
				Rendering from the same array keeps the overlay in
				lock-step with the actual key bindings. */}
				<div className="flex flex-col gap-2">
					<h3 className="text-sm font-medium text-(--text-primary)">
						{t("help.shortcuts.title")}
					</h3>
					<ul className="flex flex-col gap-2 text-sm">
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
				</div>

				<PunctuationCheatSheet />
				<p className="text-xs text-(--text-muted)">
					{t("help.closeHint", { key: SHORTCUTS.cancel.keys })}
				</p>
			</div>
		</Modal>
	);
}

// Wrap in React.memo so stable callbacks from App.tsx can
// short-circuit re-renders when no props have changed.
export const HelpOverlay = memo(HelpOverlayInner);
