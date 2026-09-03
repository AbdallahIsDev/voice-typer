// OverlaySettingsSection — the Overlay section of the Settings surface.
//
// Extracted from the former GeneralSettingsSection (which stacked the
// General and Overlay cards on one page) so the Overlay domain gets its
// own focused section page (settingsOverlay). Renders one SettingsSection
// block: "Overlay" (Bubble Behavior, Bubble Position, Show on App Startup,
// Drag to Move, Bubble Mic Button). Behaviour is identical to the previous
// combined implementation, including the per-row search-filter visibility
// via the `isVisible` prop and the section-level "hide if no items match"
// check.

import { memo } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n/i18n";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

const BUBBLE_BEHAVIOR_OPTIONS = [
	{ value: "always_visible", labelKey: "settings.bubbleBehaviorAlwaysVisible" },
	{ value: "show_on_record", labelKey: "settings.bubbleBehaviorShowOnRecord" },
] as const;

export const OverlaySettingsSection = memo(function OverlaySettingsSection({
	config,
	updateConfig,
	isVisible,
}: SettingsSectionSharedProps) {
	// F-3: subscribe to locale changes so this section repaints in the
	// new language without a full page reload.
	const t = useT();

	if (!config) return <SettingsSkeleton rows={3} />;

	//section-level visibility check for the Overlay section. The title
	// constant feeds BOTH the `<SettingsSection title>` prop AND the
	// `isVisible` third parameter, so search matches the heading the
	// user actually sees.
	const overlaySectionTitle = t("settings.overlay");
	const overlayItems = [
		{
			label: t("settings.bubbleBehaviorLabel"),
			info: t("settings.bubbleBehaviorInfo"),
		},
		{
			label: t("settings.bubblePositionLabel"),
			info: t("settings.bubblePositionInfo"),
		},
		{
			label: t("settings.showOnAppStartup"),
			info: t("settings.showOnAppStartupInfo"),
		},
		{
			label: t("settings.dragToMove"),
			info: t("settings.dragToMoveInfo"),
		},
		{
			label: t("settings.bubbleMicButton"),
			info: t("settings.bubbleMicButtonDescription"),
		},
	];
	const overlayVisible = overlayItems.some((item) =>
		isVisible(item.label, item.info, overlaySectionTitle),
	);

	// ── Inline handler extraction ─────────────────────────────────
	const handleBubbleBehaviorChange = (v: string) =>
		updateConfig({
			bubble_behavior: v as "show_on_record" | "always_visible",
		});
	const handleBubblePositionChange = (v: string) => {
		updateConfig({ bubble_position: v as "top" | "bottom" });
		window.bubble?.setPosition?.(v as "top" | "bottom");
	};
	const handleBubbleStartupChange = (checked: boolean) =>
		updateConfig({ bubble_show_on_startup: checked });
	const handleDragToMoveChange = (checked: boolean) => {
		updateConfig({ bubble_draggable: checked });
		window.bubble?.setDraggable?.(checked);
	};
	//mic button toggle — only meaningful in always_visible mode.
	const handleBubbleMicButtonChange = (checked: boolean) =>
		updateConfig({ bubble_mic_button: checked });

	if (!overlayVisible) return null;

	return (
		<SettingsSection
			title={overlaySectionTitle}
			description={t("settings.overlayDescription")}
		>
			{/* ── Dropdowns ──────────────────────────────────────── */}
			<SettingRow
				label={t("settings.bubbleBehaviorLabel")}
				info={t("settings.bubbleBehaviorInfo")}
			>
				<SegmentedControl
					options={BUBBLE_BEHAVIOR_OPTIONS.map((opt) => ({
						value: opt.value,
						label: t(opt.labelKey),
					}))}
					value={config.bubble_behavior ?? "show_on_record"}
					onChange={handleBubbleBehaviorChange}
					ariaLabel={t("settings.bubbleBehaviorLabel")}
				/>
			</SettingRow>

			<SettingRow
				label={t("settings.bubblePositionLabel")}
				info={t("settings.bubblePositionInfo")}
			>
				<SegmentedControl
					options={[
						{ value: "top", label: t("settings.bubblePositionTop") },
						{ value: "bottom", label: t("settings.bubblePositionBottom") },
					]}
					value={config.bubble_position ?? "bottom"}
					onChange={handleBubblePositionChange}
					ariaLabel={t("settings.bubblePositionLabel")}
				/>
			</SettingRow>

			{/* ── Switches ───────────────────────────────────────── */}
			{/* Show on app startup toggle — only visible when Always Visible is selected */}
			{config.bubble_behavior === "always_visible" && (
				<SettingRow
					label={t("settings.showOnAppStartup")}
					info={t("settings.showOnAppStartupInfo")}
				>
					<Switch
						checked={config.bubble_show_on_startup ?? true}
						onCheckedChange={handleBubbleStartupChange}
						aria-label={t("settings.showOnAppStartup")}
					/>
				</SettingRow>
			)}

			{/*mic button toggle — only visible when Always Visible is
                selected. Lets the user disable the clickable mic button
                (reverting the bubble to non-interactive). */}
			{config.bubble_behavior === "always_visible" && (
				<SettingRow
					label={t("settings.bubbleMicButton")}
					info={t("settings.bubbleMicButtonDescription")}
				>
					<Switch
						checked={config.bubble_mic_button ?? true}
						onCheckedChange={handleBubbleMicButtonChange}
						aria-label={t("settings.bubbleMicButton")}
					/>
				</SettingRow>
			)}

			<SettingRow
				label={t("settings.dragToMove")}
				info={t("settings.dragToMoveInfo")}
			>
				<Switch
					checked={config.bubble_draggable ?? true}
					onCheckedChange={handleDragToMoveChange}
					aria-label={t("settings.dragToMove")}
				/>
			</SettingRow>
		</SettingsSection>
	);
});
