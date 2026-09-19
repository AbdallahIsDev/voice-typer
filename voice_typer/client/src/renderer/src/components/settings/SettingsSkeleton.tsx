import { SwitchSkeleton } from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface SettingsSkeletonProps {
	/** Number of skeleton rows to render. Default 3. */
	rows?: number;
	/** Shape of the trailing control placeholder. Default "switch". */
	control?: "switch" | "select";
	/** Additional class names. */
	className?: string;
}

function ControlSkeleton({ control }: { control: "switch" | "select" }) {
	if (control === "select") {
		return <Skeleton className="h-8 w-24 rounded-4xl" />;
	}
	return <SwitchSkeleton />;
}

export function SettingsSkeleton({
	rows = 3,
	control = "switch",
	className = "",
}: SettingsSkeletonProps) {
	return (
		<output
			className={cn("flex flex-col gap-4", className)}
			aria-busy="true"
			aria-label={t("a11y.loading")}
		>
			<div className="flex flex-col gap-1">
				<Skeleton className="h-7 w-40" />
				<Skeleton className="h-5 w-64" />
			</div>
			<div className="w-full rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/5">
				{Array.from({ length: rows }, (_, i) => (
					<div
						//restored biome-ignore, the rule fires under `preset: "recommended"` (the previous `recommended: true` was deprecated and silently skipped enforcement). Skeleton rows are static, identical, and never reorder; index is the only stable key.
						// biome-ignore lint/suspicious/noArrayIndexKey: skeleton rows are static and identical; index is the only stable key
						key={`row-${rows}-${i}`}
						className="flex items-center justify-between gap-6 px-4 py-2"
					>
						<div className="flex items-center gap-2">
							<Skeleton className="h-5 w-28" />
							<Skeleton className="h-4 w-4" />
						</div>
						<ControlSkeleton control={control} />
					</div>
				))}
			</div>
		</output>
	);
}
