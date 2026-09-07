// PrivacySettingsSection — Audio & Recovery + Privacy & Consent sections of
// the Settings page.
//
// Extracted from src/renderer/src/pages/Settings.tsx. Renders two
// SettingsSection blocks: "Audio & Recovery" (Crash Recovery) and
// "Privacy & Consent" (HuggingFace / Voice biometric / OpenAI / Groq /
// Deepgram / LLM polish consents, Agree-to-All banner, Export Templates
// and Config buttons). Behaviour is identical to the previous
// monolithic implementation; this section owns its own `usePython` and
// `useSnackbar` hooks (per the refactor spec) so it can issue the
// `get_templates` / `get_config` IPC calls and surface their results
// without needing the parent to forward `call` or `showSnack` as props.

import {
	CheckmarkCircle01Icon,
	InformationCircleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, type ReactNode, useState } from "react";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { SettingRow } from "@/components/common/SettingRow";
import { SettingsSection } from "@/components/common/SettingsSection";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { useT } from "@/i18n/i18n";
import type { TranslationKey } from "@/i18n/translation-keys";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import { SettingsSkeleton } from "./SettingsSkeleton";

import type { SettingsSectionSharedProps } from "./types";

/**
 * Config keys whose value type is `boolean` — the keys a Switch row can
 * read/write. Derived from `VoiceTyperConfig` so a value-type change on
 * any of these keys surfaces here at compile time. The `-?` modifier
 * strips the interface's optional markers so the indexed access yields
 * a plain literal union (homomorphic mapped types preserve `?`, which
 * would otherwise leak `undefined` into the union).
 */
type BooleanConfigKey = {
	[K in keyof VoiceTyperConfig]-?: VoiceTyperConfig[K] extends boolean
		? K
		: never;
}[keyof VoiceTyperConfig];

/**
 * One row of the consent/privacy switch matrix. The descriptor IS the
 * render spec (the audio-filter row registry is the in-repo precedent):
 * the section-level search-visibility arrays, the per-row visibility
 * gating, the rendered SettingRow+Switch pair, and the Agree-to-All
 * update payload are all derived from this single list — adding consent
 * #7 is a one-entry change here, not ~7 coordinated edits across the
 * file.
 */
export interface ConsentFieldDescriptor {
	/** Which Settings section renders the row. */
	section: "audioRecovery" | "privacy";
	/** Config key the Switch reads/writes (boolean-valued). */
	configKey: BooleanConfigKey;
	/** i18n key for the row's visible label. */
	labelKey: TranslationKey;
	/** i18n key for the SettingRow info tooltip. */
	infoKey: TranslationKey;
	/**
	 * i18n key for the search-visible info — the section-level
	 * "any row visible?" arrays read this variant. Equals `infoKey`
	 * for rows that have no dedicated `*InfoSearch` key.
	 */
	infoSearchKey: TranslationKey;
	/** i18n key for the Switch's aria-label. */
	ariaKey: TranslationKey;
	/** Fallback when the runtime config value is undefined. */
	defaultValue: boolean;
	/**
	 * Renders inside the highlighted ConsentRow wrapper (which carries
	 * the `data-consent-field` scroll target + deep-link focus ring).
	 * The six GDPR consent flags use it; the hidden-config rows render
	 * as bare SettingRows, exactly as before.
	 */
	consentRow?: boolean;
	/**
	 * Participates in Agree-to-All (the granted payload), the granted
	 * count, and the button's disabled state.
	 */
	agreeToAll?: boolean;
	/** Stable Switch `data-testid` (hidden-config rows carry one). */
	testId?: string;
}

/**
 * The consent/privacy switch matrix — order is render order within each
 * section. i18n keys are unchanged from the hand-written rows this
 * registry replaces (pure refactor; no locale edits).
 */
export const CONSENT_FIELDS: readonly ConsentFieldDescriptor[] = [
	// Audio & Recovery section (single row).
	{
		section: "audioRecovery",
		configKey: "crash_recovery_enabled",
		labelKey: "settings.privacy.crashRecovery",
		infoKey: "settings.privacy.crashRecoveryInfo",
		infoSearchKey: "settings.privacy.crashRecoveryInfoSearch",
		ariaKey: "settings.privacy.crashRecoveryAria",
		defaultValue: true,
	},
	// Privacy & Consent section — the six GDPR consent flags. All
	// four consent flags live in the Python Config and are enforced
	// by the backend (HuggingFace download refusal, CloudEngine
	// ConsentRequiredError, etc.).  This section gives the user a
	// single place to view and revoke any consent they've
	// previously granted.  Initial grant happens contextually
	// (HuggingFace banner on Models page, per-provider toggles on
	// Models page) — this section is primarily for
	// review/revocation.
	{
		section: "privacy",
		configKey: "huggingface_consent",
		labelKey: "settings.privacy.huggingFaceDownloadsLabel",
		infoKey: "settings.privacy.huggingFaceDownloadsInfo",
		infoSearchKey: "settings.privacy.huggingFaceDownloadsInfoSearch",
		ariaKey: "settings.privacy.huggingFaceDownloadsAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	{
		section: "privacy",
		configKey: "voice_biometric_consent",
		labelKey: "settings.privacy.voiceBiometricLabel",
		infoKey: "settings.privacy.voiceBiometricProcessingInfo",
		infoSearchKey: "settings.privacy.voiceBiometricInfoSearch",
		ariaKey: "settings.privacy.voiceBiometricProcessingAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	// Per-provider cloud ASR consents — mirror the Models page toggles.
	{
		section: "privacy",
		configKey: "cloud_openai_consent",
		labelKey: "settings.privacy.openaiCloudAsrLabel",
		infoKey: "settings.privacy.openaiCloudAsrInfo",
		infoSearchKey: "settings.privacy.openaiCloudAsrInfoSearch",
		ariaKey: "settings.privacy.openaiCloudAsrAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	{
		section: "privacy",
		configKey: "cloud_groq_consent",
		labelKey: "settings.privacy.groqCloudAsrLabel",
		infoKey: "settings.privacy.groqCloudAsrInfo",
		infoSearchKey: "settings.privacy.groqCloudAsrInfoSearch",
		ariaKey: "settings.privacy.groqCloudAsrAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	{
		section: "privacy",
		configKey: "cloud_deepgram_consent",
		labelKey: "settings.privacy.deepgramCloudAsrLabel",
		infoKey: "settings.privacy.deepgramCloudAsrInfo",
		infoSearchKey: "settings.privacy.deepgramCloudAsrInfoSearch",
		ariaKey: "settings.privacy.deepgramCloudAsrAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	// LLM polish consent (existing field, surfaced here for completeness).
	{
		section: "privacy",
		configKey: "llm_polish_consent",
		labelKey: "settings.privacy.llmTextPolishingLabel",
		infoKey: "settings.privacy.llmTextPolishingInfo",
		infoSearchKey: "settings.privacy.llmTextPolishingInfoSearch",
		ariaKey: "settings.privacy.llmTextPolishingAria",
		defaultValue: false,
		consentRow: true,
		agreeToAll: true,
	},
	// Hidden-config rows (previously config.json-only fields, now
	// user-tunable): transcription logging and the clipboard
	// borrow/restore behavior (ADR-0010). Both are privacy-relevant
	// (transcription text leaving traces; the app reading clipboard
	// contents), so they live in the Privacy & Consent section.
	{
		section: "privacy",
		configKey: "log_transcriptions",
		labelKey: "settings.privacy.logTranscriptionsLabel",
		// No dedicated *InfoSearch key — the tooltip text doubles
		// as the search-visible info, exactly as before.
		infoKey: "settings.privacy.logTranscriptionsInfo",
		infoSearchKey: "settings.privacy.logTranscriptionsInfo",
		ariaKey: "settings.privacy.logTranscriptionsAria",
		defaultValue: false,
		testId: "log-transcriptions-switch",
	},
	{
		section: "privacy",
		configKey: "clipboard_save_restore",
		labelKey: "settings.privacy.clipboardSaveRestoreLabel",
		infoKey: "settings.privacy.clipboardSaveRestoreInfo",
		infoSearchKey: "settings.privacy.clipboardSaveRestoreInfo",
		ariaKey: "settings.privacy.clipboardSaveRestoreAria",
		defaultValue: true,
		testId: "clipboard-save-restore-switch",
	},
];

/** The Agree-to-All subset — the six flags the banner grants at once. */
const AGREE_TO_ALL_FIELDS = CONSENT_FIELDS.filter((field) => field.agreeToAll);

/**
 * Module-level consent-row wrapper (stable identity). Carries the
 * ``data-consent-field`` attribute that Settings.tsx's deep-link scroll
 * targets, and renders the temporary highlight ring when ``highlighted``.
 *
 * MUST stay at module scope: an inline component would get a fresh
 * function identity on every section re-render, which React treats as a
 * changed element type → unmount/remount of the whole row subtree on
 * each render — losing focus on a just-clicked Switch and resetting
 * child-local state.
 */
function ConsentRow({
	field,
	highlighted,
	children,
}: {
	field: string;
	highlighted: boolean;
	children: ReactNode;
}) {
	return (
		<div
			data-consent-field={field}
			className={cn(
				"rounded-lg transition-shadow duration-500",
				highlighted && "ring-2 ring-primary bg-(--bg-subtle)",
			)}
		>
			{children}
		</div>
	);
}

export const PrivacySettingsSection = memo(function PrivacySettingsSection({
	config,
	updateConfig,
	isVisible,
	consentFocusField,
}: SettingsSectionSharedProps & {
	/**
	 * Consent deep-link target (e.g. ``"voice_biometric_consent"``).
	 * When set, the matching consent row renders a temporary highlight
	 * ring so the user lands visually on the exact toggle the
	 * ``client.consent_required`` refusal named. Rendered by
	 * Settings.tsx from the navigate ``{ consentField }`` option;
	 * cleared after a short timeout. ``data-consent-field``
	 * attributes double as Settings.tsx's scroll target.
	 */
	consentFocusField?: string | null;
}) {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const t = useT();

	//confirmation dialog state for "Agree to All" — granting all
	// 6 consents at once is a significant privacy decision (enables cloud
	// transcription, LLM polishing, HuggingFace downloads, voice-biometric
	// processing) so we surface a destructive-variant ConfirmDialog before
	// actually persisting the change. The user can still revoke individual
	// consents via the toggles below.
	const [showAgreeConfirm, setShowAgreeConfirm] = useState(false);

	if (!config) return <SettingsSkeleton rows={3} />;

	// One handler factory replaces the nine verbatim per-field
	// `(checked) => updateConfig({ key: checked })` closures.
	// `configKey` is a non-literal union, so the computed key widens
	// to a string index signature which `Partial<VoiceTyperConfig>`
	// (whose keys hold strings/numbers/…) rejects — the cast is the
	// documented registry-path exception (same as the audio-filter
	// row registry's `set` helper).
	const makeConsentChangeHandler =
		(configKey: BooleanConfigKey) => (checked: boolean) =>
			updateConfig({ [configKey]: checked } as Partial<VoiceTyperConfig>);

	//opening the ConfirmDialog instead of granting all 6 consents
	// immediately. The actual updateConfig call happens in
	// `handleConfirmAgreeToAll` (below) once the user confirms.
	const handleAgreeToAll = () => {
		setShowAgreeConfirm(true);
	};
	// The granted payload is derived from the SAME descriptor subset
	// that drives the granted count and the button's disabled state,
	// so the three can never drift apart.
	const handleConfirmAgreeToAll = () => {
		setShowAgreeConfirm(false);
		updateConfig(
			Object.fromEntries(
				AGREE_TO_ALL_FIELDS.map((field) => [field.configKey, true]),
			) as Partial<VoiceTyperConfig>,
		);
	};

	// IMPL-C: resolve the translated labels once per render so the
	// section-level isVisible check and the rendered SettingRow labels
	// share the same strings (one t() pass per key, like the label
	// locals this map replaces).
	const consentRows = CONSENT_FIELDS.map((field) => ({
		field,
		label: t(field.labelKey),
		info: t(field.infoKey),
		infoSearch: t(field.infoSearchKey),
	}));

	const exportAllDataLabel = t("settings.privacy.exportAllDataLabel");
	const exportAllDataInfoSearch = t("settings.privacy.exportAllDataInfoSearch");

	//section-level visibility check for Audio & Recovery section.
	const audioRecoveryTitle = t("settings.privacy.audioRecoveryTitle");
	const audioRecoveryItems = consentRows
		.filter((row) => row.field.section === "audioRecovery")
		.map((row) => ({ label: row.label, info: row.infoSearch }));
	const audioRecoveryVisible = audioRecoveryItems.some((item) =>
		isVisible(item.label, item.info, audioRecoveryTitle),
	);

	//section-level visibility check for Privacy & Consent section.
	//The export row participates in the search (it renders inside
	// this section) but is not a Switch row — its entry is appended
	// to the switch-row descriptors' entries.
	const privacyTitle = t("settings.privacy.privacyTitle");
	const privacyItems = [
		...consentRows
			.filter((row) => row.field.section === "privacy")
			.map((row) => ({ label: row.label, info: row.infoSearch })),
		{ label: exportAllDataLabel, info: exportAllDataInfoSearch },
	];
	const privacyVisible = privacyItems.some((item) =>
		isVisible(item.label, item.info, privacyTitle),
	);

	// Agree-to-All banner state — derived from the same descriptor
	// subset as the granted payload (see handleConfirmAgreeToAll).
	const grantedConsentCount = AGREE_TO_ALL_FIELDS.filter(
		(field) => config[field.configKey],
	).length;
	const allConsentsGranted = AGREE_TO_ALL_FIELDS.every(
		(field) => config[field.configKey],
	);

	return (
		<>
			{/* ── SECTION: Audio & Recovery ─────────────────────────── */}
			{audioRecoveryVisible && (
				<SettingsSection
					title={audioRecoveryTitle}
					description={t("settings.privacy.audioRecoveryDescription")}
				>
					{consentRows
						.filter((row) => row.field.section === "audioRecovery")
						.map(({ field, label }) => (
							<SettingRow
								key={field.configKey}
								label={label}
								info={t(field.infoKey)}
							>
								{" "}
								<Switch
									checked={config[field.configKey] ?? field.defaultValue}
									onCheckedChange={makeConsentChangeHandler(field.configKey)}
									aria-label={t(field.ariaKey)}
								/>
							</SettingRow>
						))}
				</SettingsSection>
			)}

			{/* ── SECTION: Privacy & Consent ─────────────────────────── */}
			{privacyVisible && (
				<>
					{/*006/009: centralized consent management —
                                        see the CONSENT_FIELDS registry comments for
                                        the enforcement/revocation rationale.

                                        PRIV-AGREE-ALL (fix-quit-and-privacy): an "Agree to All"
                                        affordance at the top lets the user enable every consent
                                        flag at once without clicking six toggles.  Defaults stay
                                        False (privacy-by-default); the button is purely a UX
                                        convenience, not an implicit grant.  Individual toggles
                                        below remain for granular control / revocation. */}
					<SettingsSection
						title={privacyTitle}
						description={t("settings.privacy.privacyDescription")}
					>
						{/* PRIV-AGREE-ALL: header banner + Agree to All button.
                                        Explains what "agreeing" means in plain language so
                                        the user can make an informed decision before
                                        clicking.  The banner sits inside the same
                                        bordered container as the toggles (visually grouped
                                        with them) but uses a slightly different background
                                        to distinguish it from per-flag rows. */}
						<div className="flex flex-col gap-3 p-4">
							<div className="flex items-start gap-2">
								<HugeiconsIcon
									icon={InformationCircleIcon}
									strokeWidth={2}
									className="h-4 w-4 mt-0.5 shrink-0 text-(--text-muted)"
								/>
								<div className="flex min-w-0 flex-col gap-2 text-sm text-(--text-muted)">
									<p>{t("settings.privacy.consentBannerDesc")}</p>
									<ul className="list-disc ps-4 flex flex-col gap-0.5 text-xs">
										<li>{t("settings.privacy.huggingFaceItem")}</li>
										<li>{t("settings.privacy.cloudAsrItem")}</li>
										<li>{t("settings.privacy.llmPolishItem")}</li>
										<li>{t("settings.privacy.voiceBiometricItem")}</li>
									</ul>
									<p className="text-xs">
										{t("settings.privacy.revokeNotice")}
									</p>
								</div>
							</div>
							<div className="flex items-center justify-between gap-3">
								<div className="text-xs text-(--text-muted)">
									{t("settings.privacy.consentsGranted", {
										granted: String(grantedConsentCount),
									})}
								</div>
								<Button
									variant="default"
									size="sm"
									className="gap-2"
									onClick={handleAgreeToAll}
									disabled={allConsentsGranted}
									aria-label={t("settings.privacy.agreeToAllAria")}
									title={t("settings.privacy.agreeToAllHint")}
								>
									<HugeiconsIcon
										icon={CheckmarkCircle01Icon}
										strokeWidth={2}
										className="h-4 w-4"
									/>
									{t("settings.privacy.agreeToAll")}
								</Button>
							</div>
						</div>

						{consentRows
							.filter((row) => row.field.section === "privacy")
							.map(({ field, label, info }) =>
								isVisible(label, info, privacyTitle) ? (
									field.consentRow ? (
										<ConsentRow
											key={field.configKey}
											field={field.configKey}
											highlighted={consentFocusField === field.configKey}
										>
											<SettingRow label={label} info={info}>
												<Switch
													checked={
														config[field.configKey] ?? field.defaultValue
													}
													onCheckedChange={makeConsentChangeHandler(
														field.configKey,
													)}
													aria-label={t(field.ariaKey)}
												/>
											</SettingRow>
										</ConsentRow>
									) : (
										<SettingRow key={field.configKey} label={label} info={info}>
											<Switch
												checked={config[field.configKey] ?? field.defaultValue}
												onCheckedChange={makeConsentChangeHandler(
													field.configKey,
												)}
												aria-label={t(field.ariaKey)}
												data-testid={field.testId}
											/>
										</SettingRow>
									)
								) : null,
							)}

						{/*GDPR right-to-export (Art. 15/20).
                                        Previously only history + vocabulary were exportable.
                                        Templates and config are also user data and must be
                                        exportable on request.  The handlers live in
                                        main/index.ts (templates:export, config:export) and
                                        are exposed via the preload bridge. */}
						<SettingRow
							label={exportAllDataLabel}
							info={t("settings.privacy.exportAllDataInfo")}
						>
							<div className="flex gap-2">
								<Button
									variant="outline"
									size="sm"
									onClick={async () => {
										try {
											const templates = await call("get_templates");
											const result = await (
												window.window_ as {
													exportTemplates?: (data: unknown) => Promise<{
														success: boolean;
														path?: string;
														error?: string;
													}>;
												}
											).exportTemplates?.(templates);
											if (result?.success) {
												showSnack(
													t("settings.privacy.templatesExported", {
														filename:
															result.path?.split(/[\\/]/).pop() ??
															t("settings.privacy.fileFallback"),
													}),
													"success",
												);
											} else if (result?.error) {
												showSnack(
													t("settings.privacy.exportFailedError", {
														error: result.error,
													}),
													"error",
												);
											}
										} catch (err) {
											showSnack(
												t("settings.privacy.exportFailedError", {
													error:
														err instanceof Error ? err.message : String(err),
												}),
												"error",
											);
										}
									}}
									aria-label={t("settings.privacy.exportTemplatesAria")}
								>
									{t("settings.privacy.exportTemplates")}
								</Button>
								<Button
									variant="outline"
									size="sm"
									onClick={async () => {
										try {
											const cfg = await call("get_config");
											const result = await (
												window.window_ as {
													exportConfig?: (data: unknown) => Promise<{
														success: boolean;
														path?: string;
														error?: string;
													}>;
												}
											).exportConfig?.(cfg);
											if (result?.success) {
												showSnack(
													t("settings.privacy.configExported", {
														filename:
															result.path?.split(/[\\/]/).pop() ??
															t("settings.privacy.fileFallback"),
													}),
													"success",
												);
											} else if (result?.error) {
												showSnack(
													t("settings.privacy.exportFailedError", {
														error: result.error,
													}),
													"error",
												);
											}
										} catch (err) {
											showSnack(
												t("settings.privacy.exportFailedError", {
													error:
														err instanceof Error ? err.message : String(err),
												}),
												"error",
											);
										}
									}}
									aria-label={t("settings.privacy.exportConfigAria")}
								>
									{t("settings.privacy.exportConfig")}
								</Button>
							</div>
						</SettingRow>
					</SettingsSection>
				</>
			)}

			{/*confirmation dialog for "Agree to All". Discloses the
                                scope of the action (which 6 consents will be granted and what
                                each enables) so the user can make an informed decision before
                                clicking. Uses variant="destructive" because granting all cloud
                                + biometric consents at once is a non-reversible-at-runtime
                                privacy action (revocation requires toggling each one off).
                                Title and message are localised via t("settings.privacy.agreeConfirm*")
                                — native translations exist in all 8 locale JSON files. */}
			<ConfirmDialog
				open={showAgreeConfirm}
				title={t("settings.privacy.agreeConfirmTitle")}
				message={t("settings.privacy.agreeConfirmMessage")}
				confirmLabel={t("settings.privacy.agreeToAll")}
				cancelLabel={t("common.cancel")}
				variant="destructive"
				onConfirm={handleConfirmAgreeToAll}
				onCancel={() => setShowAgreeConfirm(false)}
			/>
		</>
	);
});
