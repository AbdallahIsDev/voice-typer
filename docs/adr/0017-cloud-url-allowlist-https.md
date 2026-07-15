# ADR 0017: Cloud URL Allowlist with HTTPS Enforcement (RELIABILITY-004)

## Status

Accepted — implemented in `voice_typer/server/_secrets.py`.

## Date

2026-07-14

## Context

Voice Typer allows users to configure custom API endpoints for cloud transcription and LLM polishing via the `cloud_api_url` and `llm_api_url` config fields. These fields can be set through the Settings UI (IPC `set_config`) or by directly editing `config.json`.

**Threat model (SEC-002 endpoint-swap):** An attacker who gains write access to the user's config file (or exploits a vulnerability in `set_config`) can change the API endpoint to an attacker-controlled server. All subsequent transcription requests and API keys would be sent to the attacker. This is a high-impact attack:
- API keys (OpenAI, Groq, Deepgram, etc.) would be exfiltrated to the attacker.
- Transcribed text (which may contain sensitive personal information) would be sent to the attacker.
- The victim would see no visible difference — the attacker's server just proxies the request to the real provider.

**Without an allowlist:** A `set_config` call setting `cloud_api_url = "http://evil.example.com/steal"` would be accepted by the server. The HTTP client would happily POST audio to the attacker's endpoint.

**Additional concern — HTTPS enforcement:** Many cloud providers support HTTP for legacy compatibility, but transmitting API keys and audio over cleartext HTTP on the public internet is unacceptable. Even if the host is in the allowlist, the scheme must be enforced.

**Reliability angle (RELIABILITY-004):** A mistyped or malformed URL causes silent failures ("transcription not working"). The allowlist provides early rejection with a clear error message, which is faster and more user-friendly than a timeout from a nonexistent endpoint.

## Decision

Implement a **centralized URL allowlist with HTTPS enforcement** in `voice_typer/server/_secrets.py`:

### Default Allowlist

A `frozenset` of trusted cloud provider hostnames, populated with known-good defaults:

| Provider | Allowed Hostname |
|---|---|
| OpenAI | `api.openai.com` |
| Groq | `api.groq.com` |
| Deepgram | `api.deepgram.com` |
| Anthropic (Claude) | `api.anthropic.com` |
| Google Gemini/Vertex | `generativelanguage.googleapis.com` |
| Local development | `localhost`, `127.0.0.1`, `::1` |

### Runtime Extensions

Users who run self-hosted endpoints (e.g., a local vLLM server) can extend the allowlist at runtime via `extend_url_allowlist(["my-host.example.com"])`. This is process-global and applies to all HTTP clients in the same process.

### Enforcement

The `assert_url_allowed()` function enforces three checks:

1. **Scheme validation:** Only `http` and `https` schemes are permitted. Rejects `file://`, `ftp://`, `javascript:`, and other schemes.
2. **Host allowlist:** The URL's hostname (lowercased, port-stripped) must be in the effective allowlist (defaults + user extensions).
3. **HTTPS enforcement (SEC-003):** Non-loopback hosts (anything except `localhost`, `127.0.0.1`, `::1`) MUST use HTTPS. HTTP is only permitted for local development servers.

### Integration

Every HTTP-issuing module in the codebase uses `assert_url_allowed()` before making a request:
- `cloud_engines.py` — cloud ASR requests.
- `llm_polish.py` — LLM text polishing requests.
- Any future HTTP client.

### Error Handling

When validation fails, the function raises `ValueError` with a descriptive message. The error message does NOT include the original URL (to avoid leaking a potentially malicious URL into logs). The error is caught by the IPC dispatch layer and returned as a structured `{"type": "error", "data": {"code": "invalid_field", ...}}` response.

## Consequences

### Easier
- **Defense against endpoint-swap:** An attacker who can set `cloud_api_url` via `set_config` is limited to the allowlist. They cannot redirect to an arbitrary host.
- **Clear error messages:** A mistyped URL (e.g., `api.oppenai.com`) is rejected immediately with "host 'api.oppenai.com' is not in the trusted allowlist", rather than a cryptic HTTP 404 or timeout.
- **HTTPS-by-default:** API keys and transcribed text are never transmitted over cleartext HTTP to public internet hosts. This satisfies the principle of data-in-transit protection.

### More difficult
- **Self-hosted setup friction:** Users running local vLLM or other self-hosted endpoints must call `extend_url_allowlist()` in a startup script, or the first API call fails. Mitigation: localhost is already in the allowlist, and the Settings UI could expose an "add custom endpoint" dialog.
- **New provider onboarding:** When a new cloud provider is added, its hostname must be added to `_DEFAULT_ALLOWED_HOSTS`. This is a one-line change in `_secrets.py`, but forgetting it causes the integration to fail at runtime.

### Risks
- **Allowlist bypass via proxy:** An attacker who controls a host in the allowlist (e.g., a compromised Deepgram subdomain) can intercept traffic. This is a risk inherent to any host-based allowlist and is outside Voice Typer's control.
- **False sense of security:** The URL allowlist prevents endpoint-swap attacks but does not prevent an attacker from reading the user's config file directly (where API keys are stored). That is addressed by SEC-007 (file permissions) and SEC-003 (IPC redaction).

## References

- `voice_typer/server/_secrets.py` — `assert_url_allowed()`, `extend_url_allowlist()`, `get_url_allowlist()`.
- `voice_typer/server/cloud_engines.py` — integration of URL validation.
- `voice_typer/server/llm_polish.py` — integration of URL validation.
- `voice_typer/server/security.py` — `_redact_text()` (redacts userinfo from URLs in logs).
- SECURITY.md — RELIABILITY-004 documentation.

*End of document.*
