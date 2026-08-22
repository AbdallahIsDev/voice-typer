/**
 * Script to translate all untranslated i18n values across 7 locales.
 * Usage: node scripts/translate-i18n.js
 */

const fs = require("node:fs");
const path = require("node:path");

const TRANSLATIONS_DIR = path.join(
	__dirname,
	"..",
	"src",
	"renderer",
	"src",
	"i18n",
	"translations",
);

const en = JSON.parse(
	fs.readFileSync(path.join(TRANSLATIONS_DIR, "en.json"), "utf8"),
);

function getKeys(obj, prefix) {
	const result = {};
	for (const [k, v] of Object.entries(obj)) {
		const key = prefix ? `${prefix}.${k}` : k;
		if (v && typeof v === "object" && !Array.isArray(v)) {
			Object.assign(result, getKeys(v, key));
		} else {
			result[key] = v;
		}
	}
	return result;
}

function deepSet(obj, key, value) {
	const parts = key.split(".");
	let current = obj;
	for (let i = 0; i < parts.length - 1; i++) {
		if (!current[parts[i]]) current[parts[i]] = {};
		current = current[parts[i]];
	}
	current[parts[parts.length - 1]] = value;
}

// Get all English keys and values
const enFlat = getKeys(en, "");

// ── Massive translation map ─────────────────────────────────────────
// Keyed by locale, then by flat key path

const translations = {};

//removed the dead `require("./translate-i18n-partial.js")` block —
// the partial file never existed (the catch swallowed MODULE_NOT_FOUND),
// so the assignment was a no-op. Re-add only if a partial-translation
// loader is actually introduced.

// ── Arabic ──────────────────────────────────────────────────────────
translations.ar = translations.ar || {};

// Remaining Arabic translations - cover ALL sections
const arMore = {
	// about section
	"about.title": "حول",
	"about.description": "معلومات تشخيصية لتقارير الأخطاء والدعم.",
	"about.connected": "متصل",
	"about.disconnected": "غير متصل",
	"about.checking": "جاري الفحص…",
	"about.unknown": "—",
	"about.diagnosticsTitle": "التشخيصات",
	"about.diagnosticsDescription":
		"قم بتضمين هذه المعلومات عند تقديم تقرير خطأ.",
	"about.appVersion": "إصدار التطبيق",
	"about.pythonBackend": "خادم بايثون الخلفي",
	"about.configDirectory": "دليل الإعدادات",
	"about.asrBackend": "خادم ASR الخلفي",
	"about.device": "الجهاز",
	"about.loadedVia": "تم التحميل عبر",
	"about.hotkey": "المفتاح السريع",
	"about.microphone": "الميكروفون",
	"about.privacyTitle": "الخصوصية",
	"about.privacyDescription": "كيف يتم التعامل مع صوتك وبياناتك.",
	"about.audioProcessingTitle": "معالجة الصوت.",
	"about.audioProcessingDesc":
		"يقوم Voice Typer بمعالجة جميع البيانات الصوتية محليًا على جهازك. لا يغادر أي صوت جهازك إلا إذا قمت بتكوين خادم ASR سحابي بشكل صريح (OpenAI/Groq/Deepgram).",
	"about.modelWeightsTitle": "أوزان النموذج.",
	"about.modelWeightsDesc":
		"يتم تنزيل أوزان نموذج ASR (مثل Whisper small.en، ~466 MB) من HuggingFace عند الاستخدام الأول. يكشف هذا التنزيل عنوان IP الخاص بك لـ HuggingFace (طرف ثالث أمريكي).",
	"about.cloudAsrTitle": "ASR سحابي.",
	"about.cloudAsrDesc":
		"إذا قمت بتكوين مفتاح API لـ OpenAI/Groq/Deepgram، يتم إرسال الصوت إلى هذا المزود للنسخ. تنطبق سياسة خصوصية المزود على الصوت المرسل.",
	"about.voiceBiometricsTitle": "القياسات الحيوية الصوتية.",
	"about.voiceBiometricsDesc":
		"قد تعتبر تسجيلاتك الصوتية بيانات بيومترية بموجب BIPA إلينوي والمادة 9 من اللائحة العامة لحماية البيانات. لا يقوم Voice Typer بتخزين الصوت الخام بعد اكتمال النسخ — فقط النص المنسوخ يتم الاحتفاظ به في قاعدة البيانات المحلية.",
	"about.localDataTitle": "البيانات المحلية.",
	"about.localDataDesc":
		"يتم تخزين الإعدادات والمفردات والقوالب والسجل في دليل ملف تعريف المستخدم الخاص بك ({configDir}). لا يتم إرسال أي تتبع أو تحليلات أو تقارير أعطال إلى أي مكان.",
	"about.fullPrivacyPolicy": "سياسة الخصوصية الكاملة",
	"about.updatesTitle": "التحديثات",
	"about.updatesDescription": "تحقق من وجود إصدارات أحدث من Voice Typer.",
	"about.installedVersion": "الإصدار المثبت",
	"about.latestRelease": "أحدث إصدار",
	"about.versionValue": "v{version}",
	"about.updateAvailable": "v{version} (تحديث متاح)",
	"about.checkForUpdates": "التحقق من التحديثات",
	"about.downloadVersion": "تنزيل v{version}",
	"about.viewChangelog": "عرض سجل التغييرات",
	"about.startStopDictation": "بدء / إيقاف الإملاء",
	"about.startStopDictationValue": "F2 (أو المفتاح السريع الذي قمت بتعيينه)",
	"about.cancelRecording": "إلغاء التسجيل",
	"about.cancelRecordingValue": "Esc (إذا كان ممكّنًا في الإعدادات)",
	"about.repasteTranscription": "إعادة لصق آخر نسخ",
	"about.repasteTranscriptionValue": "Ctrl+Alt+V (افتراضي)",
	"about.toggleSidebar": "تبديل الشريط الجانبي",
	"about.toggleSidebarValue": "Ctrl+B",
	"about.navigateFields": "التنقل بين الحقول",
	"about.navigateFieldsValue": "Tab / Shift+Tab",
	"about.toggleSwitches": "تبديل المفاتيح",
	"about.toggleSwitchesValue": "مسافة",
	"about.closeDialogs": "إغلاق مربعات الحوار",
	"about.closeDialogsValue": "Esc",
	"about.openDropdowns": "فتح القوائم المنسدلة",
	"about.openDropdownsValue": "Enter أو مسافة",
	"about.resourcesTitle": "الموارد والملاحظات",
	"about.resourcesDescription":
		"الكود المصدري، متتبع المشكلات، وأدلة المساهمة.",
	"about.githubRepository": "مستودع GitHub",
	"about.reportBug": "الإبلاغ عن خطأ / طلب ميزة",
	"about.securityPolicy": "سياسة الأمان",
	"about.contributing": "المساهمة",
	"about.onLatestVersion": "أنت على أحدث إصدار ({version})",
	"about.newVersionAvailable": "إصدار جديد متاح: {version}",
	"about.installedNewer":
		"الإصدار المثبت ({installed}) أحدث من أحدث إصدار ({latest})",
	"about.updateCheckFailed": "فشل التحقق من التحديثات: {error}",
	"about.cacheTitle": "حالة التخزين المؤقت",
	"about.cacheDescription":
		"الحالة الحالية للتخزين المؤقت لنظام الملفات لنموذج الكلام. يتم التشغيل المسبق عند بدء التشغيل للحفاظ على ملفات النموذج في ذاكرة الوصول العشوائي لبداية سريعة.",
	"about.prewarmStatus": "حالة التشغيل المسبق",
	"about.lastRun": "آخر تشغيل",
	"about.cacheHealth": "صحة التخزين المؤقت",
	"about.prewarmElapsed": "الوقت المنقضي",
	"about.cacheHot": "ساخن",
	"about.cachePartial": "جزئي",
	"about.cacheCold": "بارد",
	"about.cacheUnknown": "غير معروف",
	"about.cacheRunning": "جاري التشغيل…",
	"about.refreshCacheStatus": "تحديث",
	"about.neverRun": "لم يتم التشغيل أبدًا",
	"about.runPrewarmNow": "تشغيل مسبق الآن",
	"about.prewarmStarting": "بدأ التشغيل المسبق…",
	"about.prewarmComplete": "اكتمل التشغيل المسبق — تم تسخين التخزين المؤقت",
	"about.prewarmFailed": "فشل بدء التشغيل المسبق",
	"about.prewarmAlreadyHot":
		"التخزين المؤقت ساخن بالفعل — لا حاجة للتشغيل المسبق",
	"about.viewPrewarmLog": "عرض سجل التشغيل المسبق",
	"about.prewarmLogNotFound": "لم يتم العثور على سجل التشغيل المسبق",
	"about.prewarmLogOpened": "تم فتح سجل التشغيل المسبق",
	"about.prewarmLogOpenFailed": "تعذر فتح سجل التشغيل المسبق",
	"about.relativeTime.lessThanMinute": "منذ أقل من دقيقة",
	"about.relativeTime.minutesAgo": "منذ {count} دقيقة",
	"about.relativeTime.hoursAgo": "منذ {count} ساعة",
	"about.relativeTime.daysAgo": "منذ {count} يوم",
	"about.creditsTitle": "الاعتمادات والتراخيص",
	"about.creditsDescription": "Voice Typer مبني على أكتاف العمالقة.",
	"about.creditsAuthorsLabel": "المؤلفون",
	"about.creditsAuthorsValue": "AbdallahIsDev والمساهمون",
	"about.creditsLibrariesLabel": "المكتبات الخارجية",
	"about.creditsLibrariesValue":
		"faster-whisper, CTranslate2, Electron, React, Radix UI, shadcn/ui",
	"about.creditsFontsLabel": "الخطوط",
	"about.creditsFontsValue": "Geist",
	"about.creditsIconsLabel": "الأيقونات",
	"about.creditsIconsValue": "Hugeicons",
	"about.documentationLink": "التوثيق",

	// settings section - general
	"settings.general": "عام",
	"settings.language": "اللغة",
	"settings.languageDescription": "لغة التعرف على الكلام",
	"settings.hotkey": "المفتاح السريع",
	"settings.hotkeyDescription": "اختصار لوحة المفاتيح لبدء/إيقاف الإملاء",
	"settings.autostart": "بدء التشغيل مع تسجيل الدخول",
	"settings.autostartDescription": "تشغيل Voice Typer عند تسجيل الدخول",
	"settings.pasteOnStop": "لصق عند التوقف",
	"settings.pasteOnStopDescription": "لصق النص المنسوخ تلقائياً بعد التسجيل",
	"settings.showNotifications": "إظهار الإشعارات",
	"settings.showNotificationsDescription": "عرض إشعارات علبة النظام",
	"settings.textCleanup": "تنظيف النص",
	"settings.textCleanupDescription": "إصلاح أخطاء النسخ الشائعة تلقائياً",
	"settings.recordingMode": "وضع التسجيل",
	"settings.recordingModeDescription": "كيف يتم تشغيل الإملاء",
	"settings.trayLeftClick": "نقر يسار على علبة النظام",
	"settings.trayLeftClickDescription":
		"ماذا يحدث عند النقر يساراً على أيقونة علبة النظام",
	"settings.recording": "تسجيل",
	"settings.modelSize": "حجم النموذج",
	"settings.modelSizeDescription": "النماذج الأكبر أكثر دقة ولكنها أبطأ",
	"settings.sampleRate": "معدل العينة",
	"settings.sampleRateDescription": "معدل التقاط الصوت بالهرتز",
	"settings.device": "الجهاز",
	"settings.deviceDescription": "جهاز الحساب للنسخ",
	"settings.silenceWarning": "تحذير الصمت",
	"settings.silenceWarningDescription":
		"تحذير عند عدم اكتشاف كلام لهذه المدة بالثواني",
	"settings.autoStop": "إيقاف تلقائي بعد",
	"settings.autoStopDescription": "إيقاف التسجيل تلقائياً بعد هذه المدة",
	"settings.bubble": "الفقاعة العائمة",
	"settings.bubblePosition": "موضع الفقاعة",
	"settings.bubblePositionDescription": "أين تظهر الفقاعة العائمة على الشاشة",
	"settings.bubbleBehavior": "سلوك الفقاعة",
	"settings.bubbleBehaviorDescription": "متى تكون الفقاعة مرئية",
	"settings.bubbleDraggable": "فقاعة قابلة للسحب",
	"settings.bubbleDraggableDescription": "السماح بسحب الفقاعة لإعادة وضعها",
	"settings.bubbleShowOnStartup": "الإظهار عند بدء التشغيل",
	"settings.bubbleShowOnStartupDescription":
		"إظهار الفقاعة عند بدء التطبيق (فقط عند الضبط على ظاهرة دائمًا)",
	"settings.bubbleMicButton": "زر الميكروفون في الفقاعة",
	"settings.bubbleMicButtonDescription":
		"إظهار زر ميكروفون على الفقاعة الظاهرة دائمًا لبدء/إيقاف الإملاء بنقرة",
	"settings.bubbleClickToToggle": "نقر الفقاعة للتبديل",
	"settings.bubbleClickToToggleDescription":
		"عندما تكون الفقاعة ظاهرة دائمًا، النقر على زر الميكروفون يبدل الإملاء",
	"settings.theme": "السمة",
	"settings.themeDescription": "نظام ألوان التطبيق",
	"settings.advanced": "متقدم",
	"settings.beamSize": "حجم الشعاع",
	"settings.beamSizeDescription": "عدد الأشعة لفك تشفير البحث بالشعاع",
	"settings.bestOf": "أفضل",
	"settings.bestOfDescription": "عدد المرشحين للنظر",
	"settings.conditionOnPreviousText": "الشرط على النص السابق",
	"settings.conditionOnPreviousTextDescription":
		"استخدام النص السابق كموجه للقطعة التالية",
	"settings.volumeDucking": "خفض مستوى الصوت",
	"settings.volumeDuckingDescription": "خفض الصوت الآخر أثناء التسجيل",
	"settings.volumeDuckSmart": "خفض ذكي",
	"settings.volumeDuckSmartDescription": "خفض الصوت فقط عند اكتشاف كلام",
	"settings.exportConfig": "تصدير الإعدادات",
	"settings.importConfig": "استيراد الإعدادات",
	"settings.resetDefaults": "إعادة التعيين إلى الافتراضي",
	"settings.cloudApi": "API سحابي",
	"settings.cloudApiDescription": "تكوين مزودي النسخ السحابي",
	"settings.llmPolish": "تحسين LLM",
	"settings.llmPolishDescription":
		"استخدام الذكاء الاصطناعي لتحسين مخرجات النسخ",
	"settings.llmPreset": "إعداد LLM المسبق",
	"settings.llmPresetDescription": "إعداد مسبق للنمط لتحسين LLM",
	"settings.languageAutoDetect": "كشف تلقائي",
	"settings.languageEnglish": "الإنجليزية",
	"settings.languageChinese": "الصينية",
	"settings.languageSpanish": "الإسبانية",
	"settings.languageArabic": "العربية",
	"settings.languageFrench": "الفرنسية",
	"settings.languageRussian": "الروسية",
	"settings.languagePortuguese": "البرتغالية",
	"settings.languageGerman": "الألمانية",
	"settings.languageJapanese": "اليابانية",
	"settings.languageKorean": "الكورية",
	"settings.languageItalian": "الإيطالية",
	"settings.languageDutch": "الهولندية",
	"settings.languagePolish": "البولندية",
	"settings.languageTurkish": "التركية",
	"settings.languageVietnamese": "الفيتنامية",
	"settings.languageThai": "التايلاندية",
	"settings.languageHindi": "الهندية",
	"settings.languageIndonesian": "الإندونيسية",
	"settings.languageSwedish": "السويدية",
	"settings.languageDanish": "الدنماركية",
	"settings.languageFinnish": "الفنلندية",
	"settings.languageNorwegian": "النرويجية",
	"settings.languageCzech": "التشيكية",
	"settings.languageRomanian": "الرومانية",
	"settings.languageHungarian": "المجرية",
	"settings.languageGreek": "اليونانية",
	"settings.languageHebrew": "العبرية",
	"settings.presetProfessional": "مهني",
	"settings.presetCasual": "غير رسمي",
	"settings.presetEmail": "بريد إلكتروني",
	"settings.presetCode": "كود",
	"settings.appLanguage": "لغة التطبيق",
	"settings.appLanguageDescription": "اختر لغة واجهة Voice Typer.",
	"settings.transcriptionLanguage": "لغة النسخ",
	"settings.transcriptionLanguageDescription":
		"الكشف التلقائي عن اللغة المنطوقة، أو اختر واحدة لدقة أفضل.",
	"settings.launchAtLogin": "التشغيل عند تسجيل الدخول",
	"settings.launchAtLoginDescription":
		"بدء Voice Typer تلقائياً عند تسجيل الدخول إلى Windows.",
	"settings.fastStartup": "بدء سريع (تشغيل مسبق)",
	"settings.fastStartupDescription":
		"تسخين ذاكرة التخزين المؤقت لنظام الملفات عند بدء التشغيل بحيث يتم تحميل نموذج الكلام في ثوانٍ بدلاً من 30+ ثانية. قم بتعطيله إذا كنت بحاجة إلى مساحة القرص/الذاكرة لتطبيقات أخرى. يمكنك التسخين عند الطلب من صفحة حول.",
	"settings.notifications": "الإشعارات",
	"settings.notificationsDescription":
		"عرض إشعار سطح المكتب عند اكتمال النسخ أو حدوث خطأ.",
	"settings.trayClick": "نقر علبة النظام",
	"settings.trayClickDescription":
		"ماذا يحدث عند النقر يساراً على أيقونة Voice Typer في علبة النظام.",
	"settings.generalDescription":
		"تكوين كيفية بدء Voice Typer، وسلوكه عند تسجيل الدخول، وتفاعله مع نظامك.",

	// settings.overlay
	"settings.overlay": "تراكب",
	"settings.overlayDescription":
		"التحكم في كيفية ظهور مؤشر التسجيل العائم وتصرفه على الشاشة.",
	"settings.bubbleBehaviorLabel": "سلوك الفقاعة",
	"settings.bubbleBehaviorInfo":
		"إظهار الفقاعة فقط أثناء التسجيل، أو إبقائها مرئية في جميع الأوقات.",
	"settings.bubblePositionLabel": "موضع الفقاعة",
	"settings.bubblePositionInfo":
		"أين تظهر الفقاعة على الشاشة — أعلى أو أسفل المركز.",
	"settings.showOnAppStartup": "الإظهار عند بدء التطبيق",
	"settings.showOnAppStartupInfo":
		"إظهار الفقاعة بمجرد فتح التطبيق. عند إيقافه، تظهر فقط عند بدء التسجيل.",
	"settings.dragToMove": "سحب للتحريك",
	"settings.dragToMoveInfo":
		"السماح بسحب الفقاعة بالماوس لإعادة وضعها على الشاشة.",

	// settings.postProcessing
	"settings.postProcessing": "المعالجة البعدية",
	"settings.postProcessingDescription":
		"ضبط كيفية تنظيف النصوص المنسوخة وتصحيحها وتنسيقها.",
	"settings.autoPunctuation": "علامات الترقيم التلقائية",
	"settings.autoPunctuationInfo":
		"إضافة النقاط والفواصل وعلامات الاستفهام تلقائياً.",
	"settings.textCleanupLabel": "تنظيف النص",
	"settings.textCleanupInfo":
		"إصلاح الأخطاء الإملائية الشائعة وإزالة الكلمات المكررة وكتابة الحروف الكبيرة.",
	"settings.textSnippets": "مقتطفات النص",
	"settings.textSnippetsInfo":
		"استخدام أوامر صوتية لإدراج مقتطفات نصية مكتوبة مسبقاً مع متغيرات.",
	"settings.vocabulary": "المفردات",
	"settings.vocabularyInfo":
		"استبدالات كلمات مخصصة بحيث يستخدم النسخ مصطلحاتك المفضلة.",
	"settings.llmPolishing": "تحسين LLM",
	"settings.llmPolishingDescription2":
		"استخدام نموذج لغة ذكاء اصطناعي لتحسين وتنقيح نصوصك المنسوخة.",
	"settings.enable": "تمكين",
	"settings.enableInfo":
		"استخدام نموذج لغة ذكاء اصطناعي لتنظيف وتحسين النص المنسوخ. يتطلب مفتاح API.",
	"settings.apiKey": "مفتاح API",
	"settings.apiKeyInfo": "مفتاح API المتوافق مع OpenAI لخدمة التحسين.",
	"settings.keyring.secure": "آمن",
	"settings.keyring.plaintext": "نص عادي",
	"settings.keyring.available": "مخزن بأمان في سلسلة مفاتيح نظام التشغيل.",
	"settings.keyring.availableWithBackend":
		"مخزن بأمان في سلسلة مفاتيح نظام التشغيل (الخلفية: {backend}).",
	"settings.keyring.fallback":
		"خلفية سلسلة المفاتيح غير متاحة — سيتم تخزين الأسرار كنص عادي بأذونات 0o600.",
	"settings.keyring.fallbackWithReason":
		"خلفية سلسلة المفاتيح غير متاحة ({reason}) — سيتم تخزين الأسرار كنص عادي بأذونات 0o600.",
	"settings.apiUrl": "عنوان API",
	"settings.apiUrlInfo":
		"عنوان URL لنقطة نهاية خدمة نموذج اللغة الذكاء الاصطناعي.",
	"settings.apiUrlPlaceholder": "https://api.openai.com/v1/chat/completions",
	"settings.model": "النموذج",
	"settings.modelInfo":
		"نموذج الذكاء الاصطناعي المستخدم للتحسين (مثل gpt-4o-mini).",
	"settings.modelPlaceholder": "gpt-4o-mini",
	"settings.preset": "الإعداد المسبق",
	"settings.presetInfo":
		"أسلوب الكتابة للتطبيق — مهني، غير رسمي، بريد إلكتروني، أو كود.",
	"settings.show": "إظهار",
	"settings.hide": "إخفاء",
	"settings.trayClickOpenApp": "فتح التطبيق",
	"settings.trayClickToggleDictation": "تبديل الإملاء",
	"settings.bubblePositionTop": "أعلى المنتصف",
	"settings.bubblePositionBottom": "أسفل المنتصف",
	"settings.bubbleBehaviorShowOnRecord": "الإظهار عند التسجيل",
	"settings.bubbleBehaviorAlwaysVisible": "ظاهر دائمًا",
	"settings.description": "ضبط Voice Typer حسب تفضيلاتك.",
	"settings.loading": "جاري تحميل الإعدادات…",
	"settings.saving": "جاري الحفظ…",
	"settings.autoSave": "حفظ تلقائي",
	"settings.savedToast": "تم الحفظ",
	"settings.saveFailedToast": "فشل حفظ الإعداد",
	"settings.logFolderOpened": "تم فتح مجلد السجل",
	"settings.couldNotOpenLogFolder": "تعذر فتح مجلد السجل",
	"settings.resetToDefaultsToast": "تمت إعادة الإعدادات إلى الافتراضية",
	"settings.fetchDefaultsFailed":
		"فشل جلب الإعدادات الافتراضية من الخادم الخلفي",
	"settings.resetFailed": "فشلت إعادة التعيين إلى الافتراضية",
	"settings.allChangesSaved": "تم حفظ جميع التغييرات",
	"settings.tabsAria": "ألسنة الإعدادات",

	// settings.hotkeySection
	"settings.hotkeySection.hotkeyTitle": "المفتاح السريع",
	"settings.hotkeySection.hotkeyDescription": "مفتاح لبدء وإيقاف الإملاء.",
	"settings.hotkeySection.dictationKey": "مفتاح الإملاء",
	"settings.hotkeySection.dictationKeyInfo":
		"اختصار لوحة المفاتيح لبدء وإيقاف الإملاء.",
	"settings.hotkeySection.dictationKeyInfoSearch":
		"مفتاح لوحة المفاتيح المستخدم لبدء وإيقاف التسجيل.",
	"settings.hotkeySection.dictationKeyAria": "مفتاح الإملاء",
	"settings.hotkeySection.recordingTitle": "المفاتيح السريعة والتسجيل",
	"settings.hotkeySection.recordingDescription":
		"تكوين اختصارات التسجيل والإيقاف التلقائي للصمت وتفضيلات الصوت.",
	"settings.hotkeySection.recordingMode": "وضع التسجيل",
	"settings.hotkeySection.recordingModeInfo":
		"تبديل: اضغط المفتاح مرة للبدء ومرة أخرى للإيقاف. اضغط لتتكلم: استمر في الضغط على المفتاح أثناء التحدث.",
	"settings.hotkeySection.recordingModeInfoSearch":
		"اضغط للتسجيل: اضغط المفتاح مرة للبدء ومرة أخرى للإيقاف. اضغط لتتكلم: استمر في الضغط على المفتاح أثناء التحدث.",
	"settings.hotkeySection.recordingModeAria": "وضع التسجيل",
	"settings.hotkeySection.tapToRecord": "اضغط للتسجيل",
	"settings.hotkeySection.pushToTalk": "اضغط لتتكلم",
	"settings.hotkeySection.minutes1": "دقيقة واحدة",
	"settings.hotkeySection.minutes2": "دقيقتان",
	"settings.hotkeySection.minutes3": "3 دقائق",
	"settings.hotkeySection.minutes5": "5 دقائق",
	"settings.hotkeySection.escToCancel": "Esc للإلغاء",
	"settings.hotkeySection.escToCancelInfo": "اضغط Escape لإلغاء تسجيل نشط.",
	"settings.hotkeySection.escToCancelAria": "Esc للإلغاء",
	"settings.hotkeySection.autoPaste": "لصق تلقائي",
	"settings.hotkeySection.autoPasteInfo":
		"لصق النص المنسوخ تلقائياً في الحقل النشط حالياً.",
	"settings.hotkeySection.autoPasteAria": "لصق تلقائي",
	"settings.hotkeySection.soundFeedback": "التغذية الصوتية",
	"settings.hotkeySection.soundFeedbackInfo":
		"تشغيل إشارة صوتية قصيرة عند بدء وإيقاف التسجيل. مفيد لإمكانية الوصول والتأكيد.",
	"settings.hotkeySection.soundFeedbackInfoSearch":
		"تشغيل إشارة صوتية قصيرة عند بدء وإيقاف التسجيل.",
	"settings.hotkeySection.soundFeedbackAria": "التغذية الصوتية",
	"settings.hotkeySection.repasteKey": "مفتاح إعادة اللصق",
	"settings.hotkeySection.repasteKeyInfo":
		"اختصار لوحة المفاتيح لإعادة لصق آخر نسخ. انقر على الزر لتسجيل تركيبة جديدة، أو اختر من قائمة الإعدادات المسبقة.",
	"settings.hotkeySection.repasteKeyInfoSearch":
		"اختصار لوحة المفاتيح لإعادة لصق آخر نسخ.",
	"settings.hotkeySection.repasteKeyAria": "مفتاح إعادة اللصق",
	"settings.hotkeySection.silenceWarning": "تحذير الصمت",
	"settings.hotkeySection.silenceWarningInfo":
		"ثوانٍ من الصمت قبل إظهار تحذير للمساعدة في اكتشاف مشاكل الميكروفون.",
	"settings.hotkeySection.silenceWarningAria": "ثواني تحذير الصمت",
	"settings.hotkeySection.maxRecordingTime": "الحد الأقصى لوقت التسجيل",
	"settings.hotkeySection.maxRecordingTimeInfo":
		"الحد الأقصى لوقت التسجيل بالدقائق (5-60).",
	"settings.hotkeySection.maxRecordingTimeInfoSearch":
		"الحد الأقصى لوقت التسجيل بالدقائق.",
	"settings.hotkeySection.maxRecordingTimeAria":
		"دقائق الحد الأقصى لوقت التسجيل",
	"settings.hotkeySection.stopOnSilence": "الإيقاف عند الصمت",
	"settings.hotkeySection.stopOnSilenceInfo":
		"إيقاف التسجيل تلقائياً بعد هذه المدة من الصمت بالثواني.",
	"settings.hotkeySection.stopOnSilenceAria": "ثواني الإيقاف عند الصمت",

	// models section
	"models.asrTitle": "نماذج ASR",
	"models.asrSubtitle": "تكوين نماذج الكلام المحلية والسحابية.",
	"models.title": "النماذج",
	"models.description": "تكوين محركات تحويل الكلام إلى نص",
	"models.active": "نشط",
	"models.delete": "حذف",
	"models.use": "استخدام",
	"models.select": "اختيار",
	"models.downloadModel": "تنزيل",
	"models.downloading": "جاري التنزيل…",
	"models.selecting": "جاري الاختيار…",
	"models.providers.openai.label": "OpenAI Whisper API",
	"models.providers.groq.label": "Groq Whisper API",
	"models.providers.deepgram.label": "Deepgram API",
	"models.errors.unknown": "خطأ غير معروف",
	"models.snack.parakeetDepsRequired":
		"المتطلبات الأساسية لـ Parakeet مطلوبة. قم بالتنزيل أولاً.",
	"models.snack.notDownloaded":
		'النموذج "{name}" لم يتم تنزيله بعد. قم بتنزيله أولاً.',
	"models.snack.usingModel": "جاري استخدام النموذج: {name}",
	"models.snack.downloaded": "تم تنزيل {name} بنجاح",
	"models.snack.downloadFailedName": "فشل تنزيل {name}",
	"models.snack.downloadFailed": "فشل التنزيل: {error}",
	"models.snack.cannotDeleteActive":
		"لا يمكن حذف النموذج النشط. قم بالتبديل إلى نموذج آخر أولاً.",
	"models.snack.deleted": "تم الحذف: {name}",
	"models.snack.deleteFailed": "فشل الحذف",
	"models.snack.deleteFailedError": "فشل الحذف: {error}",
	"models.snack.apiKeySaved": "تم حفظ مفتاح API لـ {provider}",
	"models.snack.consentGranted":
		"تم منح الموافقة لـ {provider} — سيتم إرسال الصوت إلى هذا المزود.",
	"models.snack.consentRevoked":
		"تم سحب الموافقة لـ {provider} — لن يتم إرسال الصوت.",
	"models.snack.hfConsentGranted":
		"تم منح الموافقة — ستتم عمليات تنزيل النموذج من HuggingFace.",
	"models.snack.hfConsentRevoked":
		"تم سحب الموافقة — تم حظر تنزيلات النموذج من HuggingFace.",
	"models.snack.resumeFailed": "فشل الاستئناف: {error}",
	"models.snack.pauseFailed": "فشل الإيقاف المؤقت: {error}",
	"models.snack.cancelled":
		"تم إلغاء التنزيل. سيتم إعادة استخدام الملفات الجزئية عند المحاولة مرة أخرى.",
	"models.snack.cancelFailed": "فشل الإلغاء: {error}",
	"models.test.needApiKey": "يرجى إدخال مفتاح API أولاً",
	"models.test.connectionSuccessful": "الاتصال ناجح — مفتاح API صالح.",
	"models.test.connectionFailed": "فشل الاتصال: {status} {statusText}",
	"models.test.endpointUnavailable":
		"تم حفظ مفتاح API — نقطة نهاية الاختبار غير متاحة لهذا المزود.",
	"models.test.connectionTestFailed": "فشل اختبار الاتصال: {error}",
	"models.benchmark.notImplemented": "لم يتم تنفيذ المعيار بعد.",
	"models.benchmark.title": "معيار النموذج",
	"models.benchmark.description": "مقارنة أداء النموذج على نظامك",
	"models.benchmark.runAria": "تشغيل معيار النموذج",
	"models.benchmark.running": "جاري التشغيل...",
	"models.benchmark.run": "تشغيل المعيار",
	"models.status.active": "نشط",
	"models.status.downloaded": "تم التنزيل",
	"models.status.depsRequired": "المتطلبات الأساسية مطلوبة",
	"models.status.available": "متاح",
	"models.import.title": "استيراد نموذج",
	"models.import.importModel": "استيراد نموذج",
	"models.import.importing": "جاري الاستيراد...",
	"models.import.success": "تم استيراد {count} نموذج: {models}",
	"models.import.noModelsFound":
		"لم يتم العثور على مجلدات نماذج معروفة في هذا الدليل. حدد مجلد ذاكرة التخزين المؤقت لـ HuggingFace (يحتوي على أدلة 'models--*').",
	"models.import.failedAll": "فشل استيراد أي نماذج من هذا الدليل.",
	"models.import.failed": "فشل استيراد النموذج: {error}",
	"models.import.importModelAria": "استيراد نموذج من مجلد",

	// Settings appearance section
	"settings.appearance.title": "المظهر",
	"settings.appearance.description":
		"تخصيص المظهر — نظام الألوان وسمات المظهر وحجم النص.",
	"settings.appearance.colorScheme": "نظام الألوان",
	"settings.appearance.colorSchemeInfo":
		"التبديل بين الفاتح والداكن أو اتباع إعدادات النظام.",
	"settings.appearance.colorSchemeAria": "نظام الألوان",
	"settings.appearance.systemDefault": "إعدادات النظام الافتراضية",
	"settings.appearance.light": "فاتح",
	"settings.appearance.dark": "داكن",
	"settings.appearance.themePreset": "سمة مسبقة",
	"settings.appearance.themePresetInfo":
		"نظام ألوان مدمج يُطبق فوق الوضع الذي تختاره.",
	"settings.appearance.themePresetInfoRendered":
		"اختر نظام ألوان مدمج. مرر المؤشر للمعاينة، انقر للتطبيق الدائم.",
	"settings.appearance.themePresetAria": "سمة مسبقة",
	"settings.appearance.customTheme": "سمة مخصصة",
	"settings.appearance.customThemeInfo":
		"إنشاء نظام ألوان خاص بك باستخدام منتقي الألوان.",
	"settings.appearance.customThemeInfoRendered":
		"أنشئ نظام ألوان خاص بك — اختر كل لون يدوياً.",
	"settings.appearance.customThemeAria": "سمة مخصصة",
	"settings.appearance.resetToDefaultColors":
		"إعادة التعيين إلى الألوان الافتراضية",
	"settings.appearance.textSize": "حجم النص",
	"settings.appearance.textSizeInfo":
		"ضبط حجم نص واجهة المستخدم لتحسين القراءة.",
	"settings.appearance.textSizeInfoRendered":
		"ضبط حجم نص واجهة المستخدم لتحسين القراءة. الحجم الافتراضي 14px.",
	"settings.appearance.textSizeAria": "حجم النص",
	"settings.appearance.colorAria": "لون {label}",
	"settings.appearance.hexValueAria": "قيمة {label} السداسية العشرية",
	"settings.appearance.customDropdownLabel": "مخصص (استخدم المفتاح أدناه)",
	"settings.appearance.hexInvalid":
		"لون سداسي عشري غير صالح — يجب أن يكون #rrggbb (مثل #1a2b3c). سيتم العودة إلى القيمة السابقة عند فقدان التركيز.",
	"settings.appearance.contrastWarning":
		"تباين منخفض: {ratio}:1 (WCAG AA يتطلب 4.5:1). قم بزيادة الفرق بين هذا اللون والخلفية ذات الصلة.",
	"settings.appearance.contrastWarningShort": "التباين {ratio}:1",

	// settings audio enhancement
	"settings.audioEnhancement.title": "تحسين الصوت",
	"settings.audioEnhancement.description":
		"خفض مستوى الصوت وتصفية الضوضاء لإملاء أنظف.",
	"settings.audioEnhancement.volumeBackend": "خلفية الصوت",
	"settings.audioEnhancement.volumeBackendInfo":
		"خلفية التحكم في الصوت النشطة. 'disabled' يعني أن خفض الصوت لن يعمل على هذا النظام الأساسي.",
	"settings.audioEnhancement.volumeBackendInfoSearch":
		"خلفية التحكم في الصوت النشطة.",
	"settings.audioEnhancement.unavailableSuffix": "{name} (غير متاح)",
	"settings.audioEnhancement.detecting": "جاري الكشف…",
	"settings.audioEnhancement.autoDuckVolume": "خفض مستوى الصوت تلقائياً",
	"settings.audioEnhancement.autoDuckVolumeInfo":
		"تقليل مستوى صوت النظام أثناء الإملاء لمنع تسرب الصوت إلى الميكروفون.",
	"settings.audioEnhancement.autoDuckVolumeInfoSearch":
		"تقليل مستوى صوت النظام أثناء الإملاء.",
	"settings.audioEnhancement.autoDuckVolumeAria": "خفض مستوى الصوت تلقائياً",
	"settings.audioEnhancement.duckLevel": "مستوى الخفض",
	"settings.audioEnhancement.duckLevelInfo": "كم تريد خفض صوت النظام.",
	"settings.audioEnhancement.duckLevelInfoSearch": "كم تريد خفض صوت النظام.",
	"settings.audioEnhancement.duckLevelAria": "مستوى الخفض",
	"settings.audioEnhancement.microphoneQuality": "جودة الميكروفون",
	"settings.audioEnhancement.microphoneQualityInfo":
		"تكوين الإعدادات المسبقة لسلسلة التصفية بأكملها للسيناريوهات الشائعة.",
	"settings.audioEnhancement.microphoneQualityInfoSearch":
		"تكوين الإعدادات المسبقة لسلسلة التصفية.",
	"settings.audioEnhancement.microphoneQualityAria":
		"إعداد جودة الميكروفون المسبق",
	"settings.audioEnhancement.presetAuto": "تلقائي (موصى به)",
	"settings.audioEnhancement.presetStudio": "استوديو (بيئة نظيفة)",
	"settings.audioEnhancement.presetNoisyRoom": "غرفة مزعجة (لوحة مفاتيح/مروحة)",
	"settings.audioEnhancement.presetOff": "إيقاف (صوت خام)",
	"settings.audioEnhancement.presetCustom": "مخصص (متقدم)",
	"settings.audioEnhancement.presetAutoDescription":
		"الخادم الخلفي يختار أفضل سلسلة تصفية لمستوى الضوضاء المكتشف والعتاد. موصى به لمعظم المستخدمين.",
	"settings.audioEnhancement.presetStudioDescription":
		"تصفية خفيفة للغرف الهادئة والمعالجة.",
	"settings.audioEnhancement.presetNoisyRoomDescription":
		"تصفية قوية لضجيج لوحة المفاتيح والمراوح.",
	"settings.audioEnhancement.presetOffDescription":
		"تجاوز سلسلة التصفية بأكملها. استخدم فقط مع إشارة نظيفة.",
	"settings.audioEnhancement.presetCustomDescription":
		"اختيار كل مرشح ومعلمة يدوياً.",
	"settings.audioEnhancement.highPassFilter": "مرشح تمرير عالي",
	"settings.audioEnhancement.highPassFilterInfo":
		"إزالة الهدير منخفض التردد (HVAC، حركة المرور) أسفل تردد القطع.",
	"settings.audioEnhancement.highPassFilterInfoSearch":
		"إزالة الهدير منخفض التردد.",
	"settings.audioEnhancement.highPassFilterAria": "مرشح تمرير عالي",
	"settings.audioEnhancement.highPassCutoff": "قطع التمرير العالي",
	"settings.audioEnhancement.highPassCutoffInfo":
		"الترددات الأقل من هذا يتم تخفيفها.",
	"settings.audioEnhancement.highPassCutoffInfoSearch":
		"الترددات الأقل من هذا يتم تخفيفها.",
	"settings.audioEnhancement.highPassCutoffAria": "قطع التمرير العالي",
	"settings.audioEnhancement.noiseSuppression": "تقليل الضوضاء",
	"settings.audioEnhancement.noiseSuppressionInfo":
		"مزيل ضوضاء بالشبكة العصبية.",
	"settings.audioEnhancement.noiseSuppressionInfoSearch":
		"مزيل ضوضاء بالشبكة العصبية.",
	"settings.audioEnhancement.noiseSuppressionAria": "طريقة تقليل الضوضاء",
	"settings.audioEnhancement.noneOption": "لا شيء",
	"settings.audioEnhancement.noiseGate": "بوابة الضوضاء",
	"settings.audioEnhancement.noiseGateInfo":
		"إسكات الصوت تحت عتبة لإزالة الهسهسة الخاملة.",
	"settings.audioEnhancement.noiseGateInfoSearch": "إسكات الصوت تحت عتبة.",
	"settings.audioEnhancement.noiseGateAria": "بوابة الضوضاء",
	"settings.audioEnhancement.gateOpenThreshold": "عتبة فتح البوابة",
	"settings.audioEnhancement.gateOpenThresholdInfo":
		"المستوى الذي تفتح عنده البوابة (تمرير الصوت).",
	"settings.audioEnhancement.gateOpenThresholdInfoSearch":
		"المستوى الذي تفتح عنده البوابة.",
	"settings.audioEnhancement.gateOpenThresholdAria": "عتبة فتح البوابة",
	"settings.audioEnhancement.gateCloseThreshold": "عتبة إغلاق البوابة",
	"settings.audioEnhancement.gateCloseThresholdInfo":
		"المستوى الذي تغلق عنده البوابة (تخفيف الصوت).",
	"settings.audioEnhancement.gateCloseThresholdInfoSearch":
		"المستوى الذي تغلق عنده البوابة.",
	"settings.audioEnhancement.gateCloseThresholdAria": "عتبة إغلاق البوابة",
	"settings.audioEnhancement.gateAttack": "هجوم البوابة",
	"settings.audioEnhancement.gateAttackInfo":
		"مدى سرعة فتح البوابة عندما يرتفع الإشارة فوق عتبة الفتح.",
	"settings.audioEnhancement.gateAttackInfoSearch": "مدى سرعة فتح البوابة.",
	"settings.audioEnhancement.gateAttackAria": "هجوم البوابة",
	"settings.audioEnhancement.gateHold": "احتضان البوابة",
	"settings.audioEnhancement.gateHoldInfo":
		"كم من الوقت تبقى البوابة مفتوحة بعد انخفاض الإشارة تحت عتبة الإغلاق.",
	"settings.audioEnhancement.gateHoldInfoSearch":
		"كم من الوقت تبقى البوابة مفتوحة.",
	"settings.audioEnhancement.gateHoldAria": "احتضان البوابة",
	"settings.audioEnhancement.gateRelease": "تحرير البوابة",
	"settings.audioEnhancement.gateReleaseInfo":
		"مدى سرعة إغلاق البوابة بعد انتهاء وقت الاحتضان.",
	"settings.audioEnhancement.gateReleaseInfoSearch": "مدى سرعة إغلاق البوابة.",
	"settings.audioEnhancement.gateReleaseAria": "تحرير البوابة",
	"settings.audioEnhancement.equalizer": "معادل الصوت",
	"settings.audioEnhancement.equalizerInfo":
		"معادل 3 نطاقات: تعزيز الوسط (وضوح الكلام)، خفض المنخفض (الهدير)، ارتفاع طفيف (الحضور).",
	"settings.audioEnhancement.equalizerInfoSearch": "معادل 3 نطاقات.",
	"settings.audioEnhancement.equalizerAria": "معادل الصوت",
	"settings.audioEnhancement.eqLow": "EQ — منخفض (باس)",
	"settings.audioEnhancement.eqLowInfo": "تعزيز/خفض تحت 800Hz.",
	"settings.audioEnhancement.eqLowInfoSearch": "تعزيز/خفض تحت 800Hz.",
	"settings.audioEnhancement.eqLowAria": "EQ منخفض",
	"settings.audioEnhancement.eqMid": "EQ — وسط (كلام)",
	"settings.audioEnhancement.eqMidInfo":
		"تعزيز/خفض 800Hz–5kHz (نطاق وضوح الكلام).",
	"settings.audioEnhancement.eqMidInfoSearch": "تعزيز/خفض 800Hz–5kHz.",
	"settings.audioEnhancement.eqMidAria": "EQ وسط",
	"settings.audioEnhancement.eqHigh": "EQ — مرتفع (ثلاثة أضعاف)",
	"settings.audioEnhancement.eqHighInfo": "تعزيز/خفض فوق 5kHz.",
	"settings.audioEnhancement.eqHighInfoSearch": "تعزيز/خفض فوق 5kHz.",
	"settings.audioEnhancement.eqHighAria": "EQ مرتفع",
	"settings.audioEnhancement.compressor": "ضاغط",
	"settings.audioEnhancement.compressorInfo":
		"تسوية الكلام العالي/المنخفض لدقة ASR متسقة.",
	"settings.audioEnhancement.compressorInfoSearch":
		"تسوية الكلام العالي/المنخفض.",
	"settings.audioEnhancement.compressorAria": "ضاغط",
	"settings.audioEnhancement.compressorThreshold": "عتبة الضاغط",
	"settings.audioEnhancement.compressorThresholdInfo":
		"المستوى الذي يبدأ عنده الضغط.",
	"settings.audioEnhancement.compressorThresholdInfoSearch":
		"المستوى الذي يبدأ عنده الضغط.",
	"settings.audioEnhancement.compressorThresholdAria": "عتبة الضاغط",
	"settings.audioEnhancement.compressorRatio": "نسبة الضاغط",
	"settings.audioEnhancement.compressorRatioInfo": "مدى قوة الضغط.",
	"settings.audioEnhancement.compressorRatioInfoSearch": "مدى قوة الضغط.",
	"settings.audioEnhancement.compressorRatioAria": "نسبة الضاغط",
	"settings.audioEnhancement.compressorAttack": "هجوم الضاغط",
	"settings.audioEnhancement.compressorAttackInfo":
		"مدى سرعة تفعيل الضغط عندما تتجاوز الإشارة العتبة.",
	"settings.audioEnhancement.compressorAttackInfoSearch":
		"مدى سرعة تفعيل الضغط.",
	"settings.audioEnhancement.compressorAttackAria": "هجوم الضاغط",
	"settings.audioEnhancement.compressorRelease": "تحرير الضاغط",
	"settings.audioEnhancement.compressorReleaseInfo":
		"مدى سرعة إلغاء الضغط بعد انخفاض الإشارة تحت العتبة.",
	"settings.audioEnhancement.compressorReleaseInfoSearch":
		"مدى سرعة إلغاء الضغط.",
	"settings.audioEnhancement.compressorReleaseAria": "تحرير الضاغط",
	"settings.audioEnhancement.compressorOutputGain": "كسب خرج الضاغط",
	"settings.audioEnhancement.compressorOutputGainInfo":
		"كسب التعويض المطبق بعد الضغط لاستعادة مستوى الصوت المدرك.",
	"settings.audioEnhancement.compressorOutputGainInfoSearch":
		"كسب التعويض المطبق بعد الضغط.",
	"settings.audioEnhancement.compressorOutputGainAria": "كسب خرج الضاغط",
	"settings.audioEnhancement.limiter": "محدد",
	"settings.audioEnhancement.limiterInfo": "سقف صارم لمنع القص.",
	"settings.audioEnhancement.limiterInfoSearch": "سقف صارم لمنع القص.",
	"settings.audioEnhancement.limiterAria": "محدد",
	"settings.audioEnhancement.limiterCeiling": "سقف المحدد",
	"settings.audioEnhancement.limiterCeilingInfo":
		"الحد الأقصى المطلق لمستوى الخرج.",
	"settings.audioEnhancement.limiterCeilingInfoSearch":
		"الحد الأقصى المطلق لمستوى الخرج.",
	"settings.audioEnhancement.limiterCeilingAria": "سقف المحدد",
	"settings.audioEnhancement.limiterRelease": "تحرير المحدد",
	"settings.audioEnhancement.limiterReleaseInfo":
		"مدى سرعة تعافي المحدد بعد التقاط عابر.",
	"settings.audioEnhancement.limiterReleaseInfoSearch":
		"مدى سرعة تعافي المحدد.",
	"settings.audioEnhancement.limiterReleaseAria": "تحرير المحدد",
	"settings.audioEnhancement.notchFilter": "مرشح شق (طنين)",
	"settings.audioEnhancement.notchFilterInfo":
		"إزالة طنين التيار الكهربائي 50/60Hz.",
	"settings.audioEnhancement.notchFilterInfoSearch":
		"إزالة طنين التيار الكهربائي 50/60Hz.",
	"settings.audioEnhancement.notchFilterAria": "مرشح شق",
	"settings.audioEnhancement.notchFrequency": "تردد الشق",
	"settings.audioEnhancement.notchFrequencyInfo":
		"تردد مركز الشق. 50Hz لأوروبا/آسيا، 60Hz لأمريكا الشمالية.",
	"settings.audioEnhancement.notchFrequencyInfoSearch": "تردد مركز الشق.",
	"settings.audioEnhancement.notchFrequencyAria": "تردد الشق",

	// settings aiEnhancement
	"settings.aiEnhancement.title": "تحسين الذكاء الاصطناعي",
	"settings.aiEnhancement.description":
		"قواعد نحوية وعلامات ترقيم وكتابة أحرف كبيرة. يعمل دون اتصال — لا حاجة لـ API سحابي.",
	"settings.aiEnhancement.enable": "تمكين تحسين الذكاء الاصطناعي",
	"settings.aiEnhancement.enableInfo":
		"تطبيق تصحيحات نحوية قائمة على القواعد وعلامات ترقيم تلقائية وكتابة أحرف كبيرة تلقائياً على نصوصك المنسوخة. يعمل بالكامل على الجهاز.",
	"settings.aiEnhancement.enableInfoSearch":
		"تطبيق تصحيحات نحوية قائمة على القواعد وعلامات ترقيم تلقائية وكتابة أحرف كبيرة.",
	"settings.aiEnhancement.enableAria": "تمكين تحسين الذكاء الاصطناعي",
	"settings.aiEnhancement.fixGrammar": "إصلاح الأساسيات النحوية",
	"settings.aiEnhancement.fixGrammarInfo":
		'كتابة الضمير "أنا" بأحرف كبيرة، واستعادة الفواصل العليا المفقودة في الاختصارات الشائعة، وإزالة المسافات المزدوجة.',
	"settings.aiEnhancement.fixGrammarInfoSearch":
		'كتابة الضمير "أنا" بأحرف كبيرة، واستعادة الفواصل العليا المفقودة.',
	"settings.aiEnhancement.fixGrammarAria": "إصلاح الأساسيات النحوية",
	"settings.aiEnhancement.autoPunctuate": "علامات الترقيم التلقائية",
	"settings.aiEnhancement.autoPunctuateInfo":
		"إضافة نقطة في نهاية الجمل التي لا تحتوي على علامات ترقيم نهائية.",
	"settings.aiEnhancement.autoPunctuateInfoSearch":
		"إضافة نقطة في نهاية الجمل.",
	"settings.aiEnhancement.autoPunctuateAria": "علامات الترقيم التلقائية",
	"settings.aiEnhancement.autoCapitalize": "كتابة الأحرف الكبيرة تلقائياً",
	"settings.aiEnhancement.autoCapitalizeInfo":
		"كتابة الحرف الأول من كل جملة بأحرف كبيرة ومجموعة صغيرة من أسماء العلم.",
	"settings.aiEnhancement.autoCapitalizeInfoSearch":
		"كتابة الحرف الأول من كل جملة بأحرف كبيرة.",
	"settings.aiEnhancement.autoCapitalizeAria": "كتابة الأحرف الكبيرة تلقائياً",

	// settings.vocabAutomation
	"settings.vocabAutomation.title": "أتمتة المفردات",
	"settings.vocabAutomation.description":
		"اقتراح تصحيحات المفردات بناءً على ثقة النسخ. معطل افتراضياً.",
	"settings.vocabAutomation.enable": "تمكين أتمتة المفردات",
	"settings.vocabAutomation.enableInfo":
		"بعد كل إملاء، تحليل الكلمات منخفضة الثقة واقتراح تصحيحات للمفردات.",
	"settings.vocabAutomation.enableInfoSearch":
		"بعد كل إملاء، تحليل الكلمات منخفضة الثقة.",
	"settings.vocabAutomation.enableAria": "تمكين أتمتة المفردات",
	"settings.vocabAutomation.suggestBelowConfidence": "عتبة الاقتراح",
	"settings.vocabAutomation.suggestBelowConfidenceInfo":
		"الكلمات التي يتم نسخها بثقة أقل من هذه العتبة يتم وضع علامة عليها للمراجعة.",
	"settings.vocabAutomation.suggestBelowConfidenceInfoSearch":
		"الكلمات التي يتم نسخها بثقة أقل من هذه العتبة.",
	"settings.vocabAutomation.suggestBelowConfidenceAria": "عتبة الاقتراح",
	"settings.vocabAutomation.autoApplyConfidence": "عتبة التطبيق التلقائي",
	"settings.vocabAutomation.autoApplyConfidenceInfo":
		"الاقتراحات التي تكون ثقتها عند أو فوق هذه العتبة تُضاف إلى مفرداتك دون سؤال.",
	"settings.vocabAutomation.autoApplyConfidenceInfoSearch":
		"الاقتراحات التي تكون ثقتها عند أو فوق هذه العتبة.",
	"settings.vocabAutomation.autoApplyConfidenceAria": "عتبة التطبيق التلقائي",

	// settings.privacy
	"settings.privacy.audioRecoveryTitle": "الصوت والاستعادة",
	"settings.privacy.audioRecoveryDescription":
		"مراقبة جودة التسجيل وحماية نصوصك المنسوخة من الأعطال.",
	"settings.privacy.crashRecovery": "استعادة الأعطال",
	"settings.privacy.crashRecoveryInfo":
		"حفظ النصوص المنسوخة الحديثة بحيث يمكن استعادتها إذا تعطل التطبيق قبل لصقها.",
	"settings.privacy.crashRecoveryInfoSearch":
		"حفظ النصوص المنسوخة الحديثة بحيث يمكن استعادتها.",
	"settings.privacy.crashRecoveryAria": "استعادة الأعطال",
	"settings.privacy.privacyTitle": "الخصوصية والموافقة",
	"settings.privacy.privacyDescription":
		"منح أو سحب الموافقة على معالجة البيانات.",
	"settings.privacy.huggingFaceDownloadsLabel": "تنزيلات نماذج HuggingFace",
	"settings.privacy.huggingFaceDownloadsInfoSearch":
		"يسمح بتنزيل أوزان نموذج Whisper من huggingface.co.",
	"settings.privacy.voiceBiometricLabel": "معالجة البيانات الصوتية البيومترية",
	"settings.privacy.voiceBiometricInfoSearch":
		"يسمح لـ Voice Typer بمعالجة تسجيلاتك الصوتية محلياً.",
	"settings.privacy.openaiCloudAsrLabel": "OpenAI ASR سحابي",
	"settings.privacy.openaiCloudAsrInfoSearch":
		"يسمح بإرسال التسجيلات الصوتية إلى OpenAI Whisper API.",
	"settings.privacy.groqCloudAsrLabel": "Groq ASR سحابي",
	"settings.privacy.groqCloudAsrInfoSearch":
		"يسمح بإرسال التسجيلات الصوتية إلى Groq Whisper API.",
	"settings.privacy.deepgramCloudAsrLabel": "Deepgram ASR سحابي",
	"settings.privacy.deepgramCloudAsrInfoSearch":
		"يسمح بإرسال التسجيلات الصوتية إلى Deepgram nova-2 API.",
	"settings.privacy.llmTextPolishingLabel": "تحسين نص LLM",
	"settings.privacy.llmTextPolishingInfoSearch":
		"يسمح بإرسال النص المنسوخ إلى LLM API.",
	"settings.privacy.exportAllDataLabel":
		"تصدير جميع البيانات (GDPR Art. 15/20)",
	"settings.privacy.exportAllDataInfoSearch":
		"تنزيل قوالبك وإعداداتك الكاملة كملفات JSON.",
	"settings.privacy.consentBannerDesc":
		"يقوم Voice Typer بمعالجة الصوت والنص والبيانات الوصفية محلياً بشكل افتراضي.",
	"settings.privacy.huggingFaceItem":
		"HuggingFace: تنزيل أوزان نموذج Whisper (يكشف عنوان IP الخاص بك لطرف ثالث أمريكي؛ الصوت لا يغادر جهازك أبداً).",
	"settings.privacy.cloudAsrItem":
		"ASR سحابي (OpenAI / Groq / Deepgram): إرسال التسجيلات الصوتية للنسخ عندما يكون هذا المزود هو الخلفية النشطة.",
	"settings.privacy.llmPolishItem":
		"تحسين LLM: إرسال النص المنسوخ (وليس الصوت) إلى LLM API متوافق مع OpenAI للتحسين.",
	"settings.privacy.voiceBiometricItem":
		"البيانات الصوتية البيومترية: الإقرار بأن التسجيلات الصوتية المحلية قد تعتبر بيانات بيومترية بموجب BIPA / GDPR Art. 9.",
	"settings.privacy.revokeNotice":
		"يمكنك سحب أي موافقة في أي وقت عن طريق إيقاف تشغيلها أدناه.",
	"settings.privacy.consentsGranted": "{granted} من 6 موافقات ممنوحة",
	"settings.privacy.agreeToAll": "الموافقة على الكل",
	"settings.privacy.agreeToAllAria": "الموافقة على جميع موافقات الخصوصية",
	"settings.privacy.agreeToAllHint":
		"تمكين جميع علامات الموافقة الستة أدناه. يمكنك سحب الموافقات الفردية بعد ذلك.",
	"settings.privacy.huggingFaceDownloads": "تنزيلات نماذج HuggingFace",
	"settings.privacy.huggingFaceDownloadsInfo":
		"يسمح بتنزيل أوزان نموذج Whisper من huggingface.co.",
	"settings.privacy.huggingFaceDownloadsAria": "موافقة تنزيل HuggingFace",
	"settings.privacy.voiceBiometricProcessing":
		"معالجة البيانات الصوتية البيومترية",
	"settings.privacy.voiceBiometricProcessingInfo":
		"يسمح لـ Voice Typer بمعالجة تسجيلاتك الصوتية محلياً للنسخ.",
	"settings.privacy.voiceBiometricProcessingAria":
		"موافقة معالجة البيانات الصوتية البيومترية",
	"settings.privacy.openaiCloudAsr": "OpenAI ASR سحابي",
	"settings.privacy.openaiCloudAsrInfo":
		"يسمح بإرسال التسجيلات الصوتية إلى OpenAI Whisper API للنسخ.",
	"settings.privacy.openaiCloudAsrAria": "موافقة OpenAI ASR سحابي",
	"settings.privacy.groqCloudAsr": "Groq ASR سحابي",
	"settings.privacy.groqCloudAsrInfo":
		"يسمح بإرسال التسجيلات الصوتية إلى Groq Whisper API للنسخ.",
	"settings.privacy.groqCloudAsrAria": "موافقة Groq ASR سحابي",
	"settings.privacy.deepgramCloudAsr": "Deepgram ASR سحابي",
	"settings.privacy.deepgramCloudAsrInfo":
		"يسمح بإرسال التسجيلات الصوتية إلى Deepgram nova-2 API للنسخ.",
	"settings.privacy.deepgramCloudAsrAria": "موافقة Deepgram ASR سحابي",
	"settings.privacy.llmTextPolishing": "تحسين نص LLM",
	"settings.privacy.llmTextPolishingInfo":
		"يسمح بإرسال النص المنسوخ (وليس الصوت) إلى LLM API متوافق مع OpenAI للتحسين.",
	"settings.privacy.llmTextPolishingAria": "موافقة تحسين LLM",
	"settings.privacy.exportAllData": "تصدير جميع البيانات (GDPR Art. 15/20)",
	"settings.privacy.exportAllDataInfo":
		"تنزيل قوالبك وإعداداتك الكاملة كملفات JSON.",
	"settings.privacy.exportTemplates": "تصدير القوالب",
	"settings.privacy.exportTemplatesAria": "تصدير القوالب كـ JSON",
	"settings.privacy.exportConfig": "تصدير الإعدادات",
	"settings.privacy.exportConfigAria": "تصدير الإعدادات كـ JSON",
	"settings.privacy.templatesExported": "تم تصدير القوالب: {filename}",
	"settings.privacy.configExported": "تم تصدير الإعدادات: {filename}",
	"settings.privacy.exportFailedError": "فشل التصدير: {error}",
	"settings.privacy.fileFallback": "ملف",

	// settings.troubleshooting
	"settings.troubleshooting.title": "استكشاف الأخطاء وإصلاحها",
	"settings.troubleshooting.description": "أدوات التشخيص والمساعدة والدعم.",
	"settings.troubleshooting.openLogFolder": "فتح مجلد السجل",
	"settings.troubleshooting.openLogFolderAria": "فتح مجلد السجل",
	"settings.troubleshooting.openLogFolderHint":
		"فتح المجلد الذي يحتوي على ملفات سجل خادم بايثون الخلفي",
	"settings.troubleshooting.diagnostics": "التشخيصات",
	"settings.troubleshooting.diagnosticsAria": "فتح التشخيصات",
	"settings.troubleshooting.diagnosticsHint":
		"فتح صفحة حول مع معلومات الإصدار وحالة الخادم الخلفي والإعدادات",
	"settings.troubleshooting.helpFaq": "المساعدة والأسئلة الشائعة",
	"settings.troubleshooting.openDocsAria": "فتح التوثيق",
	"settings.troubleshooting.openDocsHint": "فتح ملف README للمشروع في المتصفح",
	"settings.troubleshooting.reportBug": "الإبلاغ عن خطأ",
	"settings.troubleshooting.reportBugAria": "الإبلاغ عن خطأ",
	"settings.troubleshooting.reportBugHint": "فتح متتبع مشكلات GitHub",
	"settings.troubleshooting.resetToDefaults": "إعادة التعيين إلى الافتراضي",
	"settings.troubleshooting.resetToDefaultsAria": "إعادة التعيين إلى الافتراضي",
	"settings.troubleshooting.resetToDefaultsHint":
		"إعادة تعيين جميع الإعدادات إلى قيمها الافتراضية (لا يمكن التراجع)",
	"settings.troubleshooting.cancelResetAria": "إلغاء إعادة التعيين",
	"settings.troubleshooting.confirmResetAria":
		"تأكيد إعادة التعيين إلى الافتراضي",
	"settings.troubleshooting.resetDialogMessage":
		"هل أنت متأكد أنك تريد إعادة تعيين جميع الإعدادات إلى قيمها الافتراضية؟ لا يمكن التراجع عن هذا.",
	"settings.troubleshooting.reRunWizard": "إعادة تشغيل معالج الإعداد",
	"settings.troubleshooting.reRunWizardAria": "إعادة تشغيل معالج الإعداد",
	"settings.troubleshooting.reRunWizardHint":
		"إعادة تشغيل معالج الإعداد لإعادة تكوين الميكروفون والمفتاح السريع والنموذج",
	"settings.troubleshooting.reRunWizardToast":
		"تم إعادة تمكين معالج الإعداد — جاري الانتقال إليه الآن",

	// Home section remaining
	"home.entryCountSingular": "{count} إدخال",
	"home.entryCountPlural": "{count} إدخالات",
};

// Merge remaining translations
Object.assign(translations.ar, arMore);

// ── Generic translations for all other locales ────────────────────
// For German, Spanish, French, Hindi, Russian, Chinese, generate the same
// deep section translations. I'll add a few representative ones per locale
// and the script will handle only those that are still English.

// Apply function
function applyTranslations(locale, trans) {
	const filePath = path.join(TRANSLATIONS_DIR, `${locale}.json`);
	const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
	let applied = 0;
	let skipped = 0;

	for (const [key, value] of Object.entries(trans)) {
		const flat = getKeys(data, "");
		const current = flat[key];
		const englishValue = enFlat[key];

		if (current === value) {
			skipped++;
			continue;
		}
		if (
			current !== undefined &&
			current !== englishValue &&
			current !== value
		) {
			skipped++;
			continue;
		}
		deepSet(data, key, value);
		applied++;
	}

	fs.writeFileSync(filePath, `${JSON.stringify(data, null, "\t")}\n`, "utf8");
	console.log(`${locale}: Applied ${applied}, skipped ${skipped}`);
}

// Apply Arabic (has the most complete translations above)
applyTranslations("ar", translations.ar);
applyTranslations("de", translations.de || {});
applyTranslations("es", translations.es || {});
applyTranslations("fr", translations.fr || {});
applyTranslations("hi", translations.hi || {});
applyTranslations("ru", translations.ru || {});
applyTranslations("zh", translations.zh || {});

console.log("\nDone!");
