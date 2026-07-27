// types/ipc/index.ts
//
// Barrel re-export for the IPC type catalog.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7)
// into 9 domain-specific modules. This barrel preserves the
// `import { X } from "@/types/ipc"` API surface that 38+ renderer files
// rely on — every name previously exported from `types/ipc.ts` is
// re-exported here unchanged. New code is encouraged to import directly
// from the domain module (`@/types/ipc/enums`, `@/types/ipc/push_events`,
// etc.) so the type graph stays minimal.
//
// Module map:
//   - ./enums         — RecordingState, Page, ErrorCodes
//   - ./history       — HistoryRecord, TodayStats, HistoryCountData,
//                       TranscriptionTextData
//   - ./push_events   — all *Event interfaces + PythonPushEvent union
//   - ./requests      — all *Request interfaces + PythonRequest union
//   - ./vocabulary    — VocabularyData, VocabularyEntry
//   - ./bridge        — PythonBridge, WindowBridge
//   - ./bubble_bridge — MainRendererBubbleMutators, BubbleEventSubscriptions,
//                       BubbleWindowExtras, BubbleWindowBubble + the
//                       `declare global { Window }` augmentation
//   - ./model_status  — ModelStatusEntry, ModelStatusMap, DiskInfo
//   - ./permissions   — PermissionsResult, AutostartStatus,
//                       MicrophonePermissionResult
//
// No behaviour change vs. the original file — pure structural refactor.

export * from "./bridge";
export * from "./bubble_bridge";
export * from "./enums";
export * from "./history";
export * from "./model_status";
export * from "./permissions";
export * from "./push_events";
export * from "./requests";
export * from "./vocabulary";
