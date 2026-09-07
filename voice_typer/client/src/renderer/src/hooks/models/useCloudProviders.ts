/**
 * useCloudProviders — cloud-provider API keys + consent slice.
 *
 *  (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * cloud-provider test results and the actions that persist API keys +
 * consent flags:
 *   • `saveApiKey` — persists the per-provider API key via `updateConfig`
 *     + surfaces a localised success snackbar. Bails out (and surfaces
 *     an info snackbar) when the key is empty or unchanged from the
 *     persisted value — prevents silently clobbering stored keys
 *     with the empty string that `safeApiKey` substitutes for the
 *     `<redacted>` sentinel on every config fetch.
 *   • `setCloudConsent` — persists a cloud-provider consent flag +
 *     optimistically updates the local config snapshot (so the UI
 *     flips immediately without waiting for the `config_changed`
 *     event round-trip). HuggingFace consent is NOT handled here —
 *     it's granted through the shared point-of-use consent dialog
 *     (`lib/consentGate.ts`) opened by the download flow in
 *     `useModelLifecycle.handleDownloadModel`, and revoked via the
 *     Settings privacy row.
 *   • `testConnection` — routes the cloud-provider key verification
 *     through the backend IPC `test_cloud_connection` command so the
 *     API key never leaves the Python process (C-DATA-1 offline-app
 *     compliance — ). Sets a `"pending"` status at the start so
 *     the UI can show a spinner + disable the Test button.
 *   • `clearTestResult` — clears the test result for a single
 *     provider. Wired to the API-key Input's onChange so stale
 *     "Success" badges don't linger after the user edits the key.
 *
 * The three module-level helpers (`consentKeyFor`, `apiKeyConfigField`,
 * `safeApiKey`) live in this file. `safeApiKey` is re-exported so
 * `useModelConfig.loadConfig` can call it when seeding `apiKeys` from
 * the freshly-fetched config — without duplicating the
 * redaction-sentinel stripping logic.
 *
 * `apiKeys` + `setApiKeys` are received as args (state owned by
 * `useModelConfig` because `loadConfig` populates them — see that
 * hook's docstring for the rationale). `setConfig` + `updateConfig`
 * come from `useModelConfig` too. `config` is also forwarded so
 * `saveApiKey`'s unchanged-guard can compare the in-memory input
 * value against the persisted (redacted-or-not) config field.
 */

import { useCallback, useState } from "react";
import type { PythonCall } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { formatErrorMessage, getProviderLabel } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// ── Types ─────────────────────────────────────────────────────────────

export interface ApiTestResult {
	message: string;
	// `"pending"` is set at the start of `testConnection` so the UI can
	// show a spinner + disable the Test button. The status transitions
	// to `"success"` / `"failure"` / `"info"` on terminal branches.
	status: "pending" | "success" | "failure" | "info";
}

// Type alias for the IPC `call` function (matches the pattern used by
// useModelConfig, useModelFolder, useModelDownload, etc.).

interface UseCloudProvidersArgs {
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	setConfig: React.Dispatch<React.SetStateAction<VoiceTyperConfig | null>>;
	config: VoiceTyperConfig | null;
	apiKeys: Record<string, string>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
	call: PythonCall;
}

export interface UseCloudProvidersResult {
	testResults: Record<string, ApiTestResult>;
	saveApiKey: (provider: string) => Promise<void>;
	setCloudConsent: (provider: string, granted: boolean) => Promise<void>;
	testConnection: (provider: string) => Promise<void>;
	/** Clear the test result for a single provider. Wired to the API-key
	 * Input's onChange so stale "Success" badges don't linger after
	 * the user edits the key. */
	clearTestResult: (provider: string) => void;
}

// ── Helpers (module-level — `safeApiKey` is re-exported) ──────────────

/**
 *  fix #6 helper: translate the cloud-provider key into the
 * matching `cloud_*_consent` config field. Returns the config key
 * (typed as a keyof VoiceTyperConfig so callers can index safely).
 * Exported so presentational consumers (CloudProvidersPanel) reuse
 * the SAME mapping instead of re-declaring a drifting duplicate.
 */
export function consentKeyFor(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "cloud_openai_consent";
	if (provider === "groq") return "cloud_groq_consent";
	return "cloud_deepgram_consent";
}

/**
 *  fix #6 helper: translate the cloud-provider key into the
 * matching `*_api_key` config field.
 */
function apiKeyConfigField(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "openai_api_key";
	if (provider === "groq") return "groq_api_key";
	return "deepgram_api_key";
}

/**
 * Strip the "<redacted>" sentinel that the backend substitutes for
 * saved API keys in `get_config` responses. The renderer never
 * displays the redacted marker — it shows an empty input field
 * instead, so the user can re-enter the key without confusion.
 *
 * Re-exported (not just used internally) because `useModelConfig.
 * loadConfig` calls it when seeding `apiKeys` from the freshly-fetched
 * config.
 */
export function safeApiKey(value: string | undefined | null): string {
	return value && value !== "<redacted>" ? value : "";
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useCloudProviders({
	showSnack,
	setConfig,
	config,
	apiKeys,
	updateConfig,
	call,
}: UseCloudProvidersArgs): UseCloudProvidersResult {
	const [testResults, setTestResults] = useState<Record<string, ApiTestResult>>(
		{},
	);

	// Clear the test result for a single provider. Used by the API-key
	// Input's onChange handler so a stale "Success" badge doesn't linger
	// after the user edits the key.
	const clearTestResult = useCallback((provider: string) => {
		setTestResults((prev) => {
			if (!prev[provider]) return prev;
			const next = { ...prev };
			delete next[provider];
			return next;
		});
	}, []);

	// ── Action: saveApiKey / setCloudConsent ─────────────────────────
	//
	// Bail out (and surface an info snackbar) when:
	//   • `key.trim() === ""` — the input is empty (the user clicked
	//     Save without typing anything). This is the most dangerous case
	//     because `safeApiKey` substitutes `""` for the `<redacted>`
	//     sentinel on every config fetch — so the input is always empty
	//     after navigating away and back, even when a key IS stored.
	//     Without this guard, clicking Save would overwrite the stored
	//     secret with `""`.
	//   • `key === persistedKey` — the user re-typed the exact same key.
	//     No-op (avoids a redundant IPC round-trip + a misleading "saved"
	//     toast). The persisted key is read from `config` so we compare
	//     against the value the backend last acknowledged.
	const saveApiKey = useCallback(
		async (provider: string) => {
			const key = apiKeys[provider] ?? "";
			if (!key.trim()) {
				showSnack(t("models.snack.apiKeyEmpty"), "info");
				return;
			}
			const configKey = apiKeyConfigField(provider);
			const persistedRaw = config?.[configKey] as string | undefined;
			const persisted = safeApiKey(persistedRaw);
			if (persisted && key === persisted) {
				showSnack(t("models.snack.apiKeyUnchanged"), "info");
				return;
			}
			const updates = { [configKey]: key } as Partial<VoiceTyperConfig>;
			await updateConfig(updates);
			showSnack(
				t("models.snack.apiKeySaved", {
					provider: getProviderLabel(provider),
				}),
				"success",
			);
		},
		[apiKeys, config, showSnack, updateConfig],
	);

	const setCloudConsent = useCallback(
		async (provider: string, granted: boolean) => {
			const configKey = consentKeyFor(provider);
			const updates = { [configKey]: granted } as Partial<VoiceTyperConfig>;
			await updateConfig(updates);
			setConfig((prev) => (prev ? { ...prev, [configKey]: granted } : prev));
			showSnack(
				granted
					? t("models.snack.consentGranted", {
							provider: getProviderLabel(provider),
						})
					: t("models.snack.consentRevoked", {
							provider: getProviderLabel(provider),
						}),
				granted ? "success" : "warning",
			);
		},
		[showSnack, updateConfig, setConfig],
	);

	// ── Action: testConnection ──────────────────────────────────────
	//
	// Sets a `"pending"` status at the start so the UI can disable the
	// Test button + show an inline spinner. The status transitions to
	// `"success"` / `"failure"` / `"info"` on terminal branches.
	//
	//previously the renderer-side ``fetch`` to the cloud
	// provider's API leaked the user's API key through the
	// ``Authorization`` header on a cross-origin request — and a
	// CORS / network failure surfaced as an opaque ``TypeError:
	// Failed to fetch`` with no actionable message. The full fix
	// (route the test through a backend IPC ``test_cloud_connection``
	// that keeps the key inside the Python process) requires backend
	// changes that are out of scope for GROUP 3; this hardening
	// improves the renderer-side error handling only:
	//   • detect ``TypeError`` from ``fetch`` (CORS / DNS / network)
	//     and surface a specific message;
	//   • never log the API key — ``formatErrorMessage`` only
	//     extracts the error's ``message`` field, so the
	//     ``Authorization`` header value never enters the log.
	const testConnection = useCallback(
		async (provider: string) => {
			const key = apiKeys[provider] ?? "";
			if (!key) {
				setTestResults((prev) => ({
					...prev,
					[provider]: { message: t("models.test.needApiKey"), status: "info" },
				}));
				return;
			}
			// Mark pending immediately so the UI can disable the button + show a
			// spinner. The pending status is overwritten by the terminal
			// success/failure/info branches below.
			setTestResults((prev) => ({
				...prev,
				[provider]: {
					message: "Testing…",
					status: "pending",
				},
			}));
			try {
				await saveApiKey(provider);
				// Route the connection test through the Python backend IPC
				// (test_cloud_connection) so the API key never leaves the
				// Python process and the renderer stays network-free
				// (C-DATA-1 offline-app compliance). The backend handler
				// reads the key from the live Config, makes the HTTP probe
				// using urllib, and returns { ok, status, message }.
				const result = await call<{
					ok: boolean;
					status: number;
					message: string;
				}>("test_cloud_connection", { provider });
				if (result.ok) {
					setTestResults((prev) => ({
						...prev,
						[provider]: {
							message: t("models.test.connectionSuccessful"),
							status: "success",
						},
					}));
				} else {
					setTestResults((prev) => ({
						...prev,
						[provider]: {
							message: t("models.test.connectionFailed", {
								status: String(result.status),
								statusText: result.message,
							}),
							status: "failure",
						},
					}));
				}
			} catch (err) {
				setTestResults((prev) => ({
					...prev,
					[provider]: {
						message: t("models.test.connectionTestFailed", {
							error: formatErrorMessage(err),
						}),
						status: "failure",
					},
				}));
			}
		},
		[apiKeys, saveApiKey, call],
	);

	return {
		testResults,
		saveApiKey,
		setCloudConsent,
		testConnection,
		clearTestResult,
	};
}
