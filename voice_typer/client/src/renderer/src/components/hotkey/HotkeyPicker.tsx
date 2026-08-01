import {
	AlertCircleIcon,
	Cancel01Icon,
	KeyboardIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { checkScreenReaderConflict } from "./checkHotkeyConflict";
import { formatHotkeyLabel, tryCommitHotkey } from "./hotkey-utils";
import { useHotkeyCapture } from "./useHotkeyCapture";

interface HotkeyPickerProps {
	value: string;
	onChange: (hotkey: string) => void;
	mode: "single" | "combo";
	/**
	 * Optional preset options for the dropdown menu.
	 * When provided, a dropdown is rendered so the user can pick from
	 * these presets. When omitted or empty, no dropdown is shown and
	 * only the capture button is available.
	 */
	presets?: { value: string; label: string }[];
	className?: string;
	"aria-label"?: string;
	/**
	 * ESC-: optional callback invoked when capture mode starts.
	 * Used by the parent to pause the global ESC cancel hotkey in the
	 * backend so that pressing Escape during capture doesn't trigger
	 * recording cancellation.
	 */
	onCaptureStart?: () => void;
	/**
	 * ESC-: optional callback invoked when capture mode ends
	 * (user pressed Escape, selected a key, or clicked the button
	 * again).  Used by the parent to resume the global ESC cancel
	 * hotkey in the backend.
	 */
	onCaptureEnd?: () => void;
	/**
	 * DUPLICATE-001: hotkey strings that are already occupied by other
	 * settings. When the user tries to set this picker to a value that's
	 * already in use, an error is shown and the change is rejected.
	 * This prevents two settings from having the same hotkey.
	 * Example: if the dictation key is set to "<shift>", passing
	 * occupiedHotkeys={["<shift>"]} to the repaste key picker prevents
	 * the user from also setting the repaste key to Shift.
	 */
	occupiedHotkeys?: string[];
	/**
	 * when true (and ``value`` is non-empty), renders a
	 * small "Clear" (X) button next to the picker that calls
	 * ``onChange("")``. Lets the user unset a hotkey without having
	 * to capture a new one. Defaults to ``false`` so existing
	 * callers that don't want a clear button see no UI change.
	 */
	allowClear?: boolean;
}

/**
 * Presentational shell for the hotkey picker. All capture-session
 * state (refs, keyboard listeners, countdown, commit / cancel logic)
 * lives in ``useHotkeyCapture``; this component renders the Button +
 * DropdownMenu + output hint + error ``<p>`` + Clear button and wires
 * them to the hook's API.
 *
 * Screen-reader conflict warning: when the currently assigned
 * ``value`` matches a default SR modifier key on the user's platform
 * (e.g. ``<caps_lock>`` on macOS → VoiceOver, or on Windows →
 * Narrator/NVDA/JAWS), a localized warning banner is rendered below
 * the picker. The warning is advisory — the user can keep the
 * conflicting hotkey if they know what they're doing (e.g. they've
 * remapped their SR modifier away from Caps Lock). The detection is
 * heuristic + offline (per C-DATA-1): no OS query, no network call.
 * See ``checkScreenReaderConflict`` in ``checkHotkeyConflict.ts``.
 */

// Hardcoded English fallback string for the SR-conflict warning.
// The i18n key ``hotkeyPicker.capsLockSrConflictWarning`` is owned by
//another task () which will add translations to all 8 locale
// JSON files. Until those land, ``t("hotkeyPicker.capsLockSrConflictWarning")``
// returns the raw key string (no English translation registered) — we
// detect that and fall back to this English string so the warning still
//shows. Once  lands the en.json entry, the ``translated === key``
// check is false and the localized string is used.
const CAPS_LOCK_SR_WARNING_FALLBACK =
	"Caps Lock is the default modifier key for VoiceOver / Narrator. Choosing it may break your screen reader.";

/**
 * Resolve the SR-conflict warning text, using the i18n key when
 * available and falling back to a hardcoded English string otherwise.
 *
 * Exported (not inlined in JSX) so the test suite can assert against
 * the fallback string without depending on the i18n key being
 * registered.
 */
export function _resolveSrConflictWarning(): string {
	const key = "hotkeyPicker.capsLockSrConflictWarning";
	const translated = t(key);
	// t() falls back to the raw key string when no translation is
	// registered (see i18n/translate.ts:144-148). Detect that and use
	// the hardcoded English fallback so the warning is still visible
	// while the i18n key is pending.
	return translated === key ? CAPS_LOCK_SR_WARNING_FALLBACK : translated;
}

export function HotkeyPicker({
	value,
	onChange,
	mode,
	presets,
	className,
	"aria-label": ariaLabel = "Hotkey picker",
	onCaptureStart,
	onCaptureEnd,
	occupiedHotkeys,
	allowClear = false,
}: HotkeyPickerProps) {
	const {
		recording,
		error,
		secondsRemaining,
		heldModifiersLabel,
		startRecording,
		cancelRecording,
		setError,
		containerRef,
	} = useHotkeyCapture({
		value,
		mode,
		onChange,
		onCaptureStart,
		onCaptureEnd,
		occupiedHotkeys,
	});

	//HOTKEY-: "Custom" sentinel. When the current
	// hotkey is not one of the preset values, the Select would render
	// an empty trigger (Radix Select quirk: a non-empty value that
	// matches no SelectItem suppresses the placeholder). We detect
	// custom values and map them to a "__custom__" sentinel that
	// displays the actual hotkey label, so the dropdown always shows
	// something meaningful.
	//
	// The presets are now passed in from the parent via the `presets`
	// prop — no hard-coded preset logic in this component. If no
	// presets are provided, the dropdown is not rendered at all.
	const presetOptions = presets ?? [];
	const rawPresetValue = mode === "single" ? value.replace(/[<>]/g, "") : value;
	const isPresetValue = presetOptions.some(
		(opt) => opt.value === rawPresetValue,
	);
	const customLabel = value ? formatHotkeyLabel(value) : "";

	// SR-conflict detection: runs against the CURRENTLY assigned value
	// (not the in-progress capture), so the banner stays visible after
	// the user commits a Caps Lock choice — until they pick a different
	// key. The check is a pure offline heuristic (per C-DATA-1) and is
	// cheap enough (a JSON lookup + navigator.platform read) to run on
	// every render. When no value is assigned, no banner is shown.
	const srConflict = value ? checkScreenReaderConflict(value) : null;
	const showSrWarning = srConflict?.conflict === true;

	return (
		<div className="flex flex-col gap-2" ref={containerRef}>
			<div className="flex items-center gap-2">
				<Button
					variant={recording ? "default" : "outline"}
					size="sm"
					onClick={recording ? cancelRecording : startRecording}
					className={cn("gap-2 font-mono", className)}
					aria-label={
						recording
							? t("hotkeyPicker.cancelRecordingAria", { label: ariaLabel })
							: t("hotkeyPicker.recordNewAria", { label: ariaLabel })
					}
					// expose the in-progress modifier combo
					// via aria-keyshortcuts so assistive tech can announce
					// what the user is currently holding. Cleared (omitted)
					// when no modifiers are held so screen readers don't
					// read a stale value.
					{...(recording && heldModifiersLabel
						? { "aria-keyshortcuts": heldModifiersLabel }
						: {})}
				>
					<HugeiconsIcon
						icon={recording ? Cancel01Icon : KeyboardIcon}
						strokeWidth={1.625}
						className="h-4 w-4"
					/>
					{recording ? (
						<span className="animate-pulse">{t("hotkeyPicker.pressAKey")}</span>
					) : (
						<span>{formatHotkeyLabel(value) || t("hotkeyPicker.none")}</span>
					)}
				</Button>

				{presetOptions.length > 0 && (
					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								variant="outline"
								size="sm"
								className="w-40 justify-between font-mono"
								aria-label={t("hotkeyPicker.presetHotkeysAria", {
									label: ariaLabel,
								})}
							>
								<span>
									{(() => {
										if (!value) return t("hotkeyPicker.presets");
										if (isPresetValue) {
											const opt = presetOptions.find(
												(o) => o.value === rawPresetValue,
											);
											return opt?.label ?? formatHotkeyLabel(value);
										}
										return t("hotkeyPicker.customLabel", {
											label: customLabel,
										});
									})()}
								</span>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									width="16"
									height="16"
									viewBox="0 0 24 24"
									fill="none"
									stroke="currentColor"
									strokeWidth="2"
									strokeLinecap="round"
									strokeLinejoin="round"
									className="h-4 w-4 opacity-50"
									aria-hidden="true"
								>
									<path d="m6 9 6 6 6-6" />
								</svg>
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent className="w-40" align="start">
							{presetOptions.map((opt) => (
								<DropdownMenuItem
									key={opt.value}
									onSelect={() => {
										const newValue =
											mode === "single" ? `<${opt.value}>` : opt.value;
										//shared validate-then-conflict-check.
										// resetSession:false because the dropdown has no
										// capture session to reset.
										const r = tryCommitHotkey(newValue, {
											mode,
											value,
											occupiedHotkeys,
											t,
											resetSession: false,
										});
										if (!r.ok) {
											setError(r.error);
											return;
										}
										setError(null);
										onChange(newValue);
									}}
								>
									{opt.label}
								</DropdownMenuItem>
							))}
							{!isPresetValue && value && (
								<DropdownMenuItem
									disabled
									className="text-(--text-muted) cursor-default"
								>
									{t("hotkeyPicker.customLabel", { label: customLabel })}
								</DropdownMenuItem>
							)}
						</DropdownMenuContent>
					</DropdownMenu>
				)}
				{/* Clear button — lets the user unset a
                                    hotkey without having to capture a new one. Only
                                    shown when ``allowClear`` is true, a hotkey is
                                    currently assigned, and we're not in the middle of
                                    capture (the capture button itself toggles to a
                                    cancel button during recording, so a second X would
                                    be redundant).

                                    Accessibility: aria-label and title are
                                    localised via ``t("hotkeyPicker.clearAria",
                                    { label })`` and ``t("hotkeyPicker.clearTitle")``.
                                    Native translations exist in all 8 locale JSON
                                    files. */}
				{allowClear && value && !recording && (
					<Button
						variant="ghost"
						size="sm"
						className="h-7 w-7 p-0 text-(--text-muted)"
						onClick={() => onChange("")}
						aria-label={t("hotkeyPicker.clearAria", { label: ariaLabel })}
						title={t("hotkeyPicker.clearTitle")}
					>
						<HugeiconsIcon
							icon={Cancel01Icon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
					</Button>
				)}
			</div>
			{recording && (
				<output
					className="text-xs text-(--text-muted)"
					// live-region role so
					// screen readers announce countdown ticks and the
					// "Holding: …" line as they update.
					aria-live="polite"
				>
					{t("hotkeyPicker.assignHint")}
					{/* countdown timer. Shown the whole
                                        time so the user knows how long they have;
                                        turns red in the last 10 seconds for emphasis. */}
					<span
						className={cn(
							"ms-2 tabular-nums",
							secondsRemaining <= 10 && "text-destructive",
						)}
					>
						({secondsRemaining}s)
					</span>
					{/* live modifier indicator. Mirrors
                                        the ``aria-keyshortcuts`` attribute on the
                                        capture button so visual users get the same
                                        in-progress feedback screen readers do. */}
					{heldModifiersLabel && (
						<span className="ms-2">
							{t("hotkeyValidation.holding")}: {heldModifiersLabel}
						</span>
					)}
				</output>
			)}
			{error && (
				<p className="text-xs text-destructive" role="alert">
					{error}
				</p>
			)}
			{showSrWarning && (
				// SR-conflict warning banner. Rendered as a ``role="status"``
				// polite live region (NOT ``role="alert"``) because this is
				// an advisory warning, not a blocking error — the user can
				// still proceed. ``aria-live="polite"`` ensures screen
				// readers announce the warning after the user's current
				// interaction, without interrupting in-progress speech.
				//
				// Styling: amber/yellow tone (rather than the red used for
				// hard errors) to signal "caution, but not blocked". Uses
				// inline-flex so the warning icon aligns with the text
				// baseline on the first line (the message can wrap on
				// narrow widths).
				<div
					className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400"
					aria-live="polite"
					data-testid="sr-conflict-warning"
				>
					<HugeiconsIcon
						icon={AlertCircleIcon}
						strokeWidth={1.625}
						className="mt-0.5 h-4 w-4 shrink-0"
						aria-hidden="true"
					/>
					<span>{_resolveSrConflictWarning()}</span>
				</div>
			)}
		</div>
	);
}
