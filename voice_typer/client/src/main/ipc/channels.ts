// Single source of truth for IPC channel names.
//
// All `ipcMain.on` / `ipcMain.handle` and `ipcRenderer.invoke` /
// `ipcRenderer.send` calls must reference these constants — not bare
// string literals. The preload scripts (`preload/_bubble-channels.ts`,
// `preload/index.ts`) re-export the subsets they need so the renderer
// side stays in sync.
//
//previously each main-process handler module and each preload
// module declared its own bare string literal for the same channel
// (`"bubble:level"`, `"python-call"`, …), so a rename had to be
// applied in 3-5 places. Centralising here means a rename is a
// single-line edit + `tsc` will flag every call site that needs the
// new constant name.

export const WindowChannels = {
	minimize: "window:minimize",
	toggleMaximize: "window:toggle-maximize",
	close: "window:close",
	isMaximized: "window:is-maximized",
	maximizedChanged: "window:maximized-changed",
	openLogs: "window:open-logs",
} as const;

export const PythonChannels = {
	call: "python-call",
	event: "python-event",
} as const;

export const ExportChannels = {
	history: "history:export",
	vocabulary: "vocabulary:export",
	templates: "templates:export",
	config: "config:export",
} as const;

export const I18nChannels = {
	setLocale: "i18n:set-locale",
} as const;

export const ModelChannels = {
	importDialog: "model:import-dialog",
} as const;

export const RendererChannels = {
	logError: "renderer:log-error",
} as const;

export const BubbleChannels = {
	level: "bubble:level",
	show: "bubble:show",
	showFromRenderer: "bubble:show-from-renderer",
	hide: "bubble:hide",
	hidden: "bubble:hidden",
	draggable: "bubble:draggable",
	ready: "bubble:ready",
	moveBy: "bubble:move-by",
	setPosition: "bubble:set-position",
	resize: "bubble:resize",
	setState: "bubble:set-state",
	config: "bubble:config",
	toggleDictation: "bubble:toggle-dictation",
	dismiss: "bubble:dismiss",
	localeChanged: "bubble:locale-changed",
} as const;
