// Available-microphones list.
//
// ONE unified RadioGroup: the first row is "System Default" (value
// maps to ``null``), followed by every reported device — INCLUDING the
// currently-active one, rendered checked. Radix radio groups need the
// active item present and checked so arrow-key navigation and the
// checked visual work for the whole set; there are no per-row "Use"
// buttons anymore — selection IS the radio.
//
// While a test is running the items carry a real ``disabled`` attribute
// (keyboard + AT safe; CSS-only pointer blocking was a keyboard hole)
// and rows dim via opacity.
//
// Falls back to an ``EmptyState`` (``MicOff01Icon``) when the backend
// reports zero microphones.

import { Mic02Icon, MicOff01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { MouseEvent } from "react";
import { EmptyState } from "@/components/feedback/EmptyState";
import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { t } from "@/i18n/i18n";
import type { MicrophoneDevice } from "@/types/config";

/**
 * Sentinel RadioGroup value for the OS-default device — Radix radio
 * values are strings, but the backend's "system default" state is
 * ``config.microphone === null``, so the sentinel maps to ``null`` in
 * the selection handler.
 */
export const SYSTEM_DEFAULT_MIC_VALUE = "__system_default__";

export interface AvailableMicrophonesListProps {
	/** All microphones reported by the backend. */
	microphones: MicrophoneDevice[];
	/** Currently-selected device id, or ``null`` for the OS default. */
	activeMicId: string | null;
	/** Disables list interaction while a test recording is in flight. */
	testRunning: boolean;
	/** Selection handler — receives ``null`` for "use system default". */
	onSelectMicrophone: (micId: string | null) => void;
}

function rowClickHandler(
	onSelect: () => void,
	disabled: boolean,
	isActive: boolean,
): (event: MouseEvent<HTMLDivElement>) => void {
	return (event) => {
		// Clicks that originate on the radio control itself are handled by
		// Radix (onValueChange); handling them here too would fire the
		// selection IPC twice for one click.
		if (
			(event.target as HTMLElement).closest('[data-slot="radio-group-item"]')
		) {
			return;
		}
		if (disabled || isActive) return;
		onSelect();
	};
}

export function AvailableMicrophonesList({
	microphones,
	activeMicId,
	testRunning,
	onSelectMicrophone,
}: AvailableMicrophonesListProps) {
	if (microphones.length === 0) {
		return (
			<EmptyState
				icon={MicOff01Icon}
				title={t("microphone.noMicrophonesFound")}
				description={t("microphone.connectAndRestart")}
			/>
		);
	}

	const value = activeMicId === null ? SYSTEM_DEFAULT_MIC_VALUE : activeMicId;

	// Default device first (stable sort keeps the backend order otherwise).
	const sorted = [...microphones].sort(
		(a, b) => Number(b.default ?? false) - Number(a.default ?? false),
	);

	const handleValueChange = (next: string) => {
		onSelectMicrophone(next === SYSTEM_DEFAULT_MIC_VALUE ? null : next);
	};

	return (
		<div>
			<p className="text-xs font-semibold uppercase tracking-wide text-(--text-muted) mb-2 px-1">
				{t("microphone.availableMicrophones")}
			</p>
			<RadioGroup
				value={value}
				onValueChange={handleValueChange}
				disabled={testRunning}
				className="rounded-lg border border-border/5 bg-(--bg-subtle)"
				data-testid="microphone-radio-list"
			>
				{/* native <ul>/<li> list semantics around the radio rows — the
				    implicit list/listitem ARIA roles come from the elements
				    themselves (biome's noRedundantRoles + ARIA-in-HTML agree). */}
				<ul className="divide-y divide-border/10">
					<li className={testRunning ? "opacity-50" : undefined}>
						{/* biome-ignore lint/a11y/noStaticElementInteractions: the nested RadioGroupItem is the accessible control (role=radio); the row click is pointer convenience. */}
						{/* biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation goes through the focused radio itself (Space/arrows via Radix); a keydown mirror here would double-fire the selection. */}
						<div
							className={
								"flex items-center gap-3 px-3.5 py-2.5 transition-colors" +
								(testRunning || activeMicId === null
									? ""
									: " cursor-pointer hover:bg-foreground/5")
							}
							onClick={rowClickHandler(
								() => onSelectMicrophone(null),
								testRunning,
								activeMicId === null,
							)}
							data-testid="system-default-row"
						>
							<HugeiconsIcon
								icon={Mic02Icon}
								strokeWidth={2}
								className="h-4 w-4 shrink-0 text-(--text-muted)"
							/>
							<div className="flex flex-col flex-1 min-w-0 gap-1">
								<p className="text-sm font-medium text-(--text-primary)">
									{t("microphone.systemDefault")}
								</p>
								<p className="text-xs text-(--text-muted)">
									{t("microphone.systemDefaultDesc")}
								</p>
							</div>
							<RadioGroupItem
								value={SYSTEM_DEFAULT_MIC_VALUE}
								disabled={testRunning}
								aria-label={t("microphone.systemDefault")}
							/>
						</div>
					</li>
					{sorted.map((mic) => (
						// Dim device rows during a test to match the
						// system-default row — the whole list reads as one
						// disabled surface, not half-disabled.
						<li
							key={mic.id ?? String(mic.index)}
							className={testRunning ? "opacity-50" : undefined}
						>
							<MicrophoneListItem
								mic={mic}
								checked={(mic.id ?? String(mic.index)) === activeMicId}
								showDefaultBadge={Boolean(mic.default) && activeMicId !== null}
								disabled={testRunning}
								onSelect={() => onSelectMicrophone(mic.id ?? String(mic.index))}
							/>
						</li>
					))}
				</ul>
			</RadioGroup>
		</div>
	);
}
