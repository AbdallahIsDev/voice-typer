import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { Ref } from "react";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { t } from "@/i18n/i18n";
import { HEADING_CLASS } from "../lib/constants";
import type { MicrophoneOption } from "../lib/types";

export interface MicrophoneStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	microphones: MicrophoneOption[];
	selectedMic: string;
	setSelectedMic: (v: string) => void;
	/** Optional refresh callback. When provided, the no-mics
	 * branch shows a Refresh button + hint instead of a dead-end
	 * message. The parent (Onboarding.tsx via useOnboardingWizard)
	 * can wire this to re-fetch the microphone list. */
	onRefreshMics?: () => void;
}

export function MicrophoneStep({
	headingRef,
	microphones,
	selectedMic,
	setSelectedMic,
	onRefreshMics,
}: MicrophoneStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.micTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.micDescription")}
			</p>
			{microphones.length > 0 ? (
				<Select value={selectedMic} onValueChange={setSelectedMic}>
					<SelectTrigger
						className="w-full"
						aria-label={t("onboarding.micSelectAria")}
					>
						<SelectValue placeholder={t("onboarding.micSelectAria")} />
					</SelectTrigger>
					<SelectContent>
						{microphones.map((mic) => {
							// S2-CR-39: surface two backend-provided
							// flags the renderer previously ignored.
							// `default === true` is the OS default
							// input device — a "Default" badge
							// helps the user understand why this
							// option is pre-selected. `is_bluetooth
							// === true` indicates a Bluetooth/HFP
							// device (8 kHz sample rate) — a "BT"
							// badge warns the user that audio
							// quality will be limited. The "BT"
							// literal is intentionally untranslated
							// (Bluetooth is a registered trademark
							// used as a universal proper noun across
							// all locales).
							const showDefaultBadge = mic.default === true;
							const showBluetoothBadge =
								mic.is_bluetooth === true;
							return (
								<SelectItem key={mic.id} value={mic.id}>
									<span className="flex items-center gap-2">
										<span>{mic.name}</span>
										{showDefaultBadge && (
											<span
												className="rounded-full border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent"
												data-testid={`mic-default-badge-${mic.id}`}
											>
												{t("onboarding.defaultMic")}
											</span>
										)}
										{showBluetoothBadge && (
											<span
												className="rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:text-amber-400"
												data-testid={`mic-bluetooth-badge-${mic.id}`}
												title="Bluetooth/HFP — audio quality may be limited"
											>
												BT
											</span>
										)}
									</span>
								</SelectItem>
							);
						})}
					</SelectContent>
				</Select>
			) : (
				// the no-mics branch was previously a
				// dead end — just a passive message with no recovery
				// affordance. Now it shows the message PLUS a hint about
				// OS mic permission and (when the parent provides
				// onRefreshMics) a Refresh button so the user can re-scan
				// after plugging in a mic or granting permission.
				<div className="flex flex-col gap-3">
					<p className="text-sm text-(--text-muted)">
						{t("onboarding.noMics")}
					</p>
					<p className="text-xs text-(--text-muted)">
						{t("onboarding.noMicsHint")}
					</p>
					{onRefreshMics && (
						<Button
							type="button"
							variant="outline"
							className="self-start gap-2"
							onClick={onRefreshMics}
						>
							<HugeiconsIcon
								icon={RefreshIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{t("onboarding.refreshMics")}
						</Button>
					)}
				</div>
			)}
		</>
	);
}

export default MicrophoneStep;
