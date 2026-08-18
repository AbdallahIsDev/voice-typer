import type { Ref } from "react";
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

// HotkeyStepProps now accepts optional test-hotkey
// props. The wizard previously only offered a "Test hotkey" button on
// the Permissions step (step 3, with the default hotkey) — so the user
// picked a non-default hotkey on step 4 (Hotkey) with no inline way to
// verify it works. Now HotkeyStep accepts the same onTestHotkey handler
// + permissionsTest state the PermissionsStep uses, and renders an
// inline test button + result message below the Select.
export interface HotkeyStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	hotkeyPresets: string[];
	selectedHotkey: string;
	setSelectedHotkey: (v: string) => void;
	/** Optional test-hotkey handler. When provided, renders a "Test
	 * hotkey" button below the Select that calls this handler. The
	 * parent (Onboarding.tsx) passes through the same handleTestHotkey
	 * used by PermissionsStep. */
	onTestHotkey?: () => void;
	/** Optional test-hotkey status: the same PermissionsTestState
	 * discriminated union used by PermissionsStep. When provided,
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
			<p className="mb-4 text-sm text-(--text-muted)">
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
							{formatHotkey(hk)}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			{/* : inline test-hotkey affordance.
				Mirrors the test button + status text pattern from
				PermissionsStep so the user can verify a newly-picked
				hotkey without navigating back to the Permissions step. */}
			{onTestHotkey && (
				<div className="mt-4 flex flex-col gap-2">
					<Button
						type="button"
						variant="outline"
						className="self-start"
						onClick={onTestHotkey}
					>
						{t("onboarding.permissionsTestButton")}
					</Button>
					{permissionsTest?.kind === "listening" && (
						<p className="text-xs text-(--text-muted)">
							{t("onboarding.permissionsTestLabel")}
						</p>
					)}
					{permissionsTest?.kind === "success" && (
						<p className="text-xs text-(--text-primary)">
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
