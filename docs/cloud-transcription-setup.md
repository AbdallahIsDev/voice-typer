# Cloud Transcription Setup

End-user + integrator guide for routing Voice Typer's ASR (and LLM
polishing) through a cloud provider instead of the default on-device
faster-whisper / Qwen / Parakeet engines. Covers supported providers,
API key format, network requirements, and per-OS quirks.

> **Threat model first.** All cloud ASR / LLM traffic is gated by the
> URL allowlist + HTTPS-enforcement policy in
> [`docs/adr/0017-cloud-url-allowlist-https.md`](adr/0017-cloud-url-allowlist-https.md)
> (RELIABILITY-004 / SEC-002 endpoint-swap / SEC-003 in-transit). Read
> that ADR before adding a new provider or a self-hosted endpoint —
> every hostname MUST be on the allowlist (or extended at runtime via
> `add_trusted_endpoint`) or the request is rejected before it leaves
> the process.

## Supported providers

| Provider | ASR | LLM polish | Default hostname | Notes |
|----------|-----|------------|------------------|-------|
| OpenAI | ✅ Whisper (audio/transcriptions) | ✅ GPT-4o-family chat | `api.openai.com` | Default cloud option. Both ASR + LLM use the same API key. |
| Groq | ✅ Whisper-large / Distil-whisper (audio/transcriptions) | ✅ Llama-family chat | `api.groq.com` | Lowest-latency cloud ASR (typically <300 ms for a short clip). |
| Deepgram | ✅ Nova-2 streaming + pre-recorded | ❌ (use OpenAI / Anthropic for LLM polish) | `api.deepgram.com` | Best streaming/cloud-ASR accuracy for telephony-grade audio. |
| Anthropic (Claude) | ❌ (no ASR endpoint) | ✅ Claude chat | `api.anthropic.com` | LLM polish only. Pair with OpenAI / Groq / Deepgram ASR. |
| Google Gemini / Vertex | ❌ (use cloud ASR provider above) | ✅ Gemini chat | `generativelanguage.googleapis.com` | LLM polish only. |
| Self-hosted (vLLM, llama.cpp server, Whisper HTTP server, etc.) | ✅ (if the server speaks the OpenAI / Groq / Deepgram wire format) | ✅ (same) | `localhost`, `127.0.0.1`, `::1` (loopback) or any host added via `add_trusted_endpoint` | HTTPS is NOT enforced on loopback; any non-loopback self-hosted host MUST be added via `add_trusted_endpoint` AND served over HTTPS. |

> Voice Typer does not implement provider-specific SDKs — each cloud
> path is a thin HTTP client. As long as the self-hosted endpoint
> speaks the same JSON wire format as one of the supported providers,
> Voice Typer will treat it as that provider (set `cloud_provider` to
> `openai` / `groq` / `deepgram` accordingly and point `cloud_api_url`
> at the self-hosted URL).

## API key format

| Provider | Env var / config key | Format | Where it's stored |
|----------|---------------------|--------|-------------------|
| OpenAI | `OPENAI_API_KEY` (env) or `cloud_api_key` (config) | `sk-...` (32+ ASCII chars) | OS credential store — see [`docs/security/credential-store.md`](security/credential-store.md). Never written to disk in plaintext. |
| Groq | `GROQ_API_KEY` (env) or `cloud_api_key` (config) | `gsk_...` (40+ ASCII chars) | Same. |
| Deepgram | `DEEPGRAM_API_KEY` (env) or `cloud_api_key` (config) | 32-hex-char UUID-like string | Same. |
| Anthropic | `ANTHROPIC_API_KEY` (env) or `llm_api_key` (config) | `sk-ant-...` (100+ ASCII chars) | Same. |
| Google Gemini | `GEMINI_API_KEY` (env) or `llm_api_key` (config) | `AIza...` (40 ASCII chars) | Same. |
| Self-hosted | `cloud_api_key` / `llm_api_key` (config) | Whatever the server expects (often a bearer token or empty for unauthenticated local servers) | Same — if the key is non-empty. |

The API key is redacted from all log lines by the `PIIRedactionFilter`
(SEC-009) and from IPC responses via the secret-redaction layer
(SEC-003). The renderer never sees the raw key — only the `cloud_provider`
+ `cloud_api_url` are exposed via `get_config`.

## Network requirements

- **HTTPS-only** (ADR-0017 SEC-003): every non-loopback host MUST be
  reached over `https://`. The URL allowlist enforces this at the HTTP
  client boundary — a `http://api.openai.com/...` URL is rejected with
  `host 'api.openai.com' is not in the trusted allowlist` even though
  the host is on the allowlist, because the scheme check fires first.
- **Loopback exception**: `localhost`, `127.0.0.1`, and `::1` are
  the only hosts where `http://` is permitted (for local development
  servers). Self-hosted endpoints on a LAN IP (e.g. `http://10.0.0.5`)
  are NOT exempt — serve them over HTTPS with a self-signed cert and
  add the hostname via `add_trusted_endpoint`.
- **DNS resolution**: the allowlist's host check resolves the hostname
  via `socket.getaddrinfo()` and rejects any hostname that resolves to
  a non-allowlist IP (defense against DNS rebinding — see the inline
  comment in `voice_typer/server/_secrets.py`).
- **Outbound ports**: 443 (HTTPS) for all cloud providers. Voice Typer
  does not need any inbound ports open for cloud transcription.
- **Proxy support**: Voice Typer honors the standard `HTTPS_PROXY` /
  `HTTP_PROXY` env vars. The proxy URL itself is NOT subject to the
  allowlist (it's a transport-layer concern, not an endpoint).
- **Connection timeout**: 30 s connect, 120 s read for ASR (audio
  uploads can be large); 30 s connect, 60 s read for LLM polish.
  Timeouts are configurable via `cloud_connect_timeout_seconds` /
  `cloud_read_timeout_seconds` in `Config`.

## Per-OS quirks

| OS | Quirk | Mitigation |
|----|-------|-----------|
| **Windows** | The system trust store is consulted for TLS verification (via `certifi` + `ssl.create_default_context()`). If a corporate MITM proxy replaces the root CA, TLS verification fails with `SSL: CERTIFICATE_VERIFY_FAILED`. | Set the `SSL_CERT_FILE` env var to the path of the corporate root CA bundle (a `.crt` / `.pem` file exported from the corporate IT portal). Restart Voice Typer. |
| **Windows** | Windows Firewall prompts on first cloud request if the bundled `pythonw.exe` (or the Nuitka-frozen sidecar exe under Tauri) has not yet been allowlisted. | Accept the prompt once (Private networks only — never Public). Voice Typer makes only outbound HTTPS connections; no inbound rule is needed. |
| **macOS** | Same TLS-verification path as Windows; the system Keychain root CAs are picked up automatically by `certifi`. | Same `SSL_CERT_FILE` workaround for corporate MITM proxies. |
| **macOS** | iCloud Private Relay can route DNS through Apple's relay, which sometimes causes intermittent `getaddrinfo` failures for non-Apple hostnames. | Disable Private Relay for the Voice Typer binary in System Settings → Apple ID → iCloud → Private Relay → "Exclude". This is a macOS-level setting; Voice Typer has no in-app toggle. |
| **Linux (X11 + Wayland)** | The trust store is rooted at `/etc/ssl/certs/ca-certificates.crt` (Debian/Ubuntu) or `/etc/pki/tls/certs/ca-bundle.crt` (Fedora/RHEL). `certifi` ships its own bundle and uses it in preference to the system bundle. | If a corporate root CA is installed in the system trust store but NOT in `certifi`'s bundle, set `SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt` (or the Fedora path) to force the system bundle. |
| **Linux (AppImage)** | The AppImage bundles its own `certifi` CA bundle; the host system's trust store is NOT consulted unless `SSL_CERT_FILE` is set explicitly. | Same `SSL_CERT_FILE` workaround as above. |
| **Linux (Tauri sidecar)** | The Nuitka-frozen sidecar exe does NOT consult the system trust store by default; it uses the `certifi` bundle baked in at freeze time. | If the `certifi` bundle is stale (a CA was added/rotated after the sidecar was built), update Voice Typer to a newer release — there is no in-app CA bundle refresh. |
| **All OSes (Tauri host)** | The Tauri v2 host does NOT proxy cloud requests through Rust — all cloud ASR / LLM HTTP traffic is initiated by the Python backend (same as the Electron host). The Rust host's `tauri-plugin-http` / `tauri-plugin-reqwest` is NOT used for cloud transcription. | No action needed — this is by design (the allowlist + redaction live in the Python layer). |

## Verifying connectivity

Voice Typer ships an IPC command specifically for testing cloud
connectivity without recording audio:

```
dispatch({type: "test_cloud_connection", data: {provider: "openai"}})
```

The handler (`voice_typer/server/handlers/cloud_test_handlers.py`) opens
a short-lived HTTPS connection to the configured `cloud_api_url` (or
the provider's default hostname if no URL is set), sends a trivial
`GET /v1/models` (or equivalent) request with the configured API key,
and reports the HTTP status code + round-trip latency back to the
renderer. This is exposed in Settings → Models → Cloud as the "Test
connection" button.

The `test_cloud_connection` command is rate-limited to 10 calls per
minute (per-connection ADR-0019 rate limiter) to prevent abuse.

## Self-hosted endpoint quickstart

1. Stand up your self-hosted endpoint (e.g. `vllm serve --model
   whisper-large-v3`, or `llama.cpp server --model qwen2.5-72b`).
   Make sure it speaks the OpenAI / Groq / Deepgram wire format.
2. Serve it over HTTPS. For a LAN-only deployment, use a self-signed
   cert (e.g. `caddy` / `nginx` with a self-signed CA). Loopback
   (`http://localhost:port`) is exempt from HTTPS but anything on a
   LAN IP is not.
3. Add the hostname to the allowlist at runtime:

   ```
   dispatch({type: "add_trusted_endpoint", data: {host: "asr.lan.example.com"}})
   ```

   The host is added to the per-process URL allowlist (`extend_url_allowlist`
   under the hood). The extension is process-global and applies to all
   HTTP clients in the process; it is NOT persisted across restarts —
   add it from a startup hook or your config wizard if you need it
   permanent.
4. In Settings → Models → Cloud, set:
   - `cloud_provider` = `openai` (or whichever wire format your server
     speaks)
   - `cloud_api_url` = `https://asr.lan.example.com/v1`
   - `cloud_api_key` = the bearer token your server expects (or empty
     for unauthenticated loopback servers)
5. Click "Test connection" — you should get a 200 + a latency reading.
   If you get `host 'asr.lan.example.com' is not in the trusted
   allowlist`, the `add_trusted_endpoint` call didn't land (check the
   IPC response envelope for an error).

## See also

- [`docs/adr/0017-cloud-url-allowlist-https.md`](adr/0017-cloud-url-allowlist-https.md) —
  the URL allowlist + HTTPS-enforcement ADR (RELIABILITY-004).
- [`docs/security/credential-store.md`](security/credential-store.md) —
  how API keys are stored per-OS (Keychain / Credential Manager /
  Secret Service).
- [`docs/privacy/encryption-at-rest.md`](privacy/encryption-at-rest.md) —
  what user data (including audio clips + transcriptions) is encrypted
  at rest and how.
- [`docs/ipc-reference.md`](ipc-reference.md) — the `test_cloud_connection`
  + `add_trusted_endpoint` IPC command rows.
- [`docs/python-api.md`](python-api.md) — the `Config` dataclass fields
  (`cloud_provider`, `cloud_api_url`, `cloud_api_key`, etc.).
