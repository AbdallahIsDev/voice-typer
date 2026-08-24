// LinuxWindowButtonsSettingsSection — Settings → Appearance section
// (LINUX ONLY; returns null elsewhere). Lets the user control the
// frameless title bar's window buttons:
//   - mode "system": follow the desktop's own button-layout
//     (gsettings org.gnome.desktop.wm.preferences button-layout, read
//     once by the Python sidecar and surfaced through the read-only
//     `linux_window_buttons_system` get_config field).
//   - mode "custom": pick the side (left/right) and which of the three
//     buttons are shown.
// The whole `linux_window_buttons` object is always sent as ONE complete
// update — the server validator requires every key (SEC-002 shape
// contract in _make_linux_window_buttons_validator).

import { memo } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { IS_LINUX } from "@/components/hotkey/hotkey-utils";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n/i18n";
import { DEFAULT_LINUX_WINDOW_BUTTONS } from "@/lib/utils/windowButtons";
import type { LinuxWindowButtonsConfig } from "@/types/config";

import type { SettingsSectionSharedProps } from "./types";

const MODE_OPTIONS = [
	{ value: "system", labelKey: "settings.linuxWindowButtons.modeSystem" },
	{ value: "custom", labelKey: "settings.linuxWindowButtons.modeCustom" },
] as const satisfies ReadonlyArray<{
	value: LinuxWindowButtonsConfig["mode"];
	labelKey: string;
}>;

const SIDE_OPTIONS = [
	{ value: "left", labelKey: "settings.linuxWindowButtons.sideLeft" },
	{ value: "right", labelKey: "settings.linuxWindowButtons.sideRight" },
] as const satisfies ReadonlyArray<{
	value: LinuxWindowButtonsConfig["side"];
	labelKey: string;
}>;

export const LinuxWindowButtonsSettingsSection = memo(
	function LinuxWindowButtonsSettingsSection({
		config,
		updateConfig,
		isVisible,
	}: SettingsSectionSharedProps) {
		const t = useT();
		if (!IS_LINUX) return null;

		// Labels resolved INSIDE the body (B-REVIEW-3 pattern — t() reads
		// a module-level locale variable; import-time evaluation would
		// freeze the strings to the first locale).
		const title = t("settings.linuxWindowButtons.title");
		const description = t("settings.linuxWindowButtons.description");
		const modeLabel = t("settings.linuxWindowButtons.mode");
		const modeInfo = t("settings.linuxWindowButtons.modeInfo");
		const sideLabel = t("settings.linuxWindowButtons.side");
		const sideInfo = t("settings.linuxWindowButtons.sideInfo");
		const showMinimizeLabel = t("settings.linuxWindowButtons.showMinimize");
		const showMaximizeLabel = t("settings.linuxWindowButtons.showMaximize");
		const showCloseLabel = t("settings.linuxWindowButtons.showClose");
		const systemLayoutTemplate = t("settings.linuxWindowButtons.systemLayout");
		const systemUnavailable = t(
			"settings.linuxWindowButtons.systemUnavailable",
		);

		// Merge over the defaults: older sidecars may omit the field or
		// carry a partial object — the UI always edits a COMPLETE object.
		const current: LinuxWindowButtonsConfig = {
			...DEFAULT_LINUX_WINDOW_BUTTONS,
			...(config?.linux_window_buttons ?? {}),
		};
		const system = config?.linux_window_buttons_system ?? null;

		const commit = (patch: Partial<LinuxWindowButtonsConfig>) => {
			updateConfig({
				linux_window_buttons: { ...current, ...patch },
			});
		};

		const systemLayoutText = system?.layout
			? systemLayoutTemplate
					.replace(
						"{side}",
						t(
							system.layout.side === "left"
								? "settings.linuxWindowButtons.sideLeft"
								: "settings.linuxWindowButtons.sideRight",
						),
					)
					.replace("{buttons}", system.layout.buttons.join(", "))
			: systemUnavailable;

		return (
			<SettingsSection title={title} description={description}>
				{isVisible(modeLabel, modeInfo, title) && (
					<SettingRow label={modeLabel} info={modeInfo}>
						<Select
							value={current.mode}
							onValueChange={(value) =>
								commit({
									mode: value as LinuxWindowButtonsConfig["mode"],
								})
							}
						>
							<SelectTrigger className="w-40" aria-label={modeLabel}>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{MODE_OPTIONS.map((option) => (
									<SelectItem key={option.value} value={option.value}>
										{t(option.labelKey)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>
				)}
				{current.mode === "system" &&
					isVisible(modeLabel, systemLayoutText, title) && (
						<SettingRow label={modeLabel} info={systemLayoutText}>
							<span className="text-sm text-(--text-muted)">
								{system?.layout
									? t("settings.linuxWindowButtons.followingSystem")
									: systemUnavailable}
							</span>
						</SettingRow>
					)}
				{current.mode === "custom" && isVisible(sideLabel, sideInfo, title) && (
					<SettingRow label={sideLabel} info={sideInfo}>
						<Select
							value={current.side}
							onValueChange={(value) =>
								commit({
									side: value as LinuxWindowButtonsConfig["side"],
								})
							}
						>
							<SelectTrigger className="w-40" aria-label={sideLabel}>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{SIDE_OPTIONS.map((option) => (
									<SelectItem key={option.value} value={option.value}>
										{t(option.labelKey)}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</SettingRow>
				)}
				{current.mode === "custom" &&
					isVisible(showMinimizeLabel, undefined, title) && (
						<SettingRow label={showMinimizeLabel}>
							<Switch
								checked={current.show_minimize}
								onCheckedChange={(checked) =>
									commit({ show_minimize: checked })
								}
								aria-label={showMinimizeLabel}
							/>
						</SettingRow>
					)}
				{current.mode === "custom" &&
					isVisible(showMaximizeLabel, undefined, title) && (
						<SettingRow label={showMaximizeLabel}>
							<Switch
								checked={current.show_maximize}
								onCheckedChange={(checked) =>
									commit({ show_maximize: checked })
								}
								aria-label={showMaximizeLabel}
							/>
						</SettingRow>
					)}
				{current.mode === "custom" &&
					isVisible(showCloseLabel, undefined, title) && (
						<SettingRow label={showCloseLabel}>
							<Switch
								checked={current.show_close}
								onCheckedChange={(checked) => commit({ show_close: checked })}
								aria-label={showCloseLabel}
							/>
						</SettingRow>
					)}
			</SettingsSection>
		);
	},
);
