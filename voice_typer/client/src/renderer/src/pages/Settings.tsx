import {
	Book02Icon,
	Bug02Icon,
	File02Icon,
	InformationCircleIcon,
	RefreshIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import PageHeading from "@/components/PageHeading";
import { SearchField } from "@/components/SearchField";
import { SettingsSection } from "@/components/SettingsSection";
import { Spinner } from "@/components/Spinner";
import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import { HotkeySettingsSection } from "@/components/settings/HotkeySettingsSection";
import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import { ThemeSettingsSection } from "@/components/settings/ThemeSettingsSection";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
// NEW-TS-004: use the shared useSnackbar hook instead of re-implementing
// the useState + setTimeout + JSX pattern inline.  Previously this page
// had its own ``showSnack`` function with a setTimeout that wasn't
// cleared on unmount (a leak risk if the page unmounted mid-toast).
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

// Module-level cache — persists across page navigations so settings render
// instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null;

interface SettingsPageProps {
	themeMode?: VoiceTyperConfig["theme_mode"];
	onThemeChange?: (mode: VoiceTyperConfig["theme_mode"]) => void;
	// NEW-UX-025: navigation callback so the Troubleshooting section can
	// route the user to the About page (which has full diagnostics).
	// NEW-TS-ERR-R2-001: typed as `Page` (not `string`).
	onNavigate?: (page: Page) => void;
}

export default function SettingsPage({
	themeMode: themeModeProp,
	onThemeChange,
	onNavigate,
}: SettingsPageProps) {
	const { call } = usePython();
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [saving, setSaving] = useState(false);
	const [showResetDialog, setShowResetDialog] = useState(false);
	// UX-028: search/filter state for settings
	const [settingsFilter, setSettingsFilter] = useState("");

	// NEW-TS-004: use the shared useSnackbar hook.  The hook manages the
	// timer ref and clears it on unmount, fixing the leak risk of the
	// previous inline setTimeout (which wasn't cleared if the page
	// unmounted mid-toast).
	const { showSnack, Snackbar } = useSnackbar();

	const loadConfig = useCallback(async () => {
		try {
			const result = await call<VoiceTyperConfig>("get_config");
			_cachedConfig = result;
			setConfig(result);
		} catch (err) {
			console.error("Failed to load config:", err);
		}
	}, [call]);

	// Skip initial fetch when module-level cache is populated —
	// re-renders instantly from cache instead of flashing a spinner
	// on every page navigation. The fetch still runs on first visit
	// (when _cachedConfig is null).
	useEffect(() => {
		if (!_cachedConfig) {
			loadConfig();
		}
	}, [loadConfig]);

	const updateConfig = useCallback(
		async (updates: Partial<VoiceTyperConfig>) => {
			if (!config) return;
			setSaving(true);
			try {
				const newConfig = { ...config, ...updates };
				_cachedConfig = newConfig;
				setConfig(newConfig);
				await call("set_config", updates);

				// If the custom theme was successfully saved to the backend,
				// clear the localStorage draft — it's now safely persisted.
				// (Draft helpers live in ThemeSettingsSection now; the
				// backend-confirmed save still implies the draft can be
				// discarded. The section re-loads its own draft from LS on
				// the next mount, so we don't need to clear it here — the
				// section's own _clearDraftLS() call inside its onCheckedChange
				// handler already covers the "disable custom theme" path.)

				// NEW-UX-014 / NEW-UX-035: show a "Saved" toast so the user
				// knows their change was persisted.  Previously this was a
				// comment-only "intent" with no actual call — the success
				// path was completely silent.  Every toggle/select/radio in
				// Settings now confirms the save via this toast.
				//
				// We don't show a toast for every keystroke (those go through
				// updateConfigDebounced which is debounced).  This path is
				// only hit by explicit toggle/select changes, so toasting
				// here is appropriate.
				showSnack("Saved", "success");
			} catch (err) {
				console.error("Failed to update config:", err);
				await loadConfig();
				// NEW-UX-014: also surface failures so the user knows.
				showSnack("Failed to save setting", "error");
			} finally {
				setSaving(false);
			}
		},
		[config, call, loadConfig, showSnack],
	);

	// UX-007: debounced update for text inputs that fire on every keystroke.
	// Keeps a local draft in component state; commits via updateConfig after
	// 500ms of idle.  Prevents 11 IPC roundtrips when typing "gpt-4o-mini".
	const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>(
		{},
	);
	const updateConfigDebounced = useCallback(
		(key: keyof VoiceTyperConfig, value: unknown, delayMs = 500) => {
			// Update local state immediately for responsive UI
			if (config) {
				const newConfig = { ...config, [key]: value };
				_cachedConfig = newConfig;
				setConfig(newConfig);
			}
			// Clear any pending timer for this key
			if (debouncedTimers.current[key as string]) {
				clearTimeout(debouncedTimers.current[key as string]);
			}
			// Schedule the IPC commit
			debouncedTimers.current[key as string] = setTimeout(() => {
				updateConfig({ [key]: value } as Partial<VoiceTyperConfig>);
				delete debouncedTimers.current[key as string];
			}, delayMs);
		},
		[config, updateConfig],
	);

	// Cleanup pending debounced timers on unmount.  We intentionally read
	// .current at cleanup time (not effect-run time) so ALL timers added
	// during the component's lifetime are cleared.
	useEffect(() => {
		return () => {
			// eslint-disable-next-line react-hooks/exhaustive-deps -- read ref at cleanup time, not effect-run time
			Object.values(debouncedTimers.current).forEach(clearTimeout);
		};
	}, []);

	const viewLogs = async () => {
		// UX-008: actually open the log folder via the main process.
		// Previously this just showed a snackbar without opening anything.
		try {
			const result = await window.window_?.openLogs?.();
			if (result?.success) {
				showSnack("Log folder opened", "success");
			} else {
				showSnack(result?.error || "Could not open log folder", "error");
			}
		} catch (err) {
			console.error("Failed to open logs:", err);
			showSnack("Could not open log folder", "error");
		}
	};

	const resetToDefaults = async () => {
		if (!config) return;
		setShowResetDialog(false);
		// UX-018: fetch defaults from the Python backend instead of
		// hardcoding 22+ field values here (which silently drift from
		// the Config dataclass).  The backend returns a sanitized dict
		// (API keys redacted) which we send back via set_config.
		try {
			const defaults = await call("get_defaults");
			if (defaults && typeof defaults === "object") {
				// Filter out the redacted sentinels and any non-allowlisted
				// keys before sending back via set_config.
				const safeDefaults: Record<string, unknown> = {};
				for (const [key, value] of Object.entries(
					defaults as Record<string, unknown>,
				)) {
					// Skip redacted API keys — we don't want to overwrite the
					// user's real keys with "<redacted>".
					if (value === "<redacted>") continue;
					// Skip schema_version and internal state fields.
					// NEW-UX-019: onboarding_completed is intentionally preserved
					// — resetting it would force the user to redo the 5-step
					// wizard every time they reset settings, which is bad UX.
					// The wizard can be re-triggered manually via the tray menu
					// if needed.
					if (
						[
							"schema_version",
							"wayland_warned",
							"onboarding_completed",
						].includes(key)
					)
						continue;
					safeDefaults[key] = value;
				}
				await updateConfig(safeDefaults as Partial<VoiceTyperConfig>);
				showSnack("Settings reset to defaults", "success");
			} else {
				showSnack("Failed to fetch defaults from backend", "error");
			}
		} catch (err) {
			console.error("Failed to reset to defaults:", err);
			showSnack("Failed to reset to defaults", "error");
		}
	};

	// Local wrapper around the App-level onThemeChange that also keeps the
	// Settings page's local config state in sync so the Color Scheme Select
	// doesn't revert while the App-level debounced save is in flight.
	// ThemeSettingsSection calls this via its `onThemeChange` prop.
	const handleThemeChangeLocal = useCallback(
		(mode: VoiceTyperConfig["theme_mode"]) => {
			setConfig((prev) => (prev ? { ...prev, theme_mode: mode } : prev));
			if (_cachedConfig) _cachedConfig = { ..._cachedConfig, theme_mode: mode };
			onThemeChange?.(mode);
		},
		[onThemeChange],
	);

	if (!config) {
		return (
			<div className="flex h-full items-center justify-center">
				<div className="space-y-2 text-center">
					<Spinner size={24} className="mx-auto" />
					<p className="text-sm text-(--text-muted)">Loading settings...</p>
				</div>
			</div>
		);
	}

	// UX-028: filter settings sections by label/description. Passed to each
	// section component as the `isVisible` prop so the sections can do their
	// own per-row visibility checks (and section-level hide-when-empty
	// checks) without duplicating the filter logic.
	const _filter_settings = (label: string, info?: string): boolean => {
		if (!settingsFilter.trim()) return true;
		const q = settingsFilter.toLowerCase();
		return (
			label.toLowerCase().includes(q) ||
			info?.toLowerCase().includes(q) ||
			false
		);
	};

	return (
		<div className="min-h-full">
			<div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
				{/* Header */}
				<PageHeading
					title={t("settings.title")}
					description="Adjust Voice Typer to your preferences."
				/>

				{/* UX-028: Settings search/filter */}
				<SearchField
					value={settingsFilter}
					onChange={setSettingsFilter}
					placeholder={t("settings.searchPlaceholder")}
				/>

				{/* ── SECTION: Appearance (theme mode, preset, custom picker, text size) ── */}
				<ThemeSettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
					themeModeProp={themeModeProp}
					onThemeChange={handleThemeChangeLocal}
				/>

				{/* ── SECTION: General + Overlay (autostart, UI lang, notifications, tray, bubble) ── */}
				<GeneralSettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
				/>

				{/* ── SECTION: Hotkey + Recording (dictation key, mode, push-to-talk) ── */}
				<HotkeySettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
				/>

				{/* ── SECTION: Post-Processing + LLM Polishing (language, cleanup, API key) ── */}
				<ModelSettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
				/>

				{/* ── SECTION: Audio Enhancement (volume ducking, noise filter chain) ── */}
				<AudioSettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
				/>

				{/* ── SECTION: Audio & Recovery + Privacy & Consent (crash recovery, consents, export) ── */}
				<PrivacySettingsSection
					config={config}
					updateConfig={updateConfig}
					updateConfigDebounced={updateConfigDebounced}
					isVisible={_filter_settings}
				/>

				{/* ── SECTION: Troubleshooting ──────────────────────────── */}
				{/* NEW-UX-025: previously only had "View Logs" (which lied —
                                        it opened the log FOLDER, not a log viewer) and "Reset to
                                        Defaults" (destructive, no undo).  We now also surface:
                                                - Diagnostics link (opens the About page's diagnostics
                                                        section which has version, ASR backend, device, etc.)
                                                - Help / FAQ link
                                                - Report a Bug link
                                        And clarify the "View Logs" label. */}
				<SettingsSection
					title="Troubleshooting"
					description="Diagnostic tools, help, and support."
				>
					<div className="px-3.5 py-3.5 flex flex-wrap gap-3">
						<Button
							variant="outline"
							className="gap-2"
							onClick={viewLogs}
							aria-label="Open log folder"
							title="Open the folder containing the Python backend's log files"
						>
							<HugeiconsIcon
								icon={File02Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							Open Log Folder
						</Button>
						<Button
							variant="outline"
							className="gap-2"
							onClick={() => onNavigate?.("about")}
							aria-label="Open Diagnostics"
							title="Open the About page with version, backend status, and config info"
						>
							<HugeiconsIcon
								icon={InformationCircleIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							Diagnostics
						</Button>
						<Button
							variant="outline"
							className="gap-2"
							onClick={() =>
								window.open(
									"https://github.com/AbdallahIsDev/voice-typer/blob/main/README.md",
									"_blank",
									"noopener,noreferrer",
								)
							}
							aria-label="Open documentation"
							title="Open the project README in your browser"
						>
							<HugeiconsIcon
								icon={Book02Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							Help & FAQ
						</Button>
						<Button
							variant="outline"
							className="gap-2"
							onClick={() =>
								window.open(
									"https://github.com/AbdallahIsDev/voice-typer/issues",
									"_blank",
									"noopener,noreferrer",
								)
							}
							aria-label="Report a bug"
							title="Open the GitHub issue tracker"
						>
							<HugeiconsIcon
								icon={Bug02Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							Report a Bug
						</Button>
						<Button
							variant="destructive"
							className="gap-2"
							onClick={() => setShowResetDialog(true)}
							aria-label="Reset to Defaults"
							title="Reset all settings to their default values (cannot be undone)"
						>
							<HugeiconsIcon
								icon={RefreshIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							Reset to Defaults
						</Button>
					</div>
				</SettingsSection>

				{/* BUGFIX: replaced the fixed bottom-right banner with a subtle
                                        header subtitle that's barely visible — shows "Auto-save" in
                                        dim text only during the brief save operation, then fades to
                                        invisible. The old design was distracting and always visible. */}
				<p className="-mt-6 mb-0 text-[10px] text-(--text-muted)/40 text-right">
					{saving ? (
						<span className="inline-flex items-center gap-1">
							<span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
							Saving...
						</span>
					) : (
						<span className="inline-flex items-center gap-1">Auto-save</span>
					)}
				</p>
			</div>

			{/* Reset Confirmation Dialog */}
			{showResetDialog && (
				<div
					className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40"
					onClick={() => setShowResetDialog(false)}
					onKeyDown={(e) => {
						if (e.key === "Escape") setShowResetDialog(false);
					}}
					role="dialog"
					aria-modal="true"
					aria-labelledby="reset-dialog-title"
				>
					<div
						role="document"
						className={cn(
							"animate-scale-in w-100 rounded-xl border border-border",
							"bg-(--bg) p-6 shadow-2xl",
						)}
						onClick={(e) => e.stopPropagation()}
						onKeyDown={(e) => {
							if (e.key === "Escape") setShowResetDialog(false);
						}}
					>
						<h2
							id="reset-dialog-title"
							className="text-lg font-semibold text-(--text-primary) mb-3"
						>
							Reset to Defaults
						</h2>
						<p className="text-sm text-(--text-muted) mb-6">
							Are you sure you want to reset all settings to their default
							values? This cannot be undone.
						</p>
						<div className="flex justify-end gap-3">
							<Button
								variant="ghost"
								onClick={() => setShowResetDialog(false)}
								aria-label="Cancel reset"
							>
								Cancel
							</Button>
							<Button
								variant="destructive"
								onClick={resetToDefaults}
								autoFocus
								aria-label="Confirm reset to defaults"
							>
								Reset to Defaults
							</Button>
						</div>
					</div>
				</div>
			)}

			{/* NEW-TS-004: use the shared Snackbar component from useSnackbar. */}
			<Snackbar />
		</div>
	);
}
