#!/usr/bin/env python3
"""Apply pre-translated values to non-English locale files.

This script contains hand-curated translations for the keys that were
added to en.json in Round 0.  It ONLY overwrites values that are
currently identical to the English value (i.e. untranslated), so it
never clobbers existing human translations.

Usage:
    python scripts/apply_translations.py
"""

from __future__ import annotations

from pathlib import Path

# DR-49: shared helpers live in _i18n_common (canonical flatten / load /
# save / merge routines). This script previously duplicated load_json /
# save_json alongside add_i18n_keys.py and backfill_i18n_keys.py.
from _i18n_common import load_json, save_json

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "voice_typer/client/src/renderer/src/i18n/translations"
EN_FILE = TRANSLATIONS_DIR / "en.json"

# Hand-curated translations for the keys added in Round 0.
# Format: { locale: { dot_key: translated_value } }
# Brand names ("Voice Typer", "HuggingFace", "OpenAI", "Groq", "Deepgram",
# "NVIDIA Parakeet") are kept as-is.  Technical terms (API, URL, GPU, VRAM)
# are kept as-is in most locales.
TRANSLATIONS: dict[str, dict[str, str]] = {
    "ar": {
        "models.description": "تكوين محركات تحويل الكلام إلى نص",
        "models.use": "استخدام",
        "models.errors.unknown": "خطأ غير معروف",
        "models.snack.parakeetDepsRequired": "التبعيات المطلوبة لـ Parakeet. حمّلها أولاً.",
        "models.snack.notDownloaded": 'النموذج "{name}" غير محمّل بعد. حمّله أولاً.',
        "models.snack.usingModel": "استخدام النموذج: {name}",
        "models.snack.downloaded": "تم تحميل {name} بنجاح",
        "models.snack.downloadFailedName": "فشل تحميل {name}",
        "models.snack.downloadFailed": "فشل التحميل: {error}",
        "models.snack.cannotDeleteActive": "لا يمكن حذف النموذج النشط. بدّل إلى نموذج آخر أولاً.",
        "models.snack.deleted": "تم الحذف: {name}",
        "models.snack.deleteFailed": "فشل الحذف",
        "models.snack.deleteFailedError": "فشل الحذف: {error}",
        "models.snack.apiKeySaved": "تم حفظ مفتاح API لـ {provider}",
        "models.snack.consentGranted": "تم منح الموافقة لـ {provider} — سيتم إرسال الصوت إلى هذا المزود.",
        "models.snack.consentRevoked": "تم إلغاء الموافقة لـ {provider} — لن يتم إرسال الصوت.",
        "models.snack.hfConsentGranted": "تم منح الموافقة — ستستمر تحميلات النماذج من HuggingFace.",
        "models.snack.hfConsentRevoked": "تم إلغاء الموافقة — تم حظر تحميلات النماذج من HuggingFace.",
        "models.snack.resumeFailed": "فشل الاستئناف: {error}",
        "models.snack.pauseFailed": "فشل الإيقاف المؤقت: {error}",
        "models.snack.cancelled": "تم إلغاء التحميل. ستتم إعادة استخدام الملفات الجزئية عند إعادة المحاولة.",
        "models.snack.cancelFailed": "فشل الإلغاء: {error}",
        "models.test.needApiKey": "الرجاء إدخال مفتاح API أولاً",
        "models.test.connectionSuccessful": "نجح الاتصال — مفتاح API صالح.",
        "models.test.connectionFailed": "فشل الاتصال: {status} {statusText}",
        "models.test.endpointUnavailable": "تم حفظ مفتاح API — نقطة الاختبار غير متاحة لهذا المزود.",
        "models.test.connectionTestFailed": "فشل اختبار الاتصال: {error}",
        "models.benchmark.notImplemented": "لم يتم تنفيذ المعيار بعد.",
        "models.benchmark.title": "معيار الأداء للنموذج",
        "models.benchmark.description": "قارن أداء النموذج على نظامك",
        "models.benchmark.runAria": "تشغيل معيار الأداء للنموذج",
        "models.benchmark.running": "جارٍ التشغيل...",
        "models.benchmark.run": "تشغيل المعيار",
        "models.status.active": "نشط",
        "models.status.downloaded": "محمّل",
        "models.status.depsRequired": "التبعيات مطلوبة",
        "models.status.available": "متاح",
        "models.hfConsent.title": "موافقة تحميل HuggingFace مطلوبة",
        "models.hfConsent.description": "نماذج Whisper المحلية"
        "(tiny.en، base.en، إلخ) تقوم بتحميل الأوزان من huggingface.co عند أول استخدام. امنح الموافقة لتمكين تحميلات"
        "النماذج. لا يتم رفع أي صوت — يتم فقط جلب أوزان النموذج.",
        "models.hfConsent.grantAria": "منح موافقة تحميل HuggingFace",
        "models.hfConsent.grant": "منح الموافقة",
        "models.hfConsent.blockedHint": "تحميلات النماذج محظورة حتى تمنح الموافقة.",
        "models.progress.eta": "الوقت المتبقي {time}",
        "models.progress.paused": "· متوقف مؤقتاً",
        "models.download.resumeAria": "استئناف التحميل",
        "models.download.pauseAria": "إيقاف التحميل مؤقتاً",
        "models.download.resume": "استئناف",
        "models.download.pause": "إيقاف مؤقت",
        "models.download.cancelAria": "إلغاء تحميل النموذج",
        "models.download.cancel": "إلغاء",
        "models.download.depsAria": "تحميل التبعيات لـ {name}",
        "models.download.deps": "تحميل التبعيات",
        "models.card.parakeetLabel": "NVIDIA Parakeet TDT v3  ·  ",
        "models.card.size": "الحجم: {size}",
        "models.card.vram": "VRAM: ~{vram} ميجابايت",
        "models.card.multilingual": "متعدد اللغات",
        "models.card.englishOnly": "الإنجليزية فقط",
        "models.card.speedSuffix": "{rating} سرعة",
        "models.card.distilled": "  ·  مُقطّر",
        "models.card.activeAria": "نشط: {name}",
        "models.card.useAria": "استخدام {name}",
        "models.card.deleteAria": "حذف {name}",
        "models.cloud.title": "مزودو ASR السحابيون",
        "models.cloud.description": "تكوين خدمات التفريغ الصوتي السحابية",
        "models.cloud.providerSettings": "إعدادات {provider}",
        "models.cloud.apiKey": "مفتاح API",
        "models.cloud.apiKeyPlaceholder": "أدخل مفتاح API",
        "models.cloud.saveKeyAria": "حفظ مفتاح API لـ {provider}",
        "models.cloud.saveKey": "حفظ المفتاح",
        "models.cloud.testConnectionAria": "اختبار اتصال {provider}",
        "models.cloud.testConnection": "اختبار الاتصال",
        "models.cloud.consentTitle": "موافقة إرسال الصوت",
        "models.cloud.consentDescription": "عند تحديد هذا المزود كخلفية ASR النشطة، سيتم إرسال تسجيلاتك الصوتية إلى"
        "{provider} للتفريغ. امنح الموافقة لتمكين إرسال الصوت.",
        "models.cloud.statusLabel": "الحالة:",
        "models.cloud.consentGrantedStatus": "تم منح الموافقة — سيتم إرسال الصوت عندما يكون هذا المزود نشطاً.",
        "models.cloud.consentNotGrantedStatus": "لم يتم منح الموافقة — سيرفض هذا المزود التفريغ.",
        "models.cloud.consentAria": "منح موافقة إرسال الصوت لـ {provider}",
        "models.deleteDialog.title": "حذف النموذج",
        "models.deleteDialog.message": 'هل أنت متأكد أنك تريد حذف "{name}"؟ لا يمكن التراجع عن هذا الإجراء.',
        "history.undo": "تراجع",
        "history.clearAllAria": "مسح كل السجل",
    },
    "de": {
        "models.description": "Konfigurieren Sie Ihre Spracherkennungs-Engines",
        "models.use": "Verwenden",
        "models.errors.unknown": "Unbekannter Fehler",
        "models.status.active": "Aktiv",
        "models.status.downloaded": "Heruntergeladen",
        "models.status.depsRequired": "Abhängigkeiten erforderlich",
        "models.status.available": "Verfügbar",
        "models.benchmark.title": "Modell-Benchmark",
        "models.benchmark.description": "Vergleichen Sie die Modellleistung auf Ihrem System",
        "models.benchmark.runAria": "Modell-Benchmark ausführen",
        "models.benchmark.running": "Wird ausgeführt...",
        "models.benchmark.run": "Benchmark ausführen",
        "models.download.resume": "Fortsetzen",
        "models.download.pause": "Pause",
        "models.download.cancel": "Abbrechen",
        "models.download.deps": "Abhängigkeiten herunterladen",
        "models.hfConsent.grant": "Zustimmung erteilen",
        "models.cloud.apiKey": "API-Schlüssel",
        "models.cloud.apiKeyPlaceholder": "Geben Sie Ihren API-Schlüssel ein",
        "models.cloud.saveKey": "Schlüssel speichern",
        "models.cloud.testConnection": "Verbindung testen",
        "models.deleteDialog.title": "Modell löschen",
        "history.undo": "Rückgängig",
        "history.clearAllAria": "Gesamten Verlauf löschen",
    },
    "es": {
        "models.description": "Configura tus motores de reconocimiento de voz",
        "models.use": "Usar",
        "models.errors.unknown": "Error desconocido",
        "models.snack.parakeetDepsRequired": "Se requieren dependencias para Parakeet. Descarga primero.",
        "models.snack.notDownloaded": 'El modelo "{name}" no se ha descargado aún. Descárgalo primero.',
        "models.snack.usingModel": "Usando modelo: {name}",
        "models.snack.downloaded": "{name} descargado correctamente",
        "models.snack.downloadFailedName": "Error al descargar {name}",
        "models.snack.downloadFailed": "Error de descarga: {error}",
        "models.snack.cannotDeleteActive": "No se puede eliminar el modelo activo. Cambia a otro modelo primero.",
        "models.snack.deleted": "Eliminado: {name}",
        "models.snack.deleteFailed": "Error al eliminar",
        "models.snack.deleteFailedError": "Error al eliminar: {error}",
        "models.snack.apiKeySaved": "Clave API de {provider} guardada",
        "models.snack.consentGranted": "Consentimiento otorgado para {provider} — el audio se enviará a esteproveedor.",
        "models.snack.consentRevoked": "Consentimiento revocado para {provider} — el audio NO se enviará.",
        "models.snack.hfConsentGranted": "Consentimiento otorgado — las descargas de modelos desde HuggingFace"
        "continuarán.",
        "models.snack.hfConsentRevoked": "Consentimiento revocado — las descargas de modelos desde HuggingFace están"
        "bloqueadas.",
        "models.snack.resumeFailed": "Error al reanudar: {error}",
        "models.snack.pauseFailed": "Error al pausar: {error}",
        "models.snack.cancelled": "Descarga cancelada. Los archivos parciales se reutilizarán al reintentar.",
        "models.snack.cancelFailed": "Error al cancelar: {error}",
        "models.test.needApiKey": "Por favor, introduce una clave API primero",
        "models.test.connectionSuccessful": "Conexión exitosa — la clave API es válida.",
        "models.test.connectionFailed": "Error de conexión: {status} {statusText}",
        "models.test.endpointUnavailable": "Clave API guardada — endpoint de prueba no disponible para este proveedor.",
        "models.test.connectionTestFailed": "Error en la prueba de conexión: {error}",
        "models.benchmark.notImplemented": "Benchmark aún no implementado.",
        "models.benchmark.title": "Benchmark de Modelo",
        "models.benchmark.description": "Compara el rendimiento del modelo en tu sistema",
        "models.benchmark.runAria": "Ejecutar benchmark de modelo",
        "models.benchmark.running": "Ejecutando...",
        "models.benchmark.run": "Ejecutar Benchmark",
        "models.status.active": "Activo",
        "models.status.downloaded": "Descargado",
        "models.status.depsRequired": "Dependencias requeridas",
        "models.status.available": "Disponible",
        "models.hfConsent.title": "Se requiere consentimiento de descarga de HuggingFace",
        "models.hfConsent.description": "Los modelos Whisper"
        "locales (tiny.en, base.en, etc.) descargan pesos desde huggingface.co en el primer uso. Otorga"
        "consentimiento para habilitar las descargas de modelos. No se sube audio — solo se obtienen los pesos del "
        "modelo.",
        "models.hfConsent.grantAria": "Otorgar consentimiento de descarga de HuggingFace",
        "models.hfConsent.grant": "Otorgar consentimiento",
        "models.hfConsent.blockedHint": "Las descargas de modelos están bloqueadas hasta que otorgues consentimiento.",
        "models.progress.eta": "ETA {time}",
        "models.progress.paused": "· Pausado",
        "models.download.resumeAria": "Reanudar descarga",
        "models.download.pauseAria": "Pausar descarga",
        "models.download.resume": "Reanudar",
        "models.download.pause": "Pausar",
        "models.download.cancelAria": "Cancelar descarga del modelo",
        "models.download.cancel": "Cancelar",
        "models.download.depsAria": "Descargar dependencias para {name}",
        "models.download.deps": "Descargar Deps",
        "models.card.parakeetLabel": "NVIDIA Parakeet TDT v3  ·  ",
        "models.card.size": "Tamaño: {size}",
        "models.card.vram": "VRAM: ~{vram} MB",
        "models.card.multilingual": "Multilingüe",
        "models.card.englishOnly": "Solo inglés",
        "models.card.speedSuffix": "{rating} velocidad",
        "models.card.distilled": "  ·  destilado",
        "models.card.activeAria": "Activo: {name}",
        "models.card.useAria": "Usar {name}",
        "models.card.deleteAria": "Eliminar {name}",
        "models.cloud.title": "Proveedores ASR en la Nube",
        "models.cloud.description": "Configura servicios de transcripción basados en la nube",
        "models.cloud.providerSettings": "Ajustes de {provider}",
        "models.cloud.apiKey": "Clave API",
        "models.cloud.apiKeyPlaceholder": "Introduce tu clave API",
        "models.cloud.saveKeyAria": "Guardar clave API de {provider}",
        "models.cloud.saveKey": "Guardar Clave",
        "models.cloud.testConnectionAria": "Probar conexión de {provider}",
        "models.cloud.testConnection": "Probar Conexión",
        "models.cloud.consentTitle": "Consentimiento de transmisión de audio",
        "models.cloud.consentDescription": "Cuando este"
        "proveedor se selecciona como backend ASR activo, tus grabaciones de audio se enviarán a {provider} para"
        "transcripción. Otorga consentimiento para habilitar la transmisión de audio.",
        "models.cloud.statusLabel": "Estado:",
        "models.cloud.consentGrantedStatus": "Consentimiento otorgado — el audio se enviará cuando este proveedor"
        "esté activo.",
        "models.cloud.consentNotGrantedStatus": "Consentimiento no otorgado — este proveedor se negará a transcribir.",
        "models.cloud.consentAria": "Otorgar consentimiento de transmisión de audio para {provider}",
        "models.deleteDialog.title": "Eliminar Modelo",
        "models.deleteDialog.message": '¿Estás seguro de que quieres eliminar "{name}"? Esta acción no se puede'
        "deshacer.",
        "history.undo": "Deshacer",
        "history.clearAllAria": "Borrar todo el historial",
    },
    "fr": {
        "models.description": "Configurez vos moteurs de reconnaissance vocale",
        "models.use": "Utiliser",
        "models.errors.unknown": "Erreur inconnue",
        "models.status.active": "Actif",
        "models.status.downloaded": "Téléchargé",
        "models.status.depsRequired": "Dépendances requises",
        "models.status.available": "Disponible",
        "models.benchmark.title": "Benchmark du Modèle",
        "models.benchmark.description": "Comparez les performances du modèle sur votre système",
        "models.benchmark.run": "Lancer le Benchmark",
        "models.benchmark.running": "En cours...",
        "models.download.resume": "Reprendre",
        "models.download.pause": "Pause",
        "models.download.cancel": "Annuler",
        "models.download.deps": "Télécharger les Dépendances",
        "models.hfConsent.grant": "Accorder le consentement",
        "models.cloud.apiKey": "Clé API",
        "models.cloud.apiKeyPlaceholder": "Entrez votre clé API",
        "models.cloud.saveKey": "Enregistrer la Clé",
        "models.cloud.testConnection": "Tester la Connexion",
        "models.deleteDialog.title": "Supprimer le Modèle",
        "history.undo": "Annuler",
        "history.clearAllAria": "Effacer tout l'historique",
    },
    "hi": {
        "models.description": "अपने स्पीच-टू-टेक्स्ट इंजन कॉन्फ़िगर करें",
        "models.use": "उपयोग करें",
        "models.errors.unknown": "अज्ञात त्रुटि",
        "models.status.active": "सक्रिय",
        "models.status.downloaded": "डाउनलोड किया गया",
        "models.status.depsRequired": "निर्भरताएं आवश्यक हैं",
        "models.status.available": "उपलब्ध",
        "models.benchmark.title": "मॉडल बेंचमार्क",
        "models.benchmark.description": "अपने सिस्टम पर मॉडल के प्रदर्शन की तुलना करें",
        "models.benchmark.run": "बेंचमार्क चलाएं",
        "models.benchmark.running": "चल रहा है...",
        "models.download.resume": "फिर से शुरू करें",
        "models.download.pause": "रोकें",
        "models.download.cancel": "रद्द करें",
        "models.download.deps": "निर्भरताएं डाउनलोड करें",
        "models.hfConsent.grant": "सहमति दें",
        "models.cloud.apiKey": "API कुंजी",
        "models.cloud.apiKeyPlaceholder": "अपनी API कुंजी दर्ज करें",
        "models.cloud.saveKey": "कुंजी सहेजें",
        "models.cloud.testConnection": "कनेक्शन परीक्षण",
        "models.deleteDialog.title": "मॉडल हटाएं",
        "history.undo": "पूर्ववत करें",
        "history.clearAllAria": "सभी इतिहास साफ़ करें",
    },
    "ru": {
        "models.description": "Настройте движки преобразования речи в текст",
        "models.use": "Использовать",
        "models.errors.unknown": "Неизвестная ошибка",
        "models.status.active": "Активен",
        "models.status.downloaded": "Загружен",
        "models.status.depsRequired": "Требуются зависимости",
        "models.status.available": "Доступен",
        "models.benchmark.title": "Тест производительности модели",
        "models.benchmark.description": "Сравните производительность модели в вашей системе",
        "models.benchmark.run": "Запустить тест",
        "models.benchmark.running": "Выполняется...",
        "models.download.resume": "Возобновить",
        "models.download.pause": "Пауза",
        "models.download.cancel": "Отмена",
        "models.download.deps": "Загрузить зависимости",
        "models.hfConsent.grant": "Предоставить согласие",
        "models.cloud.apiKey": "API-ключ",
        "models.cloud.apiKeyPlaceholder": "Введите ваш API-ключ",
        "models.cloud.saveKey": "Сохранить ключ",
        "models.cloud.testConnection": "Проверить соединение",
        "models.deleteDialog.title": "Удалить модель",
        "history.undo": "Отменить",
        "history.clearAllAria": "Очистить всю историю",
    },
    "zh": {
        "models.description": "配置您的语音转文本引擎",
        "models.use": "使用",
        "models.errors.unknown": "未知错误",
        "models.status.active": "活跃",
        "models.status.downloaded": "已下载",
        "models.status.depsRequired": "需要依赖项",
        "models.status.available": "可用",
        "models.benchmark.title": "模型基准测试",
        "models.benchmark.description": "在您的系统上比较模型性能",
        "models.benchmark.run": "运行基准测试",
        "models.benchmark.running": "运行中...",
        "models.download.resume": "恢复",
        "models.download.pause": "暂停",
        "models.download.cancel": "取消",
        "models.download.deps": "下载依赖项",
        "models.hfConsent.grant": "授予同意",
        "models.cloud.apiKey": "API 密钥",
        "models.cloud.apiKeyPlaceholder": "输入您的 API 密钥",
        "models.cloud.saveKey": "保存密钥",
        "models.cloud.testConnection": "测试连接",
        "models.deleteDialog.title": "删除模型",
        "history.undo": "撤销",
        "history.clearAllAria": "清除所有历史记录",
    },
}


def set_nested(data: dict, dot_key: str, value: str) -> bool:
    """Set a nested value using dot notation. Returns True if set.

    Intermediate dictionaries are created automatically via ``setdefault`` so
    that translations for keys whose parent namespace doesn't yet exist in the
    locale file (e.g. a brand-new ``models.download.progressAria`` when the
    locale only has ``models.download.resume``) are no longer silently
    dropped. If a non-dict scalar is in the way, the function logs a warning
    and returns False instead of clobbering it — that would be a real
    data-loss bug worth surfacing loudly.
    """
    parts = dot_key.split(".")
    obj = data
    for part in parts[:-1]:
        existing = obj.get(part)
        if existing is None:
            obj = obj.setdefault(part, {})
        elif isinstance(existing, dict):
            obj = existing
        else:
            # A scalar value is occupying a path segment we need to descend
            # into. This is almost always a bug (a key was promoted from a
            # scalar to a nested object in en.json but the locale still has
            # the old scalar). Surface it instead of silently dropping the
            # translation.
            print(
                f"  WARNING: cannot set {dot_key!r} — path segment {part!r} "
                f"is a scalar ({existing!r}), not a dict. Translation skipped.",
                flush=True,
            )
            return False
    final_key = parts[-1]
    obj[final_key] = value
    return True


def get_nested(data: dict, dot_key: str):
    """Get a nested value using dot notation. Returns None if not found."""
    parts = dot_key.split(".")
    obj = data
    for part in parts:
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def main() -> int:
    en_data = load_json(EN_FILE)
    total_applied = 0

    for locale, translations in TRANSLATIONS.items():
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        if not loc_file.exists():
            print(f"  {locale}: FILE MISSING — skipped")
            continue
        loc_data = load_json(loc_file)
        applied = 0
        for dot_key, translated_value in translations.items():
            en_value = get_nested(en_data, dot_key)
            if en_value is None:
                continue  # key doesn't exist in en.json — skip
            current_value = get_nested(loc_data, dot_key)
            # Only overwrite if the current value is identical to English
            # (i.e. untranslated) OR missing.  Never clobber an existing
            # human translation.
            if (current_value is None or current_value == en_value) and set_nested(loc_data, dot_key, translated_value):
                applied += 1
        if applied > 0:
            save_json(loc_file, loc_data)
        print(f"  {locale}: applied {applied} translations")
        total_applied += applied

    print(f"\nTotal: {total_applied} translations applied")
    return 0 if total_applied >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
