/**
 * useCloudProviders — cloud-provider API keys + consent slice.
 *
 * DT-34 (Phase 4.5 spaghetti split): extracted from the former
 * `useModelLifecycle.ts` (995-line) monolith. This sub-hook owns the
 * cloud-provider test results and the actions that persist API keys +
 * consent flags:
 *   • `saveApiKey` — persists the per-provider API key via `updateConfig`
 *     + surfaces a localised success snackbar.
 *   • `setCloudConsent` / `setHuggingFaceConsent` — persist the
 *     consent flag + optimistically update the local config snapshot
 *     (so the UI flips immediately without waiting for the
 *     `config_changed` event round-trip).
 *   • `handleGrantConsent` — thin wrapper that grants HuggingFace
 *     consent (used by the consent banner's "Grant" button).
 *   • `testConnection` — fetches the provider's API endpoint to verify
 *     the key works. BG-50: cross-origin `fetch` failures throw an
 *     opaque `TypeError`; we detect that and surface a specific
 *     "network blocked the request" message instead of the generic
 *     "Failed to fetch".
 *
 * The four module-level helpers (`consentKeyFor`, `apiKeyConfigField`,
 * `safeApiKey`, `testEndpointFor`) live in this file. `safeApiKey` is
 * re-exported so `useModelConfig.loadConfig` can call it when seeding
 * `apiKeys` from the freshly-fetched config — without duplicating the
 * redaction-sentinel stripping logic.
 *
 * `apiKeys` + `setApiKeys` are received as args (state owned by
 * `useModelConfig` because `loadConfig` populates them — see that
 * hook's docstring for the rationale). `setConfig` + `updateConfig`
 * come from `useModelConfig` too.
 */
import { useCallback, useState } from "react";
import { t } from "@/i18n/i18n";
import { formatErrorMessage, getProviderLabel } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";

// ── Types ─────────────────────────────────────────────────────────────

export interface ApiTestResult {
	message: string;
	status: "success" | "failure" | "info";
}

interface UseCloudProvidersArgs {
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	setConfig: React.Dispatch<React.SetStateAction<VoiceTyperConfig | null>>;
	apiKeys: Record<string, string>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => Promise<void>;
}

export interface UseCloudProvidersResult {
	testResults: Record<string, ApiTestResult>;
	saveApiKey: (provider: string) => Promise<void>;
	setCloudConsent: (provider: string, granted: boolean) => Promise<void>;
	setHuggingFaceConsent: (granted: boolean) => Promise<void>;
	handleGrantConsent: () => void;
	testConnection: (provider: string) => Promise<void>;
}

// ── Helpers (module-level — `safeApiKey` is re-exported) ──────────────

/**
 * PVT-003 fix #6 helper: translate the cloud-provider key into the
 * matching `cloud_*_consent` config field. Returns the config key
 * (typed as a keyof VoiceTyperConfig so callers can index safely).
 */
function consentKeyFor(provider: string): keyof VoiceTyperConfig {
	if (provider === "openai") return "cloud_openai_consent";
	if (provider === "groq") return "cloud_groq_consent";
	return "cloud_deepgram_consent";
}

/**
 * PVT-003 fix #6 helper: translate the cloud-provider key into the
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

/**
 * Returns the cloud-provider HTTP test endpoint + auth headers for the
 * "Test Connection" probe. Extracted from `testConnection` so the
 * per-provider switch lives in one place (previously inlined as a
 * 90-line if/else if/else in the page).
 */
function testEndpointFor(
	provider: string,
	key: string,
): { url: string; headers: Record<string, string> } {
	switch (provider) {
		case "openai":
			return {
				url: "https://api.openai.com/v1/models",
				headers: { Authorization: `Bearer ${key}` },
			};
		case "groq":
			return {
				url: "https://api.groq.com/openai/v1/models",
				headers: { Authorization: `Bearer ${key}` },
			};
		case "deepgram":
			return {
				url: "https://api.deepgram.com/v1/projects",
				headers: { Authorization: `Token ${key}` },
			};
		default:
			return { url: "", headers: {} };
	}
}

// ── Hook ──────────────────────────────────────────────────────────────

export function useCloudProviders({
	showSnack,
	setConfig,
	apiKeys,
	updateConfig,
}: UseCloudProvidersArgs): UseCloudProvidersResult {
	const [testResults, setTestResults] = useState<Record<string, ApiTestResult>>(
		{},
	);

	// ── Action: saveApiKey / setCloudConsent / setHuggingFaceConsent ─
	const saveApiKey = useCallback(
		async (provider: string) => {
			const key = apiKeys[provider] ?? "";
			const configKey = apiKeyConfigField(provider);
			const updates = { [configKey]: key } as Partial<VoiceTyperConfig>;
			await updateConfig(updates);
			showSnack(
				t("models.snack.apiKeySaved", {
					provider: getProviderLabel(provider),
				}),
				"success",
			);
		},
		[apiKeys, showSnack, updateConfig],
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

	const setHuggingFaceConsent = useCallback(
		async (granted: boolean) => {
			await updateConfig({ huggingface_consent: granted });
			setConfig((prev) =>
				prev ? { ...prev, huggingface_consent: granted } : prev,
			);
			showSnack(
				granted ? t("models.consentGranted") : t("models.consentRevoked"),
				granted ? "success" : "warning",
			);
		},
		[showSnack, updateConfig, setConfig],
	);

	const handleGrantConsent = useCallback(() => {
		void setHuggingFaceConsent(true);
	}, [setHuggingFaceConsent]);

	// ── Action: testConnection ──────────────────────────────────────
	//
	// BG-50: previously the renderer-side ``fetch`` to the cloud
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
			try {
				await saveApiKey(provider);
				const { url, headers } = testEndpointFor(provider, key);
				let resp: Response;
				try {
					resp = await fetch(url, { headers });
				} catch (fetchErr) {
					// BG-50: cross-origin ``fetch`` failures throw a
					// ``TypeError`` ("Failed to fetch") for CORS
					// rejections, DNS failures, network offline,
					// and certificate errors. The native message is
					// opaque (the browser hides CORS details for
					// security). Surface a specific message so the
					// user can distinguish "wrong API key" (HTTP
					// 401) from "network blocked the request"
					// (CORS / offline).
					if (fetchErr instanceof TypeError) {
						setTestResults((prev) => ({
							...prev,
							[provider]: {
								message: t("models.test.connectionNetworkError"),
								status: "failure",
							},
						}));
						return;
					}
					throw fetchErr;
				}
				if (resp.ok) {
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
								status: String(resp.status),
								statusText: resp.statusText,
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
		[apiKeys, saveApiKey],
	);

	return {
		testResults,
		saveApiKey,
		setCloudConsent,
		setHuggingFaceConsent,
		handleGrantConsent,
		testConnection,
	};
}
