// useHistoryIntegrityToast, surfaces the history-database integrity
// push events ``history_corrupted`` and ``history_fts5_rebuild_failed``.
//
// ``history_corrupted``: the persistence layer detected a corrupt
// history DB on open, quarantined the file (kept beside the database),
// and rebuilt a fresh DB from the salvage dump. The user must be told
// their history survived only partially, without this toast the
// recovery is invisible and "missing" recent entries look like a bug.
//
// ``history_fts5_rebuild_failed``: after a delete/clear the FTS5
// full-text index could not be rebuilt, the PRIVACY guarantee is
// broken: entries the user deleted may still be recoverable/searchable
// in the database file. This is a warning the user must see so they
// can retry the deletion; it fires on real rebuild failure evidence,
// not proactively.
//
// Both events are rare (once per corruption / per failed rebuild), so
// these toasts use a fixed sonner id and NO cooldown window: each
// emission is a genuine, distinct integrity event the user should see.
// A repeated failure replaces the in-flight toast (fixed id) instead
// of stacking.

import { usePythonEvent } from "@/hooks/usePython";
import type { TranslateFn } from "@/i18n/translate-types";
import { useDegradationToastStore } from "@/stores/degradationToastStore";
import { SNACKBAR_DEFAULT_DURATION_MS, useSnackbar } from "./useSnackbar";

/** Sonner id of the corruption-recovery toast. */
const HISTORY_CORRUPTED_TOAST_ID = "history-corrupted";

/** Sonner id of the FTS5-rebuild-failure toast. */
const HISTORY_FTS_REBUILD_FAILED_TOAST_ID = "history-fts5-rebuild-failed";

/**
 * Subscribe to the history-DB integrity push events and show their
 * warnings. Call once at the top level of a component (App wires it
 * with the i18n `t` function).
 *
 * @param t i18n translate function (from useT).
 */
export function useHistoryIntegrityToast(t: TranslateFn): void {
	const { showSnack } = useSnackbar();

	usePythonEvent("history_corrupted", (data): (() => void) | undefined => {
		const payload = (data ?? {}) as { recovered_count?: unknown };
		const recoveredCount =
			typeof payload.recovered_count === "number" &&
			payload.recovered_count >= 0
				? payload.recovered_count
				: 0;

		useDegradationToastStore.getState().setLastAnyToastShownAt(Date.now());

		showSnack(t("degradation.historyCorrupted"), "warning", {
			id: HISTORY_CORRUPTED_TOAST_ID,
			description: t("degradation.historyCorruptedHint", {
				count: String(recoveredCount),
			}),
			duration: SNACKBAR_DEFAULT_DURATION_MS.error,
		});
		return undefined;
	});

	usePythonEvent(
		"history_fts5_rebuild_failed",
		(): (() => void) | undefined => {
			useDegradationToastStore.getState().setLastAnyToastShownAt(Date.now());

			showSnack(t("degradation.historyFtsRebuildFailed"), "warning", {
				id: HISTORY_FTS_REBUILD_FAILED_TOAST_ID,
				description: t("degradation.historyFtsRebuildFailedHint"),
				duration: SNACKBAR_DEFAULT_DURATION_MS.error,
			});
			return undefined;
		},
	);
}
