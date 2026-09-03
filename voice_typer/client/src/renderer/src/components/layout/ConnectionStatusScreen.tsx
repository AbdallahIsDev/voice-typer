/**
 * ConnectionStatusScreen — full implementation.
 *
 * Previously this component was a `return null` stub. App.tsx rendered
 * it whenever the renderer was not connected to the Python backend, but
 * because the body was empty the user saw a blank main pane during
 * startup / reconnect / restart. The accessibility test mocked it as
 * `<div data-testid="connection-status" />`, hiding the regression
 * from CI.
 *
 * The component now renders a centered card that:
 *   - Shows a localized title + description explaining the disconnect.
 *   - When `status === "connecting"`: shows a Spinner + the
 *     `connectingProgress` value (if any) as a progress bar.
 *   - When `status === "disconnected"`: shows the last error (if any)
 *     and a primary Retry button via EmptyState's action affordance.
 *   - Reuses the existing `<EmptyState variant="error">` + `<Spinner>`
 *     for visual consistency with other load-failure screens.
 *
 * The retry button is auto-focused so keyboard users land on it
 * immediately after a disconnect — WCAG 2.4.3 Focus Order (Level A).
 */

import { AlertCircleIcon, RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect } from "react";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/i18n";

interface ConnectionStatusScreenProps {
	status: string;
	lastError: string | null;
	onRetry: () => void;
	connectingProgress: number | null;
}

export function ConnectionStatusScreen({
	status,
	lastError,
	onRetry,
	connectingProgress,
}: ConnectionStatusScreenProps) {
	const t = useT();

	// when transitioning to "disconnected", move focus
	// to the Retry button so keyboard users land on the recovery
	// affordance immediately. Skip the initial mount so we don't steal
	// focus during normal app startup (where "connecting" is the
	// expected initial state).
	useEffect(() => {
		if (status === "disconnected") {
			// EmptyState renders the action as a <Button> — find it via
			// the data-testid we set on the container.
			const btn = document.querySelector<HTMLButtonElement>(
				'[data-testid="connection-status"] button',
			);
			btn?.focus();
		}
	}, [status]);

	const isConnecting = status === "connecting";
	const isDisconnected = status === "disconnected";
	const isRestarting = status === "restarting";

	// State-aware title so the user can tell apart "still starting"
	// from "crashed and waiting for retry". The keys are localised in
	// every locale JSON (en/ar/de/es/fr/hi/ru/zh) under the `app.*`
	// namespace.
	const title = isConnecting
		? t("app.startingBackend")
		: status === "restarting"
			? t("app.restartingBackend")
			: t("app.lostConnection");

	// For disconnected: surface the raw error verbatim when present
	// (it's the most actionable signal — e.g. "Python process exited
	// with code 137"), otherwise fall back to the generic
	// `lostConnectionHint`. For connecting / restarting: show the
	// generic "this usually takes a few seconds" hint.
	const description = isDisconnected
		? (lastError ?? t("app.lostConnectionHint"))
		: t("app.restartingHint");

	const progressPercent =
		typeof connectingProgress === "number"
			? Math.min(100, Math.max(0, Math.round(connectingProgress)))
			: null;

	return (
		<div
			className="mx-auto flex min-h-full w-full max-w-lg flex-col items-center justify-center px-6 py-12"
			data-testid="connection-status"
		>
			<EmptyState
				variant="error"
				icon={AlertCircleIcon}
				title={title}
				// The primary EmptyState action is scoped to the
				// disconnected state only. During restarting the backend
				// is auto-recovering, so the sole manual affordance is the
				// dedicated "Force Retry" button below — showing both
				// would render two same-handler retry buttons.
				actionLabel={isDisconnected ? t("app.retryConnection") : undefined}
				onAction={isDisconnected ? onRetry : undefined}
				actionIcon={RefreshIcon}
			>
				{/* The description doubles as THE polite live region for this
				 * screen (role="status" ⇒ implicit aria-live="polite"). The
				 * wrapper div is intentionally ROLELESS: a role="alert"
				 * wrapper around the whole card made every progressbar tick
				 * re-announce the entire card assertively, drowning SR users
				 * in repeated announcements. With only this polite region,
				 * description updates (e.g. error ↔ hint swaps) announce
				 * once, calmly, while the progressbar below reports its own
				 * value changes via aria-valuenow.
				 */}
				<p role="status" className="text-xs text-(--text-muted)">
					{description}
				</p>
				{(isConnecting || isRestarting) && (
					<div className="flex w-full flex-col items-center gap-3">
						{/*the Spinner default is now a non-live
						 * <span role="img">. This screen is the ONE place
						 * where we DO want the loading state announced as
						 * a polite live region (so SR users hear "Loading"
						 * when the backend is starting, since that is the
						 * primary status message here). Wrap the Spinner
						 * in an <output aria-live="polite"> to restore the
						 * implicit aria-live region the Spinner used to
						 * provide by default. */}
						<output
							aria-live="polite"
							aria-label={t("a11y.loading")}
							className="flex items-center justify-center"
						>
							<Spinner />
						</output>
						{progressPercent !== null && (
							<div className="flex w-full max-w-xs flex-col items-center gap-1">
								<span className="text-xs text-(--text-muted)">
									{progressPercent}%
								</span>
								<div
									className="h-1.5 w-full overflow-hidden rounded-full bg-(--bg-subtle)"
									role="progressbar"
									aria-valuenow={progressPercent}
									aria-valuemin={0}
									aria-valuemax={100}
									aria-label={t("app.retryConnection")}
								>
									<div
										className="h-full bg-primary transition-[width] duration-300 ease-out"
										style={{
											width: `${progressPercent}%`,
										}}
									/>
								</div>
							</div>
						)}
						{/*secondary "Force retry" affordance for the restarting
						 * state. The backend auto-recovers on its own, but a
						 * stuck 60s safety timer can leave the screen frozen;
						 * this button short-circuits the wait and immediately
						 * retries the connection. Exposed via a stable testid
						 * so integration tests can find it without relying on
						 * localized label text. */}
						{isRestarting && (
							<Button
								type="button"
								variant="outline"
								size="sm"
								data-testid="connection-status-force-retry"
								onClick={onRetry}
								className="gap-2"
							>
								<HugeiconsIcon
									icon={RefreshIcon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{t("app.forceRetry")}
							</Button>
						)}
					</div>
				)}
			</EmptyState>
		</div>
	);
}
