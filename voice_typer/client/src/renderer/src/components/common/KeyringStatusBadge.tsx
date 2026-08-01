// KeyringStatusBadge — shows a small lock icon + tooltip when
// secrets are stored in the OS keychain, or a warning badge when only
// the plaintext fallback (config.json with 0o600 perms) is available.
//
// Used next to API key inputs in:
//   - pages/Models.tsx (OpenAI / Groq / Deepgram cloud provider cards)
//   - components/settings/ModelSettingsSection.tsx (LLM polish API key)
//
// The status comes from the backend's `get_config` response, where the
// service layer attaches a `keyring_status` field (see
// voice_typer/server/service.py:get_config). When the field is absent
// (legacy responses, or the credential_store probe failed), we treat
// it as "fallback" — same as a missing keyring backend — so the user
// always sees a truthful indicator.

import { Alert02Icon, LockKeyIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { t } from "@/i18n/i18n";
import type { KeyringStatus } from "@/types/config";

interface KeyringStatusBadgeProps {
	/** From get_config response. Undefined = legacy/unknown = fallback. */
	status?: KeyringStatus;
	/** Compact variant: just the icon, no text label (for inline use). */
	compact?: boolean;
}

export function KeyringStatusBadge({
	status,
	compact = false,
}: KeyringStatusBadgeProps) {
	// Treat absent status the same as "fallback" — never claim
	// keyring is available when we don't know.
	const available = status?.available === true;
	const backend = status?.backend ?? null;
	const reason = status?.reason ?? null;

	// Use a real <button> (with appearance-none to look like the
	// previous span) so keyboard + screen-reader users can focus the badge
	// via Tab. Radix Tooltip opens on focus by default, exposing the
	// status text without a mouse interaction.
	const buttonBaseClass =
		"inline-flex items-center appearance-none border-0 bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 cursor-default";

	if (available) {
		const tooltipText = backend
			? t("settings.keyring.availableWithBackend", { backend })
			: t("settings.keyring.available");
		return (
			<Tooltip>
				<TooltipTrigger asChild>
					<button
						type="button"
						className={
							compact
								? `${buttonBaseClass} text-emerald-600 dark:text-emerald-400 rounded-full`
								: `${buttonBaseClass} gap-1.5 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300`
						}
						// Don't duplicate the tooltip text as aria-label (SR users
						// would hear it twice — once on the button, once when the tooltip
						// opens on focus). In non-compact mode the visible "Secure" text
						// provides the accessible name. In compact mode (icon-only) we
						// expose a short generic label so the button still has an
						// accessible name.
						aria-label={compact ? t("settings.keyring.statusLabel") : undefined}
					>
						<HugeiconsIcon
							icon={LockKeyIcon}
							strokeWidth={2}
							className="h-3.5 w-3.5"
						/>
						{!compact && <span>{t("settings.keyring.secure")}</span>}
					</button>
				</TooltipTrigger>
				<TooltipContent side="top" align="center" className="max-w-72">
					{tooltipText}
				</TooltipContent>
			</Tooltip>
		);
	}

	// Fallback: plaintext in config.json with 0o600 perms.
	const tooltipText = reason
		? t("settings.keyring.fallbackWithReason", { reason })
		: t("settings.keyring.fallback");
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<button
					type="button"
					className={
						compact
							? `${buttonBaseClass} text-amber-600 dark:text-amber-400 rounded-full`
							: `${buttonBaseClass} gap-1.5 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300`
					}
					// see available branch above for the rationale on
					// dropping the tooltipText aria-label.
					aria-label={compact ? t("settings.keyring.statusLabel") : undefined}
				>
					<HugeiconsIcon
						icon={Alert02Icon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{!compact && <span>{t("settings.keyring.plaintext")}</span>}
				</button>
			</TooltipTrigger>
			<TooltipContent side="top" align="center" className="max-w-72">
				{tooltipText}
			</TooltipContent>
		</Tooltip>
	);
}
