// SelectableRow, the shared "whole row is clickable" wrapper for
// selectable list rows (radio rows, checkbox rows).
// pair + row-click wrapper (MicrophoneListItem, the system-default row
// in AvailableMicrophonesList, PresetAccordionSelector's option rows,
// TemplateListRow, VocabListRow), each new selectable row re-copied
// the pair and the skip-nested-control logic, and the copies had begun
// drifting in WHICH nested control they skip. This component owns both
// once:
//   - The a11y pair: the row click is a MOUSE-ONLY convenience for
//     selection, the NESTED control (Radix RadioGroupItem /
//     Checkbox, a real role="radio"/"checkbox" button) is the
//     accessible control. Keyboard activation goes through the focused
//     nested control itself (Space/arrows via Radix); a keydown
//     mirror on the row would DOUBLE-FIRE the selection, so the row
//     deliberately has no onKeyDown.
//   - The skip-nested-control logic: clicks that originate on a nested
//     interactive control are left to that control (its own
//     onValueChange/onCheckedChange handles the selection; handling it
//     here too would fire the selection twice for one click). Callers
//     declare WHICH controls to skip via `ignoreClicksFrom` selectors
//     (rows whose nested controls already stopPropagation themselves —
//     e.g. the checkbox rows, pass none).
// This is a PRESENTATION wrapper only: it renders exactly one <div>
// with the caller's props and the gated click handler.

import type { HTMLAttributes, MouseEvent } from "react";

export interface SelectableRowProps
	extends Omit<HTMLAttributes<HTMLDivElement>, "onClick"> {
	onRowSelect: () => void;
	ignoreClicksFrom?: readonly string[];
}

export const RADIO_GROUP_ITEM_SELECTOR = '[data-slot="radio-group-item"]';

export function SelectableRow({
	onRowSelect,
	ignoreClicksFrom,
	...rest
}: SelectableRowProps) {
	const handleClick = (event: MouseEvent<HTMLDivElement>) => {
		// Clicks originating on a nested control are handled by that
		// control itself; handling them here too would fire the
		// selection twice for one click.
		if (
			ignoreClicksFrom?.some((selector) =>
				(event.target as HTMLElement).closest(selector),
			)
		) {
			return;
		}
		onRowSelect();
	};
	return (
		// biome-ignore lint/a11y/noStaticElementInteractions: the nested control (Radix RadioGroupItem / Checkbox) is the accessible control (role=radio/checkbox); the row click is pointer convenience.
		// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation goes through the focused nested control itself (Space/arrows via Radix); a keydown mirror on the row would double-fire the selection.
		<div {...rest} onClick={handleClick} />
	);
}
