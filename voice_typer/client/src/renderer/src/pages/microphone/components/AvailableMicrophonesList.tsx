// Available-microphones list.
//
// Renders the "other microphones" section: the "Use System Default"
// row (PVT-034 / Fix 1 — the only way to revert from a named mic back
// to the OS default) followed by the list of microphones not currently
// selected, each rendered via ``MicrophoneListItem``.
//
// Falls back to an ``EmptyState`` (``MicOff01Icon``) when the backend
// reports zero microphones. While a test is running, the entire list
// is greyed out + click-disabled so the user can't swap mics
// mid-recording.

import { Mic02Icon, MicOff01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { EmptyState } from "@/components/feedback/EmptyState";
import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { MicrophoneDevice } from "@/types/config";

export interface AvailableMicrophonesListProps {
	/** All microphones reported by the backend (used for the empty check). */
	microphones: MicrophoneDevice[];
	/** Subset of ``microphones`` excluding the currently-active mic, sorted. */
	otherMicrophones: MicrophoneDevice[];
	/** ``true`` when the active mic is the OS default (no named selection). */
	isSystemDefault: boolean;
	/** Disables list interaction while a test recording is in flight. */
	testRunning: boolean;
	/** Selection handler — receives ``null`` for "use system default". */
	onSelectMicrophone: (micId: string | null) => void;
}

export function AvailableMicrophonesList({
	microphones,
	otherMicrophones,
	isSystemDefault,
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

	return (
		<div>
			<p className="text-xs font-semibold capitalize tracking-wide text-(--text-muted) mb-2 px-1">
				{t("microphone.otherMicrophones")}
			</p>
			<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
				{/* PVT-034 (Fix 1): "Use System Default" button — the only
				    way (other than refreshing and hoping) to revert
				    from a named microphone back to the OS default.
				    Disabled while a test is running so the user can't
				    swap mics mid-recording. */}
				<div
					className={cn(
						"flex items-center gap-3 px-3.5 py-2.5",
						testRunning && "opacity-50 pointer-events-none",
					)}
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
					<Button
						variant={isSystemDefault ? "default" : "outline"}
						size="sm"
						className="shrink-0"
						disabled={isSystemDefault || testRunning}
						aria-label={t("microphone.useSystemDefaultAria")}
						onClick={() => void onSelectMicrophone(null)}
					>
						{t("microphone.use")}
					</Button>
				</div>
				{otherMicrophones.length === 0 ? (
					<div className="px-3.5 py-3 text-xs text-(--text-muted)">
						{t("microphone.noOtherMicrophones")}
					</div>
				) : (
					otherMicrophones.map((mic) => (
						<div
							key={mic.id ?? String(mic.index)}
							className={cn(testRunning && "opacity-50 pointer-events-none")}
						>
							<MicrophoneListItem
								mic={mic}
								isSystemDefault={isSystemDefault}
								onSelect={(micId) => onSelectMicrophone(micId)}
							/>
						</div>
					))
				)}
			</div>
		</div>
	);
}
