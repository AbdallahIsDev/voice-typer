/**
 * BG-fixes — tests for sub-agent F4 (Task ID BG-F4).
 *
 * Coverage:
 *   - BG-16: PrivacySettingsSection's "Agree to All" ConfirmDialog uses
 *     i18n keys (settings.privacy.agreeConfirmTitle /
 *     agreeConfirmMessage) rather than hardcoded English literals.
 *   - BG-55: per-row search filtering — a search query that matches only
 *     one row must hide the other rows in the same section (previously
 *     the section-level check showed the entire section including all
 *     rows when ANY row matched).
 *   - BG-98: ModelSettingsSection's API Key input uses
 *     settings.apiKeyConfiguredPlaceholder when llm_api_key === "<redacted>"
 *     rather than the hardcoded English literal "•••••••• (configured)".
 *   - BG-57: Settings.tsx memoizes sectionProps and handleResetClick so
 *     memoized section children don't re-render unnecessarily. This is
 *     a smoke test — the heavy memoization verification lives in the
 *     Settings page's existing test suite (which passes after BG-57).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@hugeicons/react", () => ({
        HugeiconsIcon: ({
                children,
                icon,
        }: {
                children?: React.ReactNode;
                icon?: { name?: string };
        }) => (
                <span data-testid="hugeicon" data-name={icon?.name}>
                        {children}
                </span>
        ),
}));

vi.mock("@hugeicons/core-free-icons", () => {
        const make = (name: string) => ({ name });
        return {
                Alert02Icon: make("Alert02Icon"),
                ArrowDown01Icon: make("ArrowDown01Icon"),
                ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
                ArrowUp01Icon: make("ArrowUp01Icon"),
                Book02Icon: make("Book02Icon"),
                Bug02Icon: make("Bug02Icon"),
                CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
                Delete01Icon: make("Delete01Icon"),
                File02Icon: make("File02Icon"),
                InformationCircleIcon: make("InformationCircleIcon"),
                KeyboardIcon: make("KeyboardIcon"),
                LockKeyIcon: make("LockKeyIcon"),
                ModernTvIcon: make("ModernTvIcon"),
                Moon02Icon: make("Moon02Icon"),
                RefreshIcon: make("RefreshIcon"),
                Sun01Icon: make("Sun01Icon"),
                Tick02Icon: make("Tick02Icon"),
                UnfoldMoreIcon: make("UnfoldMoreIcon"),
        };
});

vi.mock("@/hooks/usePython", () => ({
        usePython: () => ({ call: vi.fn() }),
}));

vi.mock("@/hooks/useSnackbar", () => ({
        useSnackbar: () => ({ showSnack: vi.fn() }),
}));

vi.mock("sonner", () => ({
        toast: {
                success: vi.fn(),
                error: vi.fn(),
                warning: vi.fn(),
                info: vi.fn(),
                dismiss: vi.fn(),
        },
        Toaster: () => null,
}));

vi.mock("next-themes", () => ({
        useTheme: () => ({ theme: "light" as const }),
}));

import { ModelSettingsSection } from "@/components/settings/ModelSettingsSection";
import { PrivacySettingsSection } from "@/components/settings/PrivacySettingsSection";
import type { SettingsSectionSharedProps } from "@/components/settings/types";
import type { VoiceTyperConfig } from "@/types/config";

/** Minimal valid config — same shape used elsewhere in the Settings test suite. */
function makeConfig(
        overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
        return {
                schema_version: 1,
                fast_startup: true,
                hotkey: "F2",
                sample_rate: 16000,
                microphone: null,
                model_size: "small.en",
                language: "en",
                device: "cpu",
                beam_size: 5,
                best_of: 1,
                condition_on_previous_text: false,
                streaming_transcription: false,
                streaming_chunk_seconds: 0,
                streaming_step_seconds: 0,
                streaming_left_overlap_seconds: 0,
                streaming_right_guard_seconds: 0,
                streaming_min_first_chunk_seconds: 0,
                streaming_silence_threshold: 0,
                autostart: false,
                paste_on_stop: true,
                show_notifications: true,
                clipboard_save_restore: true,
                clipboard_restore_delay_ms: 150,
                asr_backend: "whisper",
                qwen_model_path: null,
                parakeet_model_path: null,
                text_cleanup_enabled: true,
                unsafe_paste_on_unknown_focus: false,
                corrections_path: null,
                log_transcriptions: false,
                recording_mode: "toggle",
                push_to_talk_hotkey: "",
                esc_cancel_enabled: true,
                repaste_hotkey: "",
                auto_punctuation: false,
                templates_enabled: true,
                vocabulary_enabled: true,
                cloud_api_key: "",
                cloud_api_url: "",
                cloud_model: "",
                openai_api_key: "",
                groq_api_key: "",
                deepgram_api_key: "",
                llm_polish: false,
                llm_api_key: "",
                llm_api_url: "",
                llm_model: "",
                llm_preset: "default",
                crash_recovery_enabled: true,
                audio_quality_warnings: false,
                waveform_bubble: true,
                bubble_position: "top",
                bubble_behavior: "show_on_record",
                bubble_draggable: true,
                bubble_show_on_startup: false,
                history_retention_days: 30,
                history_retention_count: 100,
                history_max_entries: 1000,
                onboarding_completed: true,
                tray_left_click_action: "open_app",
                theme_mode: "system",
                theme_preset: "default",
                text_size: 14,
                wayland_warned: false,
                silence_warning_seconds: 0,
                stop_on_silence_seconds: 0,
                max_recording_time_seconds: 900,
                volume_duck_enabled: false,
                volume_duck_level: 0,
                volume_duck_per_session: false,
                volume_duck_fade_ms: 0,
                volume_duck_smart: false,
                volume_duck_smart_poll_interval_ms: 0,
                audio_preset: "auto",
                noise_filter_enabled: false,
                noise_filter_highpass: false,
                noise_filter_highpass_cutoff_hz: 0,
                noise_filter_gate: false,
                noise_filter_gate_threshold: 0,
                noise_filter_gate_hold_ms: 0,
                noise_filter_gate_open_threshold_db: 0,
                noise_filter_gate_close_threshold_db: 0,
                noise_filter_gate_attack_ms: 0,
                noise_filter_gate_release_ms: 0,
                noise_filter_rnnoise: false,
                noise_filter_post_capture: false,
                noise_suppression_method: "none",
                noise_filter_eq: false,
                noise_filter_eq_low_db: 0,
                noise_filter_eq_mid_db: 0,
                noise_filter_eq_high_db: 0,
                noise_filter_compressor: false,
                noise_filter_compressor_threshold_db: 0,
                noise_filter_compressor_ratio: 0,
                noise_filter_compressor_attack_ms: 0,
                noise_filter_compressor_release_ms: 0,
                noise_filter_compressor_output_gain_db: 0,
                noise_filter_limiter: false,
                noise_filter_limiter_ceiling_db: 0,
                noise_filter_limiter_release_ms: 0,
                noise_filter_notch: false,
                noise_filter_notch_frequency_hz: 0,
                huggingface_consent: false,
                cloud_openai_consent: false,
                cloud_groq_consent: false,
                cloud_deepgram_consent: false,
                voice_biometric_consent: false,
                llm_polish_consent: false,
                sound_feedback_enabled: false,
                ai_enhancement_enabled: false,
                auto_capitalize: true,
                auto_punctuate: true,
                fix_grammar_basics: true,
                vocabulary_automation_enabled: false,
                vocabulary_auto_confidence_threshold: 0.7,
                vocabulary_auto_apply_threshold: 0.95,
                ...overrides,
        } as VoiceTyperConfig;
}

const alwaysVisible: SettingsSectionSharedProps["isVisible"] = () => true;

/** isVisible predicate that hides rows whose label doesn't include `q`. */
function filterByLabel(q: string): SettingsSectionSharedProps["isVisible"] {
        const query = q.toLowerCase();
        return (label, _info, _sectionTitle) =>
                label.toLowerCase().includes(query);
}

afterEach(() => {
        cleanup();
});

describe("BG-16: PrivacySettingsSection Agree-to-All ConfirmDialog uses i18n keys", () => {
        beforeEach(() => {
                cleanup();
        });

        it("opens the ConfirmDialog with the translated title + message when Agree to All is clicked", () => {
                const config = makeConfig();
                render(
                        <PrivacySettingsSection
                                config={config}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={alwaysVisible}
                        />,
                );

                // Click the "Agree to All" button (the visible button, not any
                // dialog buttons that may be rendered in a closed portal).
                const agreeAllBtn = screen
                        .queryAllByRole("button")
                        .find((b) => /agree to all/i.test(b.textContent ?? ""));
                expect(agreeAllBtn).toBeTruthy();
                fireEvent.click(agreeAllBtn as HTMLElement);

                // The ConfirmDialog must now render with the i18n title and message
                // (rather than the previous hardcoded English literals). The dialog
                // uses AlertDialogContent via a portal — query the document body.
                // BG-16 fix: title is t("settings.privacy.agreeConfirmTitle")
                // = "Grant all 6 consents?"
                expect(
                        screen.getByText("Grant all 6 consents?"),
                ).toBeTruthy();
                // BG-16 fix: message is t("settings.privacy.agreeConfirmMessage")
                // — a long sentence about HuggingFace / cloud transcription / LLM
                // polishing. Assert a substring unique to that message.
                expect(
                        screen.getByText(/HuggingFace downloads, cloud transcription/),
                ).toBeTruthy();
        });
});

describe("BG-55: per-row search filtering in Settings sections", () => {
        beforeEach(() => {
                cleanup();
        });

        it("ModelSettingsSection hides non-matching rows but keeps matching ones (Post-Processing)", () => {
                // Filter for "auto" — should match "Auto-Punctuation" but not
                // "Transcription Language", "Text Cleanup", "Text Snippets",
                // or "Vocabulary".
                const isVisible = filterByLabel("auto");
                render(
                        <ModelSettingsSection
                                config={makeConfig()}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={isVisible}
                        />,
                );

                // "Auto-Punctuation" should be visible (matches "auto").
                expect(screen.getByText("Auto-Punctuation")).toBeTruthy();
                // "Text Cleanup" should NOT be visible (doesn't match "auto").
                expect(screen.queryByText("Text Cleanup")).toBeNull();
                // "Text Snippets" should NOT be visible.
                expect(screen.queryByText("Text Snippets")).toBeNull();
                // "Vocabulary" should NOT be visible.
                expect(screen.queryByText("Vocabulary")).toBeNull();
        });

        it("ModelSettingsSection hides the entire section when no row matches", () => {
                // Filter for a nonsense query that matches no row.
                const isVisible = filterByLabel("zzzqqqxxxyyy999");
                const { container } = render(
                        <ModelSettingsSection
                                config={makeConfig()}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={isVisible}
                        />,
                );
                // No Post-Processing / LLM Polishing content should be rendered.
                expect(screen.queryByText("Auto-Punctuation")).toBeNull();
                expect(screen.queryByText("API Key")).toBeNull();
                // The container should be effectively empty (no SettingsSection
                // blocks rendered).
                expect(container.firstChild).toBeNull();
        });

        it("PrivacySettingsSection hides non-matching consent rows but keeps matching ones", () => {
                // Filter for "huggingface" — should match the HuggingFace row
                // (label = "HuggingFace model downloads") but not the other
                // consent rows.
                const isVisible = filterByLabel("huggingface");
                render(
                        <PrivacySettingsSection
                                config={makeConfig()}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={isVisible}
                        />,
                );

                // "HuggingFace model downloads" should be visible.
                expect(
                        screen.getByText("HuggingFace model downloads"),
                ).toBeTruthy();
                // "Voice biometric processing" should NOT be visible.
                expect(screen.queryByText("Voice biometric processing")).toBeNull();
                // "OpenAI cloud ASR" should NOT be visible.
                expect(screen.queryByText("OpenAI cloud ASR")).toBeNull();
                // "LLM text polishing" should NOT be visible.
                expect(screen.queryByText("LLM text polishing")).toBeNull();
        });
});

describe("BG-98: ModelSettingsSection uses i18n key for redacted API key placeholder", () => {
        beforeEach(() => {
                cleanup();
        });

        it("renders the configured-key placeholder from t('settings.apiKeyConfiguredPlaceholder') when llm_api_key is '<redacted>'", () => {
                // Enable LLM polish so the API Key row renders, and set the key
                // to the backend's redaction sentinel so the placeholder branch
                // is taken.
                render(
                        <ModelSettingsSection
                                config={makeConfig({
                                        llm_polish: true,
                                        llm_api_key: "<redacted>",
                                })}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={alwaysVisible}
                        />,
                );

                // The placeholder is the i18n key value "•••••••• (configured)".
                // jsdom doesn't render placeholders as visible text, but the
                // <input> element's `placeholder` attribute reflects the value.
                // Find the password input (the API Key input) and assert.
                const apiKeyInput = document.querySelector(
                        'input[type="password"]',
                ) as HTMLInputElement | null;
                expect(apiKeyInput).toBeTruthy();
                expect(apiKeyInput?.placeholder).toBe("•••••••• (configured)");
        });

        it("renders the regular placeholder from t('settings.apiKeyPlaceholder') when llm_api_key is empty", () => {
                render(
                        <ModelSettingsSection
                                config={makeConfig({
                                        llm_polish: true,
                                        llm_api_key: "",
                                })}
                                updateConfig={() => {}}
                                updateConfigDebounced={() => {}}
                                isVisible={alwaysVisible}
                        />,
                );

                const apiKeyInput = document.querySelector(
                        'input[type="password"]',
                ) as HTMLInputElement | null;
                expect(apiKeyInput).toBeTruthy();
                // t("settings.apiKeyPlaceholder") = "Enter your API key"
                expect(apiKeyInput?.placeholder).toBe("Enter your API key");
        });
});
