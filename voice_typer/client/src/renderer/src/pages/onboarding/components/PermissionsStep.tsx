import type { Ref } from "react";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { HEADING_CLASS } from "../lib/constants";
import type { PermissionsResult, PermissionsTestState } from "../lib/types";

export interface PermissionsStepProps {
	headingRef: Ref<HTMLHeadingElement>;
	permissionsResult: PermissionsResult | null;
	permissionsLoading: boolean;
	permissionsTest: PermissionsTestState;
	onTestHotkey: () => void;
	onRefreshPermission: () => void;
}

export function PermissionsStep({
	headingRef,
	permissionsResult,
	permissionsLoading,
	permissionsTest,
	onTestHotkey,
	onRefreshPermission,
}: PermissionsStepProps) {
	// prefer i18n keys; fall back to literals for legacy backends.
	const instr = permissionsResult?.instructions ?? null;
	const titleText = instr?.title_key ? t(instr.title_key) : instr?.title;
	const stepTexts = instr?.steps_keys
		? instr.steps_keys.map((k) => t(k))
		: (instr?.steps ?? []);
	// Fix 10: branch failure message on permission state.
	const failureMessage =
		permissionsResult?.needed === true
			? t("onboarding.permissionsTestFailureBlocked")
			: t("onboarding.permissionsTestFailure");

	return (
		<>
			<h2 ref={headingRef} tabIndex={-1} className={HEADING_CLASS}>
				{t("onboarding.permissionsTitle")}
			</h2>
			<p className="mb-4 text-sm text-(--text-muted)">
				{t("onboarding.permissionsDescription")}
			</p>
			<div aria-live="polite" aria-busy={permissionsLoading} className="mb-4">
				{permissionsLoading && (
					<div className="flex items-center gap-2 text-sm text-(--text-muted)">
						<Spinner />
						<span>{t("onboarding.permissionsLoading")}</span>
					</div>
				)}
				{/* : probe-failure branch — distinct from
					the "no permission needed" happy path. The server-side
					probe can fail (e.g. the `check_keyboard_permission`
					import fails) and previously the renderer fell through to
					`permissionsNoneNeeded` ("Hotkeys will work out of the
					box") which was FALSE. Now we show a clear error message
					and a Refresh button (already rendered below when
					`needed === true`). The wizard's Continue button is
					blocked via the `permissionsProbeFailed` gate in
					Onboarding.tsx. */}
				{!permissionsLoading &&
					permissionsResult &&
					permissionsResult.state === "error" && (
						<output
							role="alert"
							className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive"
						>
							{t("onboarding.permissionsCheckFailed")}
						</output>
					)}
				{!permissionsLoading &&
					permissionsResult &&
					permissionsResult.state !== "error" &&
					(permissionsResult.needed ? (
						<output className="rounded-lg border border-amber-400/40 bg-amber-50 dark:bg-amber-950/20 p-4 text-sm">
							<p className="mb-2 font-medium text-(--text-primary)">
								{t("onboarding.permissionsNeeded")}
							</p>
							{titleText && stepTexts.length > 0 && (
								<div className="space-y-2">
									<p className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
										{titleText}
									</p>
									<ol className="ml-4 list-decimal space-y-1 text-xs text-(--text-secondary)">
										{stepTexts.map((s) => (
											<li key={s}>{s}</li>
										))}
									</ol>
									{instr?.commands && instr.commands.length > 0 && (
										<pre className="mt-2 overflow-x-auto rounded bg-(--bg-subtle) p-2 text-xs text-(--text-secondary)">
											{instr.commands.join("\n")}
										</pre>
									)}
								</div>
							)}
						</output>
					) : permissionsResult.state === "granted" ? (
						<p className="text-sm text-(--text-secondary)">
							{t("onboarding.permissionsOk")}
						</p>
					) : (
						<p className="text-sm text-(--text-secondary)">
							{t("onboarding.permissionsNoneNeeded")}
						</p>
					))}
			</div>
			{/* : refresh-permission button — re-probes after granting. */}
			{permissionsResult?.needed === true && !permissionsLoading && (
				<div className="mb-4">
					<Button
						type="button"
						variant="outline"
						onClick={onRefreshPermission}
						aria-label={t("onboarding.permissionsRefreshAria")}
					>
						{t("onboarding.permissionsRefresh")}
					</Button>
				</div>
			)}
			<div className="space-y-2">
				<Button
					type="button"
					variant="outline"
					onClick={onTestHotkey}
					disabled={permissionsLoading || permissionsTest.kind === "listening"}
					aria-label={t("onboarding.permissionsTestButton")}
				>
					{t("onboarding.permissionsTestButton")}
				</Button>
				<div aria-live="polite" className="text-xs text-(--text-muted)">
					{permissionsTest.kind === "listening" && (
						<span>{t("onboarding.permissionsTestLabel")}</span>
					)}
					{permissionsTest.kind === "success" && (
						<span className="text-green-600 dark:text-green-400">
							{t("onboarding.permissionsTestSuccess")}
						</span>
					)}
					{permissionsTest.kind === "failure" && (
						<span className="text-red-600 dark:text-red-400">
							{failureMessage}
						</span>
					)}
				</div>
			</div>
		</>
	);
}

export default PermissionsStep;
