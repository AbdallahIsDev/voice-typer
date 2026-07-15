# ADR 0016: Granular Privacy Consent Flags (PRIV-005, PRIV-006, PRIV-009)

## Status

Accepted — implemented in `voice_typer/server/config.py` as typed boolean fields on the `Config` dataclass.

## Date

2026-07-14

## Context

Voice Typer processes user speech (audio and text) through several systems, each with different privacy implications:

1. **HuggingFace model download (PRIV-005):** When the user selects a Whisper or Parakeet model for the first time, the app downloads model weights from HuggingFace's CDN. This reveals the user's IP address to HuggingFace (a US-headquartered third party) and indicates that the user is running Voice Typer. Under GDPR Art. 13/44, this is a data-processing disclosure that requires explicit consent before it occurs.

2. **Cloud ASR providers (PRIV-006):** Voice Typer supports cloud-based transcription via OpenAI Whisper API, Deepgram, and Groq. When enabled, the user's audio is transmitted to these providers for transcription. Storing an API key alone does not constitute consent — the user must explicitly agree that audio will leave their machine. Each provider has different data-handling policies, so consent must be per-provider.

3. **Local biometric processing (PRIV-009):** Voice recordings may constitute biometric data under the Illinois Biometric Information Privacy Act (BIPA) and GDPR Art. 9 (special categories of personal data). Even though processing is local (on-device), some jurisdictions require informed consent before collecting or processing biometric data. Users must be informed that their voice will be processed for transcription and consent to this.

**Previous state:** Before PRIV-005/006/009, there was no explicit consent mechanism. The UI would download models or send audio to cloud providers without any disclosure or consent dialog. The `cloud_api_key` field being non-empty was implicitly treated as consent.

**Regulatory requirements:**
- **GDPR Art. 7:** Conditions for consent — must be freely given, specific, informed, and unambiguous.
- **GDPR Art. 13:** Information to be provided where personal data are collected from the data subject.
- **GDPR Art. 44:** General principle for transfers of personal data to third countries.
- **BIPA (740 ILCS 14):** Requires written consent before collecting or disclosing biometric data.
- **CCPA:** Requires notice of data collection purposes at or before the point of collection.

## Decision

Implement **three categories of explicit, granular consent flags** as boolean fields on the `Config` dataclass:

### PRIV-005: HuggingFace Model Download Consent

```python
huggingface_consent: bool = False
```

- **When checked:** Before the first model download, the renderer shows a consent dialog explaining that the download will reveal the user's IP to HuggingFace. The download proceeds only after the user accepts.
- **Scope:** This flag is NOT reset when the user changes models — only a single consent is needed per installation.
- **UI location:** Settings → Privacy → "Download model weights from HuggingFace"

### PRIV-006: Per-Provider Cloud ASR Consent

```python
cloud_openai_consent: bool = False
cloud_groq_consent: bool = False
cloud_deepgram_consent: bool = False
```

- **Each provider has an independent flag.** Enabling an API key for a provider without granting the corresponding consent is not sufficient to use that provider.
- **Scope:** Consent is per-provider. The user can consent to OpenAI but not Deepgram, or vice versa.
- **UI location:** Settings → Privacy → "Send audio to [provider] for cloud transcription"
- **Behavior:** If the user enables a cloud ASR backend without granting consent, the app falls back to local transcription (if available) or shows an error.

### PRIV-009: Local Voice Biometric Processing Consent

```python
voice_biometric_consent: bool = False
```

- **Disclosure:** The consent dialog informs the user that voice recordings (which may constitute biometric data) are processed locally for transcription on their device. No data leaves the machine for this processing.
- **Scope:** This is a one-time consent. Once granted, the user can use Voice Typer normally.
- **UI location:** First-run onboarding → "I consent to on-device voice processing for transcription"

### Design Rules

1. **Consent is NOT implied by action.** Storing an API key (`cloud_api_key`, `openai_api_key`, etc.) is NOT treated as consent. The user must explicitly check the consent box — otherwise the cloud provider is not used even with a valid key.

2. **Consent is NOT silently revoked.** Turning off `cloud_openai` toggle does NOT reset `cloud_openai_consent`. This prevents the "I already agreed but the UI forgot" user experience. Consent remains valid across toggle cycles.

3. **Consent flags are boolean.** No timestamps, no expiry, no version tracking. This is a deliberate simplification — the consent is "I understand and agree to the described data practice." If the data practice changes (e.g., a new provider), a new consent flag is added.

4. **Config persistence:** Consent flags are stored in `config.json` and survive app restarts. There is no separate consent database.

## Consequences

### Easier
- **GDPR/BIPA compliance:** Explicit, granular consent with clear disclosure at the point of collection satisfies the informed-consent requirements of GDPR Art. 7, Art. 13/44, and BIPA.
- **Audit trail:** Consent status is stored in the user's config file, which can be produced for Data Subject Access Requests (GDPR Art. 15).
- **Per-provider granularity:** Users can trust individual cloud providers while excluding others, matching the varying privacy policies of each provider.

### More difficult
- **Consent-state validation:** The app must check consent flags before each cloud transcription request and fall back gracefully if consent is missing. This adds a validation path in `cloud_engines.py`.
- **UI complexity:** The Settings page must show separate consent toggles for each provider with explanatory text. This was previously a single "cloud transcription" toggle.
- **Migration:** Existing users upgrading from a version without consent flags have `huggingface_consent: False`, `cloud_openai_consent: False`, etc. They will see the consent dialog on first model download or first cloud transcription after upgrade.

### Risks
- **False sense of compliance:** The consent flags are self-reported. There is no mechanism to prevent the user from lying about their jurisdiction (e.g., an Illinois resident setting `voice_biometric_consent: False` when the law requires it). This is inherent to any client-side consent model.
- **No revocation mechanism:** There is no "revoke all consent" button. The user must manually uncheck each consent flag. This is acceptable for the current scope — GDPR Art. 7(3) requires that withdrawal be as easy as granting, which is satisfied by the UI toggle.

## References

- `voice_typer/server/config.py` lines 657-677 — consent flag declarations.
- `voice_typer/server/config.py` lines 1240-1245 — consent fields in `_validate_non_numeric_fields`.
- `voice_typer/client/src/renderer/src/pages/Settings.tsx` — UI for consent toggles.
- SECURITY.md — PRIV-005, PRIV-006, PRIV-009 documentation.
- GDPR Art. 7 (Conditions for consent), Art. 13 (Information to be provided), Art. 44 (General principle for transfers).
- BIPA 740 ILCS 14 (Biometric Information Privacy Act).

*End of document.*
