/**
 * Minimal main-process i18n bundle.
 *
 * The main process has no React/i18next. We bundle ~10 dialog
 * strings here so native Electron dialogs (single-instance error, critical
 * error, model folder picker, export save-as dialogs) can be localized in
 * the same language the user picked in the renderer.
 *
 * Locale sync:
 *   - The renderer persists its locale to `localStorage["voice-typer-ui-locale"]`
 *     (see src/renderer/src/i18n/i18n.ts). The renderer cannot read main's
 *     memory, so it pushes its locale via an `i18n:set-locale` IPC channel.
 *     The handler in `main/ipc/window-handlers.ts` invokes
 *     {@link setMainLocale}, which reassigns {@link currentLocale} so that
 *     native main-process UI (tray tooltips, OS notifications routed
 *     through main) can be localized in the user's chosen language.
 *
 * The bundle covers all 8 locales that the renderer ships
 * (en, es, ar, de, fr, hi, ru, zh). Adding a new locale requires:
 *   1. Adding a new entry to {@link MAIN_STRINGS} below.
 *   2. Adding the locale to the renderer's `SUPPORTED_LOCALES` in
 *      `src/renderer/src/i18n/i18n.ts`.
 *
 * The shape of every locale entry MUST match the `en` entry (same keys).
 * TypeScript enforces this via the `MainStrings` mapped type.
 *
 * Dead-code cleanup: `getMainLocale()` and the
 * `export` modifier on the `MainLocale` type were removed — no consumer
 * outside this module ever imported either. `MainLocale` stays as a
 * module-local type alias so internal references (`currentLocale`)
 * remain typed.
 *
 * `setMainLocale()` was re-added and is invoked by the
 * `i18n:set-locale` IPC handler in `main/ipc/window-handlers.ts`. The
 * handler pushes locale changes from the renderer to the main process
 * so that native main-process UI (tray tooltips, OS notifications
 * routed through main) can be localized. `currentLocale` is `let`
 * because `setMainLocale` reassigns it. `MAIN_STRINGS` is still read by
 * `mainT()` (used by the `model:import-dialog` handler).
 *
 * Fallback chain: when the renderer pushes a locale, {@link setMainLocale}
 * resolves it against MAIN_STRINGS in this order:
 *
 *   1. `currentLocale` — exact match (e.g. `"zh"`, `"ar"`).
 *   2. `primary subtag` — when the pushed locale is a regional variant
 *      (contains `-`), try the bare primary subtag (e.g. `"zh-CN"` →
 *      `"zh"`). This mirrors the renderer's `t()` lookup chain so a
 *      regional UI locale that hasn't yet been registered in
 *      MAIN_STRINGS falls back to its parent language instead of
 *      English — e.g. a user on `"zh-CN"` sees Chinese dialogs (with
 *      English fallback only for keys the Chinese table lacks), not
 *      English dialogs outright.
 *   3. `"en"` — the universal fallback when neither step resolves.
 *      `mainT()` then looks up the key against the resolved locale's
 *      table, then against `MAIN_STRINGS.en`, then returns the raw key
 *      (defensive — should never happen for keys declared in
 *      {@link MAIN_STRINGS.en}).
 *
 * Inline-string gap (Low, tracked): the 8 locales × 8 dialog keys = 64
 * inline string literals below duplicate the renderer's translation
 * pipeline (which loads JSON from `src/renderer/src/i18n/translations/`).
 * Migrating main-process strings to JSON files (mirroring the renderer
 * schema) would let translators use the same tooling and prevent drift
 * between the two bundles. Out of scope for the current task — flagged
 * here so a future contributor can pick it up.
 */

import { APP_NAME } from "./branding";

/**
 * The set of locales that ship dialog strings for the main process.
 * Must stay in sync with the renderer's `SUPPORTED_LOCALES`.
 */
const MAIN_STRINGS = {
	ar: {
		"dialog.criticalError.body":
			"واجه التطبيق {count} استثناءات غير معالجة وسيتم إغلاقه.\nسجل الأعطال: {logPath}\nيرجى إعادة تشغيل Voice Typer.",
		"dialog.criticalError.title": "Voice Typer — خطأ حرج",
		"dialog.export.config": "تصدير الإعدادات",
		"dialog.export.history": "تصدير السجل",
		"dialog.export.templates": "تصدير القوالب",
		"dialog.export.vocabulary": "تصدير المفردات",
		"dialog.selectModelFolder.title": "اختيار مجلد النماذج",
		"dialog.singleInstance.message":
			"يمكن تشغيل نسخة واحدة فقط من Voice Typer في كل مرة.\n\nأغلق النسخة الموجودة أولاً ثم حاول مرة أخرى.",
		"dialog.singleInstance.title": APP_NAME,
	},
	de: {
		"dialog.criticalError.body":
			"Die App ist auf {count} nicht abgefangene Ausnahmen gestoßen und wird beendet.\nAbsturzprotokoll: {logPath}\nBitte starten Sie Voice Typer neu.",
		"dialog.criticalError.title": "Voice Typer — Kritischer Fehler",
		"dialog.export.config": "Konfiguration exportieren",
		"dialog.export.history": "Verlauf exportieren",
		"dialog.export.templates": "Vorlagen exportieren",
		"dialog.export.vocabulary": "Vokabular exportieren",
		"dialog.selectModelFolder.title": "Modellordner auswählen",
		"dialog.singleInstance.message":
			"Es kann nur eine Instanz von Voice Typer gleichzeitig ausgeführt werden.\n\nSchließen Sie zuerst die bestehende Instanz und versuchen Sie es erneut.",
		"dialog.singleInstance.title": APP_NAME,
	},
	en: {
		"dialog.criticalError.body":
			"The app encountered {count} uncaught exceptions and will exit.\nCrash log: {logPath}\nPlease restart Voice Typer.",
		"dialog.criticalError.title": "Voice Typer — Critical Error",
		"dialog.export.config": "Export Configuration",
		"dialog.export.history": "Export History",
		"dialog.export.templates": "Export Templates",
		"dialog.export.vocabulary": "Export Vocabulary",
		"dialog.selectModelFolder.title": "Select Model Folder",
		"dialog.singleInstance.message":
			"Only one instance of Voice Typer can run at a time.\n\nClose the existing instance first, then try again.",
		"dialog.singleInstance.title": APP_NAME,
	},
	es: {
		"dialog.criticalError.body":
			"La aplicación encontró {count} excepciones no capturadas y se cerrará.\nRegistro de errores: {logPath}\nPor favor, reinicia Voice Typer.",
		"dialog.criticalError.title": "Voice Typer — Error crítico",
		"dialog.export.config": "Exportar configuración",
		"dialog.export.history": "Exportar historial",
		"dialog.export.templates": "Exportar plantillas",
		"dialog.export.vocabulary": "Exportar vocabulario",
		"dialog.selectModelFolder.title": "Seleccionar carpeta de modelos",
		"dialog.singleInstance.message":
			"Solo se puede ejecutar una instancia de Voice Typer a la vez.\n\nCierra la instancia existente primero e inténtalo de nuevo.",
		"dialog.singleInstance.title": APP_NAME,
	},
	fr: {
		"dialog.criticalError.body":
			"L'application a rencontré {count} exceptions non interceptées et va se fermer.\nJournal d'incidents : {logPath}\nVeuillez redémarrer Voice Typer.",
		"dialog.criticalError.title": "Voice Typer — Erreur critique",
		"dialog.export.config": "Exporter la configuration",
		"dialog.export.history": "Exporter l'historique",
		"dialog.export.templates": "Exporter les modèles",
		"dialog.export.vocabulary": "Exporter le vocabulaire",
		"dialog.selectModelFolder.title": "Sélectionner le dossier de modèles",
		"dialog.singleInstance.message":
			"Une seule instance de Voice Typer peut être exécutée à la fois.\n\nFermez d'abord l'instance existante, puis réessayez.",
		"dialog.singleInstance.title": APP_NAME,
	},
	hi: {
		"dialog.criticalError.body":
			"ऐप को {count} अनकैप्चर्ड अपवादों का सामना करना पड़ा और यह बंद हो जाएगा।\nक्रैश लॉग: {logPath}\nकृपया Voice Typer पुनः आरंभ करें।",
		"dialog.criticalError.title": "Voice Typer — गंभीर त्रुटि",
		"dialog.export.config": "कॉन्फ़िगरेशन निर्यात करें",
		"dialog.export.history": "इतिहास निर्यात करें",
		"dialog.export.templates": "टेम्पलेट निर्यात करें",
		"dialog.export.vocabulary": "शब्दावली निर्यात करें",
		"dialog.selectModelFolder.title": "मॉडल फ़ोल्डर चुनें",
		"dialog.singleInstance.message":
			"एक समय में Voice Typer की केवल एक ही इंस्टेंस चल सकती है।\n\nपहले मौजूदा इंस्टेंस बंद करें, फिर पुनः प्रयास करें।",
		"dialog.singleInstance.title": APP_NAME,
	},
	ru: {
		"dialog.criticalError.body":
			"Приложение столкнулось с {count} необработанными исключениями и будет закрыто.\nЖурнал сбоев: {logPath}\nПожалуйста, перезапустите Voice Typer.",
		"dialog.criticalError.title": "Voice Typer — Критическая ошибка",
		"dialog.export.config": "Экспортировать конфигурацию",
		"dialog.export.history": "Экспортировать историю",
		"dialog.export.templates": "Экспортировать шаблоны",
		"dialog.export.vocabulary": "Экспортировать словарь",
		"dialog.selectModelFolder.title": "Выберите папку моделей",
		"dialog.singleInstance.message":
			"Одновременно может быть запущен только один экземпляр Voice Typer.\n\nСначала закройте существующий экземпляр, затем попробуйте снова.",
		"dialog.singleInstance.title": APP_NAME,
	},
	zh: {
		"dialog.criticalError.body":
			"应用遇到 {count} 个未捕获的异常，即将退出。\n崩溃日志：{logPath}\n请重新启动 Voice Typer。",
		"dialog.criticalError.title": "Voice Typer — 严重错误",
		"dialog.export.config": "导出配置",
		"dialog.export.history": "导出历史记录",
		"dialog.export.templates": "导出模板",
		"dialog.export.vocabulary": "导出词汇表",
		"dialog.selectModelFolder.title": "选择模型文件夹",
		"dialog.singleInstance.message":
			"同一时间只能运行一个 Voice Typer 实例。\n\n请先关闭已有实例，然后重试。",
		"dialog.singleInstance.title": APP_NAME,
	},
} as const;

type MainLocale = keyof typeof MAIN_STRINGS;

/** English reference keys — every locale must provide exactly these keys. */
type MainStrings = typeof MAIN_STRINGS.en;
type MainStringsKey = keyof MainStrings;

/**
 * The locale used by {@link mainT} for subsequent lookups.
 * Reassigned by {@link setMainLocale} when the renderer pushes its
 * locale via the `i18n:set-locale` IPC channel. Defaults to
 * `"en"` until the first sync.
 */
let currentLocale: MainLocale = "en";

/**
 * Update the main-process locale from the renderer's locale
 * selection. Called by the `i18n:set-locale` IPC handler in
 * `window-handlers.ts` whenever the user changes the UI language.
 *
 * Resolution chain (mirrors the renderer's `t()` lookup chain):
 *
 *   1. Exact match — if the pushed locale is directly registered in
 *      {@link MAIN_STRINGS} (e.g. `"zh"`, `"ar"`), use it as-is.
 *   2. Primary subtag — if the pushed locale is a regional variant
 *      (contains `-`) and not directly registered, try the bare
 *      primary subtag (e.g. `"zh-CN"` → `"zh"`). This lets a regional
 *      UI locale fall back to its parent language instead of English
 *      when MAIN_STRINGS hasn't yet been extended for the regional
 *      variant.
 *   3. English fallback — if neither step resolves, fall back to
 *      `"en"` and emit a console warning so the missing locale is
 *      visible during development. The user still gets English dialogs
 *      rather than a crash.
 */
export function setMainLocale(locale: string): void {
	if (locale in MAIN_STRINGS) {
		currentLocale = locale as MainLocale;
		return;
	}
	// Primary-subtag fallback for regional variants (e.g. "zh-CN" → "zh",
	// "pt-BR" → "pt"). Mirrors the renderer's `t()` lookup chain so the
	// main process picks the parent language rather than jumping straight
	// to English when the regional variant hasn't been registered.
	if (locale.includes("-")) {
		const primary = locale.split("-")[0];
		if (primary in MAIN_STRINGS) {
			currentLocale = primary as MainLocale;
			return;
		}
	}
	console.warn(
		`[i18n] setMainLocale: unknown locale "${locale}" — falling back to "en". ` +
			`Add dialog strings for this locale (or its primary subtag) to MAIN_STRINGS in main/i18n.ts.`,
	);
	currentLocale = "en";
}

/**
 * Translate a dialog key for the current main-process locale.
 *
 * Lookup chain: `MAIN_STRINGS[currentLocale]` → `MAIN_STRINGS.en` →
 * raw key. The locale itself was already normalized by
 * {@link setMainLocale} (regional variant → primary subtag → en), so
 * `currentLocale` here is always one of the keys present in
 * MAIN_STRINGS — the `table?.[...]` access therefore always resolves,
 * and the English fallback is only a defensive guard against a key
 * that was somehow declared outside {@link MainStringsKey}.
 *
 * Placeholders: `{name}` is replaced with `fmt.name` if provided. Missing
 * interpolation args are left as the literal `{name}` so the bug is
 * visible during testing instead of silently dropping data.
 */
export function mainT(
	key: string,
	fmt?: Record<string, string | number>,
): string {
	const table = MAIN_STRINGS[currentLocale] as MainStrings | undefined;
	const en = MAIN_STRINGS.en;
	const raw =
		(table?.[key as MainStringsKey] as string | undefined) ??
		(en[key as MainStringsKey] as string | undefined) ??
		key;
	if (!fmt) return raw;
	return raw.replace(/\{(\w+)\}/g, (_, k: string) =>
		Object.hasOwn(fmt, k) ? String(fmt[k]) : `{${k}}`,
	);
}
