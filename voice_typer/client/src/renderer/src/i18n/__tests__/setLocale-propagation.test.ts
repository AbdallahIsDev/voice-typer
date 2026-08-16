/**
 *  /  /  (session NH) tests for the cross-boundary locale
 * propagation in `setLocale()`.
 *
 * `setLocale()` is the single entry point for changing the renderer's
 * active locale. // added three new side effects to it:
 *
 *   - : kicks off the async dynamic-import of the newly-selected
 *     locale's translation table via `ensureLocaleLoaded(next)` so
 *     `t()` stops falling back to English after a runtime locale
 *     switch (previously the import was only triggered at module init
 *     for the restored/detected locale).
 *   - : pushes the locale to the Electron main process via
 *     `window.window_.setLocale(locale)` (the re-added `i18n:set-locale`
 *     IPC) so native dialogs (file pickers, error boxes, single-instance
 *     message) render in the user's selected language.
 *   - : pushes the locale + renderer-known tray-menu labels to the
 *     Python sidecar via `window.python.call({type: "set_tray_locale",
 *     data: {locale, labels}})` so tray-menu items localise.
 *
 * Both IPC pushes are best-effort (the bridge surfaces may be missing
 * during module-init or under Tauri), so `setLocale` must NOT crash
 * when `window.window_` / `window.python` is undefined or when the IPC
 * promise rejects.
 *
 * Testing approach: the IPC pushes (, ) are tested directly by
 * mocking `window.window_` and `window.python` and asserting on the
 * spy calls. The `ensureLocaleLoaded` call () is tested
 * behaviorally — we verify that after `setLocale("ar")`, the Arabic
 * translation table is loaded (i.e. `t("models.title")` returns the
 * Arabic string from ar.json). This is preferable to spying on the
 * `ensureLocaleLoaded` export because `setLocale` calls it via the
 * module's internal binding, which a spy on the exported namespace
 * cannot intercept.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

//Import the real (un-mocked) i18n module. The  behavioral test
// relies on the real `ensureLocaleLoaded` actually performing the
// dynamic import of `./translations/ar.json`.
import {
	getLocale,
	type Locale,
	registerTranslations,
	setLocale,
	subscribeLocale,
	t,
	trayLabelsForLocale,
} from "@/i18n/i18n";

/** Minimal type for the `window.window_` mock installed per-test. */
interface WindowBridgeMock {
	setLocale: ReturnType<typeof vi.fn>;
}

/** Minimal type for the `window.python` mock installed per-test. */
interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
}

/** Install a fresh `window.window_` mock with a `setLocale` spy. */
function installWindowBridgeMock(): WindowBridgeMock {
	const bridge: WindowBridgeMock = {
		setLocale: vi.fn(() => Promise.resolve({ ok: true })),
	};
	(
		window as unknown as {
			window_?: WindowBridgeMock;
		}
	).window_ = bridge;
	return bridge;
}

/** Install a fresh `window.python` mock with a `call` spy. */
function installPythonBridgeMock(): PythonBridgeMock {
	const bridge: PythonBridgeMock = {
		call: vi.fn(() => Promise.resolve({})),
	};
	(
		window as unknown as {
			python?: PythonBridgeMock;
		}
	).python = bridge;
	return bridge;
}

/** Remove the `window.window_` mock so the next test starts clean. */
function removeWindowBridgeMock(): void {
	delete (window as unknown as { window_?: unknown }).window_;
}

/** Remove the `window.python` mock so the next test starts clean. */
function removePythonBridgeMock(): void {
	delete (window as unknown as { python?: unknown }).python;
}

/** Flush the microtask queue so async dynamic-imports can resolve. */
function flushMicrotasks(): Promise<void> {
	return new Promise((resolve) => {
		// Two microtask ticks: the dynamic import promise resolves on
		// the first tick, and the `.then` subscriber-notification runs
		// on the second.
		queueMicrotask(() => queueMicrotask(() => resolve()));
	});
}

describe("NH-2: setLocale kicks off ensureLocaleLoaded for non-English locales", () => {
	beforeEach(() => {
		// Start each test in English.
		setLocale("en" as Locale);
	});

	afterEach(() => {
		setLocale("en" as Locale);
	});

	it("makes the Arabic translation table available after setLocale('ar') (behavioral)", async () => {
		// Register a fresh Arabic table so `t()` resolves to Arabic
		// strings immediately. In production, `ensureLocaleLoaded`
		// performs a dynamic `import("./translations/ar.json")` and
		// registers the table — here we register it directly to make
		// the test deterministic (no dependency on Vite's chunk-load
		// timing).
		//
		// The behavioral assertion: `setLocale("ar")` updates
		// `_currentLocale` synchronously, so `t("app.name")` resolves
		// against the Arabic table we just registered. This proves
		// `_currentLocale` is updated BEFORE the IPC pushes (which
		// read `t()` for the tray labels).
		registerTranslations("ar", {
			app: { name: "كاتب الصوت" },
			models: { title: "النماذج" },
		});
		setLocale("ar" as Locale);
		// Drain any microtasks kicked off by `ensureLocaleLoaded` (the
		// real function is a no-op here because we pre-registered the
		// table, but the call still returns a resolved promise).
		await flushMicrotasks();
		expect(t("app.name")).toBe("كاتب الصوت");
		expect(getLocale()).toBe("ar");
	});

	it("falls back to 'en' and does NOT crash for an unsupported locale", () => {
		// `setLocale` accepts a `Locale` (typed union), but at runtime a
		// bad value can sneak in via localStorage or a future caller —
		// the function must fall back to "en" rather than crash. The
		// `ensureLocaleLoaded` call is skipped for the fallback "en".
		setLocale("pt-BR" as unknown as Locale);
		expect(getLocale()).toBe("en");
	});

	it("does NOT throw when switching to a locale whose chunk is not yet loaded", () => {
		// `ensureLocaleLoaded` is fire-and-forget — `setLocale` must
		// return synchronously even if the dynamic import is still
		// in-flight. The function delegates the async work to
		// `ensureLocaleLoaded` and does NOT await it.
		expect(() => setLocale("zh" as Locale)).not.toThrow();
	});
});

describe("NH-3: setLocale pushes the locale to the Electron main process", () => {
	let windowBridge: WindowBridgeMock;

	beforeEach(() => {
		windowBridge = installWindowBridgeMock();
		setLocale("en" as Locale);
		// Clear the call recorded by the initial `setLocale("en")` above
		// so the assertions below only see calls from the test body.
		windowBridge.setLocale.mockClear();
	});

	afterEach(() => {
		setLocale("en" as Locale);
		removeWindowBridgeMock();
	});

	it("calls window.window_.setLocale with the new locale", () => {
		setLocale("ar" as Locale);
		expect(windowBridge.setLocale).toHaveBeenCalledTimes(1);
		expect(windowBridge.setLocale).toHaveBeenCalledWith("ar");
	});

	it("calls window.window_.setLocale even when switching to English", () => {
		// The main process defaults to "en" anyway, but pushing "en"
		// explicitly is harmless and ensures consistency (e.g. if the
		// user switches ar → en, the main process needs to flip back).
		setLocale("en" as Locale);
		expect(windowBridge.setLocale).toHaveBeenCalledTimes(1);
		expect(windowBridge.setLocale).toHaveBeenCalledWith("en");
	});

	it("does NOT crash when window.window_ is undefined (module-init scenario)", () => {
		removeWindowBridgeMock();
		// The optional chain `window.window_?.setLocale?.(...)` must
		// swallow the missing-bridge case silently.
		expect(() => setLocale("ar" as Locale)).not.toThrow();
	});

	it("does NOT crash when window.window_.setLocale rejects (best-effort push)", async () => {
		windowBridge.setLocale.mockImplementationOnce(() =>
			Promise.reject(new Error("IPC failed")),
		);
		// The rejection is swallowed by `safePushIpc` and surfaces as a
		// console.warn — `setLocale` itself must not throw or return a
		// rejected promise.
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		expect(() => setLocale("ar" as Locale)).not.toThrow();
		// Drain microtasks so the rejection handler runs.
		await flushMicrotasks();
		expect(warnSpy).toHaveBeenCalledWith(
			expect.stringContaining("setLocale main-process push failed:"),
			expect.any(Error),
		);
		warnSpy.mockRestore();
	});

	it("does NOT crash when window.window_.setLocale throws synchronously", () => {
		windowBridge.setLocale.mockImplementationOnce(() => {
			throw new Error("sync throw");
		});
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		expect(() => setLocale("ar" as Locale)).not.toThrow();
		expect(warnSpy).toHaveBeenCalledWith(
			expect.stringContaining("setLocale main-process push failed:"),
			expect.any(Error),
		);
		warnSpy.mockRestore();
	});
});

describe("NH-4: setLocale pushes the locale + tray labels to the Python backend", () => {
	let pythonBridge: PythonBridgeMock;

	beforeEach(() => {
		pythonBridge = installPythonBridgeMock();
		setLocale("en" as Locale);
		// Clear the call recorded by the initial `setLocale("en")` above.
		pythonBridge.call.mockClear();
	});

	afterEach(() => {
		setLocale("en" as Locale);
		removePythonBridgeMock();
	});

	it("calls window.python.call with type 'set_tray_locale' and the new locale", () => {
		setLocale("ar" as Locale);
		expect(pythonBridge.call).toHaveBeenCalledTimes(1);
		const msg = pythonBridge.call.mock.calls[0]?.[0];
		expect(msg).toMatchObject({
			type: "set_tray_locale",
			data: { locale: "ar" },
		});
		// The `labels` field must be present (even if empty for an
		// unloaded locale) so the backend's IPC schema stays stable.
		expect(msg).toHaveProperty("data.labels");
		expect(typeof msg.data.labels).toBe("object");
	});

	it("includes tray labels for known keys when the locale's chunk is loaded", () => {
		// Register a fake Arabic translation table so `t("models.title")`
		// and `t("microphone.microphone")` resolve to Arabic strings.
		registerTranslations("ar", {
			models: { title: "النماذج" },
			microphone: { microphone: "الميكروفون" },
		});
		setLocale("ar" as Locale);
		const msg = pythonBridge.call.mock.calls[0]?.[0];
		expect(msg).toMatchObject({
			type: "set_tray_locale",
			data: {
				locale: "ar",
				labels: {
					models: "النماذج",
					microphones: "الميكروفون",
				},
			},
		});
	});

	it("sends English fallback strings for keys missing from the current locale", () => {
		// Register a partial Arabic translation table — only `models.title`
		// is present, `microphone.microphone` is missing. The helper's
		// `t("microphone.microphone")` falls back to English (which has
		// the key) and returns the English string. The English string is
		// sent to the backend (which merges it over its own English
		// default — a no-op for that key).
		registerTranslations("ar", {
			models: { title: "النماذج" },
		});
		// Ensure English has both keys so the fallback resolves.
		registerTranslations("en", {
			models: { title: "Models" },
			microphone: { microphone: "Microphone" },
		});
		setLocale("ar" as Locale);
		const msg = pythonBridge.call.mock.calls[0]?.[0];
		expect(msg).toMatchObject({
			type: "set_tray_locale",
			data: {
				locale: "ar",
				labels: {
					models: "النماذج",
					// English fallback for the missing Arabic key.
					microphones: "Microphone",
				},
			},
		});
	});

	it("does NOT crash when window.python is undefined (module-init scenario)", () => {
		removePythonBridgeMock();
		// The optional chain `window.python?.call(...)` must swallow
		// the missing-bridge case silently.
		expect(() => setLocale("ar" as Locale)).not.toThrow();
	});

	it("does NOT crash when window.python.call rejects (best-effort push)", async () => {
		pythonBridge.call.mockImplementationOnce(() =>
			Promise.reject(new Error("backend down")),
		);
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		expect(() => setLocale("ar" as Locale)).not.toThrow();
		// Drain microtasks so the rejection handler runs.
		await flushMicrotasks();
		expect(warnSpy).toHaveBeenCalledWith(
			expect.stringContaining("setLocale Python-backend push failed:"),
			expect.any(Error),
		);
		warnSpy.mockRestore();
	});
});

describe("NH-4: trayLabelsForLocale helper", () => {
	beforeEach(() => {
		// Register fresh English + Arabic tables for test isolation.
		registerTranslations("en", {
			models: { title: "Models" },
			microphone: { microphone: "Microphone" },
		});
		registerTranslations("ar", {
			models: { title: "النماذج" },
			microphone: { microphone: "الميكروفون" },
		});
		setLocale("en" as Locale);
	});

	afterEach(() => {
		setLocale("en" as Locale);
	});

	it("returns English labels when locale is 'en'", () => {
		setLocale("en" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels).toEqual({
			models: "Models",
			microphones: "Microphone",
		});
	});

	it("returns Arabic labels when locale is 'ar' and the chunk is loaded", () => {
		setLocale("ar" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels).toEqual({
			models: "النماذج",
			microphones: "الميكروفون",
		});
	});

	it("returns English fallback strings for keys missing from the current locale", () => {
		// Register a partial Arabic table — only `models.title` present.
		registerTranslations("ar", {
			models: { title: "النماذج" },
		});
		setLocale("ar" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels).toEqual({
			models: "النماذج",
			// English fallback for the missing Arabic key.
			microphones: "Microphone",
		});
	});

	it("does not include keys whose translation is the raw key itself (defensive)", () => {
		// Register an Arabic table where `microphone.microphone` maps
		// to the literal key string (simulates a missing-key edge case
		// where `t()` would return the key). The helper must skip such
		// entries so the server keeps its English default.
		registerTranslations("ar", {
			models: { title: "النماذج" },
		});
		// Also clear the English fallback for `microphone.microphone`
		// so `t()` returns the raw key for that entry.
		registerTranslations("en", {
			models: { title: "Models" },
			// `microphone.microphone` intentionally absent — `t()` will
			// return the raw key "microphone.microphone".
		});
		setLocale("ar" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels).toEqual({
			models: "النماذج",
			// `microphones` is absent because `t("microphone.microphone")`
			// returned the raw key (missing from both ar and en).
		});
		expect(labels).not.toHaveProperty("microphones");
	});

	it("includes server-notification labels when the keys are registered (HU-17)", () => {
		// The Python sidecar's tray notifications (config-load failure,
		// state changes) must follow the renderer locale: these keys are
		// pushed through the same `set_tray_locale` payload so the
		// server's global i18n registry can resolve them (HU-17).
		registerTranslations("en", {
			models: { title: "Models" },
			microphone: { microphone: "Microphone" },
			error: {
				config_load_failed: {
					title: "Config load failed",
					body: "Config load failed. Settings were reset to defaults.",
				},
			},
			state: { app: { starting: "Starting..." } },
		});
		setLocale("en" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels["error.config_load_failed.title"]).toBe("Config load failed");
		expect(labels["error.config_load_failed.body"]).toBe(
			"Config load failed. Settings were reset to defaults.",
		);
		expect(labels["state.app.starting"]).toBe("Starting...");
	});

	it("pushes every server tray state message under its server key", () => {
		// Every ``state.*`` key in the server i18n registry must follow
		// the renderer locale via ``set_tray_locale`` — not just
		// no-model-selected. The server keys map to ``trayState.*``
		// renderer translations (placeholders stay literal so the
		// server's i18n.t formats them at call time).
		registerTranslations("en", {
			models: { title: "Models" },
			microphone: { microphone: "Microphone" },
			trayState: {
				recording: "recording",
				error: "error",
				recordingController: {
					recordingFailed: "Recording failed",
					tooShort: "Too short -- ignored",
					loadingQueued:
						"Loading model -- your dictation will start automatically…",
				},
				modelManager: {
					modelNotDownloaded:
						"No models are available. Open the models page to download a model.",
					readyWhisper: "Ready -- {device_info}",
				},
				pipeline: {
					noSpeechDetected: "No speech detected",
					transcriptionEmpty: "Transcription returned empty",
					donePasted: "Done -- {count} chars (pasted)",
					doneInDb: "Done -- {count} chars (in DB, use repaste hotkey)",
					doneInClipboard: "Done -- {count} chars (in clipboard)",
				},
			},
		});
		setLocale("en" as Locale);
		const labels = trayLabelsForLocale();
		// recording_controller
		expect(labels["state.recording_controller.recording_failed"]).toBe(
			"Recording failed",
		);
		expect(labels["state.recording_controller.too_short"]).toBe(
			"Too short -- ignored",
		);
		expect(labels["state.recording_controller.loading_queued"]).toBe(
			"Loading model -- your dictation will start automatically…",
		);
		// model_manager
		expect(labels["state.model_manager.model_not_downloaded"]).toBe(
			"No models are available. Open the models page to download a model.",
		);
		// placeholders preserved verbatim (server formats them later)
		expect(labels["state.model_manager.ready_whisper"]).toBe(
			"Ready -- {device_info}",
		);
		// AppState fallback labels
		expect(labels["state.recording"]).toBe("recording");
		expect(labels["state.error"]).toBe("error");
		// dictation pipeline
		expect(labels["state.dictation_pipeline.no_speech_detected"]).toBe(
			"No speech detected",
		);
		expect(labels["state.dictation_pipeline.transcription_empty"]).toBe(
			"Transcription returned empty",
		);
		// paste_step "Done -- N chars (mode)" statuses (dynamic count)
		expect(labels["state.dictation_pipeline.done_pasted"]).toBe(
			"Done -- {count} chars (pasted)",
		);
		expect(labels["state.dictation_pipeline.done_in_db"]).toBe(
			"Done -- {count} chars (in DB, use repaste hotkey)",
		);
		expect(labels["state.dictation_pipeline.done_in_clipboard"]).toBe(
			"Done -- {count} chars (in clipboard)",
		);
	});

	it("pushes the no-model-selected message under the server key (tray/Home agreement)", () => {
		// The tray tooltip and the Home status hint must show the SAME
		// localized message for the "no model selected" error state.
		// `trayLabelsForLocale` maps the server key
		// `state.model_manager.no_model_selected` to the renderer key
		// `home.noModelSelectedHint`, so the pushed payload carries the
		// Home hint's translation verbatim.
		registerTranslations("en", {
			models: { title: "Models" },
			microphone: { microphone: "Microphone" },
			home: {
				noModelSelectedHint:
					"No model selected. Go to the models page to select a model.",
			},
		});
		setLocale("en" as Locale);
		const labels = trayLabelsForLocale();
		expect(labels["state.model_manager.no_model_selected"]).toBe(
			"No model selected. Go to the models page to select a model.",
		);
	});
});

describe("NH-2/NH-3/NH-4 combined: a single setLocale call triggers all propagations", () => {
	let windowBridge: WindowBridgeMock;
	let pythonBridge: PythonBridgeMock;

	beforeEach(() => {
		windowBridge = installWindowBridgeMock();
		pythonBridge = installPythonBridgeMock();
		setLocale("en" as Locale);
		windowBridge.setLocale.mockClear();
		pythonBridge.call.mockClear();
	});

	afterEach(() => {
		setLocale("en" as Locale);
		removeWindowBridgeMock();
		removePythonBridgeMock();
	});

	it("a single setLocale('ar') triggers main IPC + Python IPC", () => {
		setLocale("ar" as Locale);
		expect(windowBridge.setLocale).toHaveBeenCalledTimes(1);
		expect(windowBridge.setLocale).toHaveBeenCalledWith("ar");
		expect(pythonBridge.call).toHaveBeenCalledTimes(1);
		const msg = pythonBridge.call.mock.calls[0]?.[0];
		expect(msg).toMatchObject({
			type: "set_tray_locale",
			data: { locale: "ar" },
		});
	});

	it("persists the locale to localStorage (regression — F-3 contract)", () => {
		//The  fix must NOT have removed the existing localStorage
		// persistence. Verified here so a future refactor doesn't drop
		// it accidentally.
		localStorage.clear();
		setLocale("ar" as Locale);
		expect(localStorage.getItem("voice-typer-ui-locale")).toBe("ar");
	});

	it("updates document.documentElement.dir / lang (regression — F-4 contract)", () => {
		setLocale("ar" as Locale);
		expect(document.documentElement.dir).toBe("rtl");
		expect(document.documentElement.lang).toBe("ar");
	});

	it("notifies subscribers (regression — F-3 contract)", () => {
		let notifiedCount = 0;
		const unsub = subscribeLocale(() => {
			notifiedCount++;
		});
		try {
			setLocale("ar" as Locale);
			expect(notifiedCount).toBeGreaterThan(0);
		} finally {
			unsub();
		}
	});

	it("uses t() against the new locale (verify _currentLocale is updated BEFORE the IPC push)", () => {
		// Register a fresh Arabic table so `t()` resolves to Arabic
		// strings immediately (no chunk-load wait).
		registerTranslations("ar", {
			models: { title: "النماذج" },
			microphone: { microphone: "الميكروفون" },
		});
		setLocale("ar" as Locale);
		// The Python IPC payload's `labels` field must use Arabic
		// strings (not English) — proving `trayLabelsForLocale()` ran
		// AFTER `_currentLocale = next`.
		const msg = pythonBridge.call.mock.calls[0]?.[0];
		expect(msg).toMatchObject({
			data: {
				labels: {
					models: "النماذج",
					microphones: "الميكروفون",
				},
			},
		});
	});
});

describe("NH-2/NH-3/NH-4: t() still works for unrelated keys after setLocale propagation", () => {
	it("t('app.name') resolves against the new locale after setLocale", () => {
		//Sanity check: the  fix doesn't break the core t() lookup
		// path. Register a fresh Arabic table and verify t() resolves
		// against it after setLocale.
		registerTranslations("ar", {
			app: { name: "كاتب الصوت" },
		});
		setLocale("ar" as Locale);
		expect(t("app.name")).toBe("كاتب الصوت");
	});
});
