import type { Ref } from "react";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { formatHotkey } from "@/components/hotkey/hotkey-format";
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
import type { PermissionsTestState } from "../lib/types";

// HotkeyStepProps accepts optional test-hotkey
// props so the FINAL wizard step (Hotkey) offers an inline "Test
// hotkey" button: the user can verify their freshly-picked hotkey
// before finishing setup. Renders an inline test button + result
// message below the Select.
export interface HotkeyStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	hotkeyPresets: string[];
	selectedHotkey: string;
	setSelectedHotkey: (v: string) => void;
	/** Optional test-hotkey handler. When provided, renders a "Test
	 * hotkey" button below the Select that calls this handler. */
	onTestHotkey?: () => void;
	/** Optional test-hotkey status: the PermissionsTestState
	 * discriminated union from usePermissionsProbe. When provided,
	 * renders the corresponding localized message below the button. */
	permissionsTest?: PermissionsTestState;
}

export function HotkeyStep({
	headingRef,
	hotkeyPresets,
	selectedHotkey,
	setSelectedHotkey,
	onTestHotkey,
	permissionsTest,
}: HotkeyStepProps) {
	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.hotkeyTitle")}
			</h2>
			<p className="text-sm text-muted-foreground">
				{t("onboarding.hotkeyDescription")}
			</p>
			<Select value={selectedHotkey} onValueChange={setSelectedHotkey}>
				<SelectTrigger
					className="w-full"
					aria-label={t("onboarding.hotkeySelectAria")}
				>
					<SelectValue placeholder={t("onboarding.hotkeySelectAria")} />
				</SelectTrigger>
				<SelectContent>
					{hotkeyPresets.map((hk) => (
						<SelectItem key={hk} value={hk}>
							<HotkeyChips keys={formatHotkey(hk)} />
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			{/* Inline test-hotkey affordance: lets the user verify a
				newly-picked hotkey right here, before finishing setup. */}
			{onTestHotkey && (
				<div className="flex flex-col gap-2">
					<Button
						type="button"
						variant="outline"
						className="self-start"
						onClick={onTestHotkey}
					>
						{t("onboarding.permissionsTestButton")}
					</Button>
					{permissionsTest?.kind === "listening" && (
						<p className="text-xs text-muted-foreground">
							{t("onboarding.permissionsTestLabel")}
						</p>
					)}
					{permissionsTest?.kind === "success" && (
						<p className="text-xs text-foreground">
							{t("onboarding.permissionsTestSuccess")}
						</p>
					)}
					{permissionsTest?.kind === "failure" && (
						<p className="text-xs text-destructive">
							{t("onboarding.hotkeyTestFailure")}
						</p>
					)}
				</div>
			)}
		</>
	);
}

export default HotkeyStep;
