import { Folder02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { ReadonlyRow } from "@/components/common/ReadonlyRow";
import { Button } from "@/components/ui/button";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { formatBytes } from "@/lib/format";
import type { ModelStorageSummary } from "@/types/ipc";

export interface ModelStorageCardProps {
	/** Null while the status fetch settles or on older backends. */
	storage: ModelStorageSummary | null;
}

/**
 * Models-page storage card: total shared-hub bytes + hub path + a
 * one-click "Open Data Folder" button (config dir, the support case).
 * Null renders nothing so older backends stay clean.
 */
export function ModelStorageCard({ storage }: ModelStorageCardProps) {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	if (!storage) return null;

	// Mirrors PrewarmAndUpdates' View-Log handler: the OS window is the
	// success feedback, only failures surface a snack.
	const handleOpenDataFolder = async () => {
		try {
			const result = await call<{
				opened: boolean;
				path?: string;
				reason?: string;
			}>("open_data_folder");
			if (!result?.opened) {
				showSnack(t("models.errors.unknown"), "error");
			}
		} catch (err) {
			showSnack(
				`${t("models.errors.unknown")}${err instanceof Error ? `: ${err.message}` : ""}`,
				"error",
			);
		}
	};

	return (
		<section
			data-testid="model-storage-card"
			aria-label={t("models.storage.storageUsed")}
			className="flex flex-col gap-3 rounded-lg border border-border/10 bg-surface-subtle p-4"
		>
			<div className="flex flex-col gap-2">
				<ReadonlyRow
					label={t("models.storage.storageUsed")}
					value={formatBytes(storage.used_bytes)}
				/>
				<p
					title={storage.hub_path}
					className="min-w-0 break-all font-mono text-xs text-muted-foreground"
				>
					{storage.hub_path}
				</p>
			</div>
			<p className="text-xs leading-relaxed text-muted-foreground">
				{t("models.storage.sharedCacheHint")}
			</p>
			<div className="flex justify-end">
				<Button
					variant="outline"
					size="sm"
					onClick={handleOpenDataFolder}
					className="gap-2 text-muted-foreground hover:text-foreground"
					aria-label={t("models.storage.openDataFolder")}
				>
					<HugeiconsIcon
						icon={Folder02Icon}
						strokeWidth={2}
						aria-hidden="true"
						className="h-4 w-4"
					/>
					{t("models.storage.openDataFolder")}
				</Button>
			</div>
		</section>
	);
}
