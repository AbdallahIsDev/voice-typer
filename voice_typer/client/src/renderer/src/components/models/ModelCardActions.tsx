/**
 * ModelCardActions — pure presentational button row for a single model.
 *
 *  fix #1: extracted from `pages/Models.tsx`'s 60-line nested
 * ternary (the `model.isActive ? <>...</> : !model.downloaded && ...
 * ? <Download/> : <><Select/><Delete/></>)` block). This component
 * takes a `ModelInfo` + handler callbacks and renders one of four
 * visual states without any IPC or state coupling — making it
 * independently testable and reusable.
 *
 * Visual states:
 *   1. Active model, available (downloaded) →
 *      disabled "Active" tick + Delete icon. Deleting the active model
 *      is allowed: the backend removes the files and reassigns the
 *      selection (first other downloaded model, or the "no model
 *      selected" state when none exists) — the old refuse-and-switch
 *      flow dead-ended users with a single downloaded model.
 *   2. Not downloaded → "Download" button (disabled while any other
 *      download is in progress; shows a localized "one at a time"
 *      tooltip when disabled). NO Delete icon — a model that isn't on
 *      disk has nothing to delete; showing a trash affordance next to
 *      a not-installed model (e.g. the default `tiny` before the
 *      user ever downloads anything) misleads the user into thinking
 *      something is installed. The backend would only answer "Model
 *      not downloaded" / "nothing to delete".
 *   3. Downloaded → "Select" button + Delete icon (the trash
 *      affordance is exactly the "installed model can be removed"
 *      signal).
 *   4. ( fix #7) Deps-installable + not depsOk → "Download Deps"
 *      button using existing `models.download.deps*` i18n keys.
 *
 *  fix #11: the Select button now sets `aria-busy` while a
 * selection is in flight and switches its `aria-label` to the
 * "Selecting…" state so screen-reader users hear the in-progress
 * status (not the stale "Select {name}" label).
 *
 *  (): the same `aria-busy` + label-swap treatment is now
 * applied to the Download + Download Deps buttons:
 *   • Download button: `aria-busy={isDownloadingThis}`, aria-label
 *     swaps to "Downloading…" when in-flight.
 *   • Download Deps button: `aria-busy={isInstallingDepsThis}`,
 *     label + text swap to "Downloading…" when in-flight, icon
 *     spins. The `isInstallingDepsThis` prop was declared on the
 *     interface but never destructured — now destructured + wired.
 *
 *  #9: the Select button now uses `Tick02Icon` (was `PlayIcon`)
 * — Select is a "mark active" affordance, not a "play media" one.
 *
 *  fix #12: disabled Download buttons get a `title` attribute
 * sourced from `models.download.oneAtATime` so users hovering over the
 * disabled button know WHY it's disabled (instead of just seeing a
 * greyed-out control).
 */
import {
	Delete01Icon,
	Download01Icon,
	Loading03Icon,
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import { formatModelSize, type ModelInfo } from "@/lib/utils/models";

// ── Fixed width for model-size download buttons ───────────────────────
//
// (2026-08-21): every "Download <size>" button uses ONE shared width so
// the buttons line up identically across models regardless of the size
// shown ("75 MB", "3 GB", "809 MB" — all fit). Apply this token to the
// Download button in Branch 2; the "Download Deps" button (Branch 4)
// shows a label instead of a size and keeps its intrinsic width.
// Buttons that display a size also apply `justify-start` so the icon +
// text begin at the same left position in every row (the Button base
// centers its content by default). (2026-08-28): width widened to 96px
// (w-24), gap bumped to gap-2, and the text size dropped to text-xs.
export const DOWNLOAD_SIZE_BUTTON_WIDTH = "w-24";
// Content is left-aligned (icon + size text share one start position
// across all model rows) instead of centered inside the fixed width.
export const DOWNLOAD_CONTENT_ALIGNMENT = "justify-start";
// Compact download icon that visually matches the 11px size text
// height (16px `h-4` dominated the button; 12px reads proportional).
export const DOWNLOAD_ICON_CLASS = "h-3 w-3";

export interface ModelCardActionsProps {
	model: ModelInfo;
	/** True while THIS model is being selected (Select button shows spinner). */
	isSelectingThis: boolean;
	/** True while THIS model is being downloaded (Download button shows "Downloading…"). */
	isDownloadingThis: boolean;
	/** True while ANY download is in progress (disables the Download button on other models). */
	anyDownloading: boolean;
	/** True while ANY deps install is in flight (disables the other
	 * models' Download/Deps buttons — the backend installs one deps set
	 * at a time, and a second concurrent click cleared the first model's
	 * in-flight spinner because `installingDepsModel` is a single slot). */
	anyInstallingDeps?: boolean;
	onSelect: () => void;
	onDownload: () => void;
	onDelete: () => void;
	/**  fix #7: triggered by the "Download Deps" button. */
	onInstallDeps?: () => void;
	/** True while THIS model is installing dependencies.  ():
	 * drives the Download Deps button's `aria-busy` + "Downloading…"
	 * label swap + spinning icon. Previously declared on the interface
	 * but never destructured — the wiring was missing. */
	isInstallingDepsThis?: boolean;
}

/**
 * fix #12 helper: returns the "one download at a time" tooltip text.
 * The key is verified present in every locale catalogue.
 */
function oneAtATimeTitle(): string {
	return t("models.download.oneAtATime");
}

/**
 * Native `title` tooltips never fire on a disabled Button: button.tsx
 * applies `disabled:pointer-events-none`, and a pointer-events:none
 * element is never hit-tested — the hint was dead on arrival. The hint
 * must live on a WRAPPER that keeps pointer events. The button keeps
 * its `title` attribute too (tests assert its presence there).
 */
function DisabledHintTooltip({
	hint,
	children,
}: {
	hint?: string;
	children: ReactNode;
}) {
	if (!hint) return <>{children}</>;
	return (
		<span className="inline-flex" title={hint}>
			{children}
		</span>
	);
}

/**
 *  fix #11 helper: aria-label for the Select button. While a
 * selection is in-flight, screen readers should announce the
 * "Selecting…" state instead of the stale "Select {name}" label.
 */
function selectAriaLabel(model: ModelInfo, isSelectingThis: boolean): string {
	if (isSelectingThis) {
		// Use the existing "Selecting…" translation; the model name is
		// already announced via the card's heading so we don't need to
		// repeat it here.
		return t("models.selecting");
	}
	return t("models.card.selectAria", { name: model.name });
}

export function ModelCardActions({
	model,
	isSelectingThis,
	isDownloadingThis,
	anyDownloading,
	//previously declared on the interface but never
	// destructured or read — the Download/Deps buttons now disable on
	// any in-flight deps install, not just downloads.
	anyInstallingDeps,
	onSelect,
	onDownload,
	onDelete,
	onInstallDeps,
	//previously declared on the interface but never
	// destructured — the Download Deps button never showed its
	// in-flight state. Now destructured + wired below.
	isInstallingDepsThis,
}: ModelCardActionsProps) {
	// ── Branch 1: Active model, available ───────────────────────────
	//
	// Renders only when the active model is actually usable (downloaded).
	// The Delete icon is PRESENT: deleting the active model is allowed —
	// the backend removes the files and reassigns the selection (first
	// other downloaded model, or the "no model selected" state when none
	// exists). The old refuse-and-switch flow was a dead-end for users
	// with a single downloaded model.
	if (model.isActive && model.downloaded) {
		return (
			<div className="flex items-center gap-2 shrink-0">
				<DeleteButton model={model} onDelete={onDelete} />
				<Button
					variant="secondary"
					size="sm"
					className="gap-1 cursor-default opacity-60"
					disabled
					aria-label={t("models.card.activeAria", { name: model.name })}
				>
					<HugeiconsIcon
						icon={Tick02Icon}
						strokeWidth={2}
						className="h-4 w-4"
					/>
					{t("models.active")}
				</Button>
			</div>
		);
	}

	//Branch 4 ( fix #7): deps-installable + not depsOk ───
	//
	// Rendered BEFORE the "not downloaded" branch so dep-gated models
	// (Parakeet) show "Download Deps" instead of "Download" until their
	// deps are installed. The "Download Deps" button uses the existing
	// `models.download.deps` / `models.download.depsAria` i18n keys
	// (no new translation keys are introduced).
	//
	// the button now exposes `aria-busy` while a deps
	// install is in-flight, swaps its label + visible text to
	// "Downloading…", and spins the icon — matching the Select
	// button's in-flight treatment so SR users hear the in-progress
	// state (not the stale per-model label).
	if (model.depsInstallable && !model.depsOk) {
		// Disabled while ANOTHER model's download OR deps install is in
		// flight (this model's own in-flight install disables it too — it
		// shows the "Downloading…" spinner).
		const depsDisabled =
			anyDownloading || (Boolean(anyInstallingDeps) && !isInstallingDepsThis);
		const depsHint =
			depsDisabled && !isInstallingDepsThis ? oneAtATimeTitle() : undefined;
		return (
			<div className="flex items-center gap-2 shrink-0">
				<DisabledHintTooltip hint={depsHint}>
					<Button
						variant="outline"
						size="sm"
						//(2026-08-21): left-aligned like the size Download
						// buttons (content shares the same start position).
						className={cn("gap-1", DOWNLOAD_CONTENT_ALIGNMENT)}
						onClick={onInstallDeps}
						disabled={depsDisabled}
						aria-busy={isInstallingDepsThis}
						aria-label={
							isInstallingDepsThis
								? t("models.downloading")
								: t("models.download.depsAria", { name: model.name })
						}
						//fix #12: explain why the button is disabled.
						//skip the tooltip when THIS is the in-flight
						// install (the button is showing "Downloading…" — the
						// "one at a time" hint would be contradictory).
						title={depsHint}
					>
						<HugeiconsIcon
							//in-flight presentation: a LOADING spinner glyph
							// (spinning) — the static download icon spinning
							// in place read as a broken/odd affordance.
							icon={isInstallingDepsThis ? Loading03Icon : Download01Icon}
							strokeWidth={2}
							className={cn("h-4 w-4", isInstallingDepsThis && "animate-spin")}
						/>
						{isInstallingDepsThis
							? t("models.downloading")
							: t("models.download.deps")}
					</Button>
				</DisabledHintTooltip>
				{/* A downloaded model whose deps are missing can still be
				    deleted (files on disk). A NOT-downloaded model has
				    nothing to delete — no trash icon (matches the
				    Download branch: "not installed → no delete"). */}
				{model.downloaded && <DeleteButton model={model} onDelete={onDelete} />}
			</div>
		);
	}

	// ── Branch 2: not downloaded → Download ────────────────────────
	//
	// the button now exposes `aria-busy` while the
	// download is in-flight and swaps its `aria-label` to
	// "Downloading…" so SR users hear the in-progress state (previously
	// only the visible text swapped — the stale per-model aria-label
	// was announced throughout the entire download).
	//
	// NO Delete icon here: the model isn't installed, so there is
	// nothing to remove. (Even when this model is the configured active
	// model — e.g. the default `tiny` before the user downloads
	// anything — a trash affordance next to "Download" falsely implies
	// an installed model that can be removed.)
	if (!model.downloaded) {
		// Disabled while another model's download OR deps install is in
		// flight (this model's own in-flight download disables it too —
		// it shows the "Downloading…" spinner).
		const downloadDisabled =
			anyDownloading || (Boolean(anyInstallingDeps) && !isDownloadingThis);
		const downloadHint =
			downloadDisabled && !isDownloadingThis ? oneAtATimeTitle() : undefined;
		return (
			<div className="flex items-center gap-2 shrink-0">
				<DisabledHintTooltip hint={downloadHint}>
					<Button
						variant="outline"
						size="sm"
						//(2026-08-21): fixed, identical width for every model-size
						// Download button at REST (see DOWNLOAD_SIZE_BUTTON_WIDTH
						// above) — "75 MB" / "3 GB" / "809 MB" all render the same
						// button width + alignment. The content is left-aligned
						// (justify-start) so the icon + size text begin at the same
						// horizontal position in every model row.
						// (2026-09-03): the IN-FLIGHT state drops the fixed width —
						// the label swaps to the localized "Downloading…" so a
						// content-fitted width is used for that state (user request:
						// no truncated size text inside a disabled spinner button).
						className={cn(
							"gap-2 text-xs whitespace-nowrap",
							!isDownloadingThis && DOWNLOAD_SIZE_BUTTON_WIDTH,
							DOWNLOAD_CONTENT_ALIGNMENT,
						)}
						onClick={onDownload}
						disabled={downloadDisabled}
						aria-busy={isDownloadingThis}
						aria-label={
							isDownloadingThis
								? t("models.downloading")
								: t("models.card.downloadAria", { name: model.name })
						}
						//fix #12: explain why the button is disabled
						// (one download at a time) so users don't think the
						//button is broken. : skip the tooltip when THIS
						// is the in-flight download (the button is showing
						// "Downloading…" — the "one at a time" hint would be
						// contradictory).
						title={downloadHint}
					>
						<HugeiconsIcon
							//in-flight presentation: a LOADING spinner glyph
							// (spinning) replaces the download glyph — the
							// static download icon spinning around itself read
							// as broken. At rest the compact download icon
							// matches the text-xs size.
							icon={isDownloadingThis ? Loading03Icon : Download01Icon}
							strokeWidth={2}
							className={cn(
								isDownloadingThis ? "h-3.5 w-3.5" : DOWNLOAD_ICON_CLASS,
								isDownloadingThis && "animate-spin",
							)}
						/>
						{/* At rest: download icon + model size only (the icon
						    communicates "download" — see the 2026-08-20 overhaul,
						    point 7). In-flight: the localized "Downloading…" label
						    replaces the size (a frozen size number inside a
						    disabled spinner button misreads as "downloaded"). */}
						{isDownloadingThis ? (
							t("models.downloading")
						) : (
							<span className="font-medium">{formatModelSize(model.size)}</span>
						)}
					</Button>
				</DisabledHintTooltip>
			</div>
		);
	}

	// ── Branch 3: downloaded → Select + Delete ─────────────────────
	//
	//#9: Select now uses `Tick02Icon` (was `PlayIcon`) —
	// Select is a "mark active" affordance, not a "play media" one.
	// Destructive control sits LEFT of the primary action — same
	// position as Branch 1's Delete-before-Active layout, so the
	// trash icon doesn't jump between card states.
	return (
		<div className="flex items-center gap-2 shrink-0">
			<DeleteButton model={model} onDelete={onDelete} />
			<Button
				variant={isSelectingThis ? "secondary" : "outline"}
				size="sm"
				className="gap-1"
				onClick={onSelect}
				disabled={isSelectingThis}
				//fix #11: announce the in-flight selection state
				// to assistive-tech users via aria-busy + a label swap.
				aria-busy={isSelectingThis}
				aria-label={selectAriaLabel(model, isSelectingThis)}
			>
				<HugeiconsIcon
					icon={Tick02Icon}
					strokeWidth={2}
					className={cn("h-4 w-4", isSelectingThis && "animate-spin")}
				/>
				{isSelectingThis ? t("models.selecting") : t("models.select")}
			</Button>
		</div>
	);
}

// ── Sub-component: Delete icon button ─────────────────────────────────
//
// Extracted because it appears in Branch 1 (Active) and Branch 3
// (Downloaded) — a verbatim duplicate in the original 60-line ternary.

interface DeleteButtonProps {
	model: ModelInfo;
	onDelete: () => void;
}

function DeleteButton({ model, onDelete }: DeleteButtonProps) {
	return (
		<Button
			variant="ghost"
			size="icon-xs"
			onClick={onDelete}
			className="text-(--text-muted) hover:text-destructive"
			aria-label={t("models.card.deleteAria", { name: model.name })}
			title={t("models.card.deleteAria", { name: model.name })}
		>
			<HugeiconsIcon
				icon={Delete01Icon}
				strokeWidth={2.5}
				className="h-4 w-4"
			/>
		</Button>
	);
}
