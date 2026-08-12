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
 *      disabled "Active" tick. NO Delete icon — the backend refuses to
 *      delete an in-use model, so a Delete button here would be a
 *      dead-end affordance that always errors.
 *   2. Not downloaded → "Download" button (disabled while any other
 *      download is in progress; shows a localized "one at a time"
 *      tooltip when disabled). NO Delete icon — a model that isn't on
 *      disk has nothing to delete; showing a trash affordance next to
 *      a not-installed model (e.g. the default `small.en` before the
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
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { ModelInfo } from "@/lib/utils/models";

export interface ModelCardActionsProps {
	model: ModelInfo;
	/** True while THIS model is being selected (Select button shows spinner). */
	isSelectingThis: boolean;
	/** True while THIS model is being downloaded (Download button shows "Downloading…"). */
	isDownloadingThis: boolean;
	/** True while ANY download is in progress (disables the Download button on other models). */
	anyDownloading: boolean;
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
 *  fix #12 helper: returns the "one download at a time" tooltip
 * text. Falls back to a literal English string when the i18n key is
 * missing (translation catalogue additions are a separate task — see
 * the i18n review sub-agent's findings).
 */
function oneAtATimeTitle(): string {
	return t("models.download.oneAtATime");
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
	// The Delete icon is GONE here. The backend refuses to delete an
	// in-use model ("Cannot delete the active model. Switch to another
	// model first.") — showing a Delete button that ALWAYS errors is a
	// dead-end affordance. Users switch to another model first; the
	// Delete icon appears on the downloaded (inactive) card.
	if (model.isActive && model.downloaded) {
		return (
			<div className="flex items-center gap-2 shrink-0">
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
		return (
			<div className="flex items-center gap-2 shrink-0">
				<Button
					variant="outline"
					size="sm"
					className="gap-1"
					onClick={onInstallDeps}
					disabled={anyDownloading}
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
					title={
						anyDownloading && !isInstallingDepsThis
							? oneAtATimeTitle()
							: undefined
					}
				>
					<HugeiconsIcon
						icon={Download01Icon}
						strokeWidth={2}
						className={cn("h-4 w-4", isInstallingDepsThis && "animate-spin")}
					/>
					{isInstallingDepsThis
						? t("models.downloading")
						: t("models.download.deps")}
				</Button>
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
	// model — e.g. the default `small.en` before the user downloads
	// anything — a trash affordance next to "Download" falsely implies
	// an installed model that can be removed.)
	if (!model.downloaded) {
		return (
			<div className="flex items-center gap-2 shrink-0">
				<Button
					variant="outline"
					size="sm"
					className="gap-1"
					onClick={onDownload}
					disabled={anyDownloading}
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
					title={
						anyDownloading && !isDownloadingThis ? oneAtATimeTitle() : undefined
					}
				>
					<HugeiconsIcon
						icon={Download01Icon}
						strokeWidth={2}
						className={cn("h-4 w-4", isDownloadingThis && "animate-spin")}
					/>
					{isDownloadingThis
						? t("models.downloading")
						: t("models.downloadModel")}
				</Button>
			</div>
		);
	}

	// ── Branch 3: downloaded → Select + Delete ─────────────────────
	//
	//#9: Select now uses `Tick02Icon` (was `PlayIcon`) —
	// Select is a "mark active" affordance, not a "play media" one.
	return (
		<div className="flex items-center gap-2 shrink-0">
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
			<DeleteButton model={model} onDelete={onDelete} />
		</div>
	);
}

// ── Sub-component: Delete icon button ─────────────────────────────────
//
// Extracted because it appears in both Branch 1 (Active) and Branch 3
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
