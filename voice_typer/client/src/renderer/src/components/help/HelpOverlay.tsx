/**
 * HelpOverlay — Modal-based keyboard-shortcut reference overlay.
 *
 * Extracted from App.tsx (BG-27, Phase 4.5 spaghetti split) to keep App.tsx
 * a pure layout shell. Behaviour is byte-identical to the original inline
 * `<Modal>` block (App.tsx L577-624): renders the same shortcut list in the
 * same order, the same `PunctuationCheatSheet`, and the same "press Esc to
 * close" hint footer.
 *
 * The overlay is opened by App.tsx in response to the `?` keydown handler
 * (also extracted — kept inline in App.tsx because it's tightly coupled to
 * the `showHelpOverlay` state and the dialog-state querySelector guard).
 *
 * The `dictationLabel` / `repasteLabel` props are pre-formatted hotkey
 * strings (e.g. "F2", "Ctrl+Shift+V") computed by App.tsx from the user's
 * config — the overlay itself doesn't read config so it stays a pure
 * presentational component.
 */
import { Modal } from "@/components/common/Modal";
import { PunctuationCheatSheet } from "@/components/help/PunctuationCheatSheet";
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

export function HelpOverlay({
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
			size="sm"
			className="w-110"
		>
			<ul className="space-y-2 text-sm">
				{[
					{ keys: dictationLabel, desc: t("help.dictation") },
					{ keys: t("help.keys.cancel"), desc: t("help.cancel") },
					{ keys: repasteLabel, desc: t("help.repaste") },
					{
						keys: t("help.keys.toggleSidebar"),
						desc: t("help.toggleSidebar"),
					},
					{
						keys: t("help.keys.openSettings"),
						desc: t("help.openSettings"),
					},
					{ keys: t("help.keys.goHome"), desc: t("help.goHome") },
					{ keys: t("help.keys.navigate"), desc: t("help.navigate") },
					{ keys: t("help.keys.toggle"), desc: t("help.toggle") },
					{ keys: t("help.keys.activate"), desc: t("help.activate") },
					{
						keys: t("help.keys.zoomTextSize"),
						desc: t("help.zoomTextSize"),
					},
					{ keys: t("help.keys.openHelp"), desc: t("help.openHelp") },
					{ keys: t("help.keys.navBack"), desc: t("help.navBack") },
				].map((shortcut) => (
					<li
						key={shortcut.keys}
						className="flex items-center justify-between gap-4"
					>
						<span className="text-(--text-muted)">{shortcut.desc}</span>
						<kbd className="rounded border border-border bg-(--bg-subtle) px-2 py-0.5 font-mono text-xs text-(--text-primary)">
							{shortcut.keys}
						</kbd>
					</li>
				))}
			</ul>
			<PunctuationCheatSheet />
			<p className="text-xs text-(--text-muted)">
				{t("help.closeHint", { key: "Esc" })}
			</p>
		</Modal>
	);
}
