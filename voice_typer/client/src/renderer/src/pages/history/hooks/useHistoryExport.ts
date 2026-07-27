// History export hook.
//
// Owns:
//   - ``doExport`` (filter-aware paging loop — pages through
//     ``get_history`` / ``get_favorites`` / ``search_history`` until
//     the backend returns an empty page or the row cap is hit,
//     aggregates the rows, then invokes the preload bridge
//     ``window.window_.exportHistory(rows, format)`` so the Electron
//     main process / Tauri Rust host can show a native save dialog
//     and write the file with SEC-015 CSV formula-injection defense).
//
// BG-52: when a filter is active (``searchQuery`` non-empty OR
// ``favoritesOnly`` true), the export pages through the matching
// endpoint (``search_history`` / ``get_favorites``) and fires an
// info toast so the user knows the exported file reflects the
// active filter, not the full history.
//
// Extracted from the former monolithic ``pages/History.tsx`` render
// function as part of the BG-54 spaghetti split. The cache + IPC
// lifecycle lives in ``useHistoryCache``; the client-side sort lives
// in ``historySort.ts``.

import { useCallback } from "react";
import { toast } from "sonner";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

import { sortRecords, type HistorySortOrder } from "../utils/historySort";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

// Page size for the export paging loop. Smaller than the cache page
// size so the loop yields to the event loop more often (the export
// can pull thousands of rows; we don't want to block the UI thread
// on a single 1000-row JSON.parse).
const EXPORT_PAGE_SIZE = 100;

// Hard cap on total rows exported in a single doExport call. Matches
// the backend's frame cap (200 rows per page) × a reasonable upper
// bound on what a user could reasonably want to export. Prevents a
// runaway export from OOM-ing the renderer.
const EXPORT_MAX_ROWS = 10000;

interface UseHistoryExportParams {
        call: CallFn;
        records: HistoryRecord[];
        sortOrder: HistorySortOrder;
        searchQuery: string;
        favoritesOnly: boolean;
}

interface UseHistoryExportReturn {
        doExport: (format: string) => Promise<void>;
}

export function useHistoryExport({
        call,
        records,
        sortOrder,
        searchQuery,
        favoritesOnly,
}: UseHistoryExportParams): UseHistoryExportReturn {
        const doExport = useCallback(
                async (format: string) => {
                        const fmt: "json" | "csv" =
                                format === "csv" ? "csv" : "json";
                        // BG-52: when a filter is active (``searchQuery`` non-empty OR
                        // ``favoritesOnly`` true), the export pages through the matching
                        // endpoint (``search_history`` / ``get_favorites``). When no
                        // filter is active, it pages through ``get_history``.
                        const filterActive =
                                searchQuery.trim() !== "" || favoritesOnly;

                        // Surface an info toast so the user knows the exported file
                        // reflects the active filter (not the full history).
                        if (filterActive) {
                                toast.info(t("history.exportFilteredToast"));
                        }

                        let allRecords: HistoryRecord[];
                        try {
                                // Page through the matching endpoint until the backend
                                // returns an empty page (or a partial page — no more rows)
                                // or we hit the EXPORT_MAX_ROWS cap.
                                allRecords = [];
                                let offset = 0;
                                // eslint-disable-next-line no-constant-condition
                                while (true) {
                                        let page: HistoryRecord[];
                                        if (favoritesOnly) {
                                                page = await call<HistoryRecord[]>(
                                                        "get_favorites",
                                                        { limit: EXPORT_PAGE_SIZE, offset },
                                                );
                                        } else if (searchQuery.trim() !== "") {
                                                page = await call<HistoryRecord[]>(
                                                        "search_history",
                                                        { query: searchQuery, limit: EXPORT_PAGE_SIZE, offset },
                                                );
                                        } else {
                                                page = await call<HistoryRecord[]>("get_history", {
                                                        limit: EXPORT_PAGE_SIZE,
                                                        offset,
                                                });
                                        }
                                        const safePage = Array.isArray(page) ? page : [];
                                        if (safePage.length === 0) break;
                                        allRecords.push(...safePage);
                                        offset += safePage.length;
                                        if (allRecords.length >= EXPORT_MAX_ROWS) {
                                                allRecords = allRecords.slice(0, EXPORT_MAX_ROWS);
                                                toast.warning(
                                                        t("history.exportTruncatedWarning", {
                                                                count: String(EXPORT_MAX_ROWS),
                                                        }),
                                                );
                                                break;
                                        }
                                        // Backend returned a partial page — no more rows.
                                        if (safePage.length < EXPORT_PAGE_SIZE) break;
                                }
                        } catch (err) {
                                console.error("[History] export paging failed:", err);
                                toast.error(t("history.exportFailed"));
                                return;
                        }

                        if (allRecords.length === 0) {
                                toast.warning(t("history.exportEmpty"));
                                return;
                        }

                        // Apply the same client-side sort the page uses so the
                        // exported file order matches what the user sees.
                        const sorted = sortRecords(allRecords, sortOrder);

                        const bridge = window.window_;
                        if (!bridge?.exportHistory) {
                                toast.error(t("history.exportFailed"));
                                return;
                        }
                        try {
                                const result = await bridge.exportHistory(
                                        sorted as unknown as Record<string, unknown>[],
                                        fmt,
                                );
                                if (result.success) {
                                        const path = result.path ?? "";
                                        const filename =
                                                path.split(/[\\/]/).pop() || "untitled";
                                        toast.success(
                                                t("history.exportSaved", { filename }),
                                        );
                                } else {
                                        toast.error(
                                                result.error ?? t("history.exportFailed"),
                                        );
                                }
                        } catch (err) {
                                console.error("[History] export bridge failed:", err);
                                toast.error(t("history.exportFailed"));
                        }
                },
                [call, records, sortOrder, searchQuery, favoritesOnly],
        );

        return { doExport };
}
