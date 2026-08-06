# Error Envelope Contract

Voice Typer uses **one unified error-envelope contract on both IPC transports** (TCP loopback for the Electron host, localhost WebSocket for the Tauri host). The unification was finalized in CR-20 (2026-07-18) — earlier drafts documented a per-transport split, but that split no longer exists.

> **Canonical form (PI-22):** the table at the [Registered Codes](#server-originated-errors-5xx-analog--server) section below is the single source of truth for every `code` value the server emits. The namespaced `client.*` / `server.*` form is canonical; the legacy non-namespaced aliases (e.g. `internal_error`, `rate_limited`, `invalid_payload`) are DEPRECATED and retained only for one-release backward compatibility. New emitters MUST use the namespaced form. The `legacy_code` field on the wire is a transitional alias emitted alongside the canonical `code` for `client.invalid_payload` / `client.invalid_field` / `client.missing_field` / `client.rate_limited` (per PI-23); it will be dropped once the renderer migration completes.

## Shape

Every error response — whether produced by payload validation, by an unknown command, by a handler-raised exception, or by the dispatch loop's catch-all — has the same shape:

```json
{"type": "error", "data": {"code": "<error_code>", "message": "<human-readable summary>"}}
```

Optionally, on the request/response channel (channel 1), the envelope may also carry the originating request `id` so the client can correlate the failure:

```json
{"type": "error", "data": {"code": "<error_code>", "message": "<...>"}, "id": <request_id>}
```

The `id` field is omitted when the error pre-dates request parsing (e.g. malformed JSON, missing `type` field) or when the request itself did not carry an `id`.

## Both paths are generic

There is **no** per-transport divergence. Both the TCP path (`ipc/transport_tcp.py:_handle_tcp_connection` on `TCPTransportMixin`, inherited by `IPCServer`) and the WS path (`sidecar_ws.py:_handle_connection`) emit the same generic envelope for unhandled exceptions:

```json
{"type": "error", "data": {"code": "server.internal_error", "message": "internal error"}}
```

The exception's `str(exc)` is deliberately NOT leaked into the `message` field — the full traceback is logged server-side at ERROR with `exc_info=True`, but only the generic `"internal error"` string reaches the wire. This is true on BOTH transports; there is no "TCP returns detailed messages, WS is production" split anymore.

## The `code` field

The `code` field is the machine-readable error identifier that the renderer (and the Tauri Rust host) switches on. The canonical registry is `ERROR_CODES` in `voice_typer/server/ipc/validation.py` — that frozenset is the single source of truth, and `tests/test_error_codes_registry.py` is the contract test that asserts every `"code": "..."` literal emitted by the server is either registered there or is a documented legacy alias.

The registered codes (as of the CR-20 unification):

### Client-originated errors (4xx analog) — `client.*`
The request was malformed, invalid, or unauthorized. The renderer can fix the request and retry.

| Code | Meaning |
|---|---|
| `client.invalid_field` | A field's value failed type / range / length validation. |
| `client.missing_field` | A required field was absent from the `data` payload. |
| `client.invalid_payload` | The `data` payload itself was malformed (not an object, oversized, etc.). |
| `client.rate_limited` | The per-connection rate limiter (ADR-0019) tripped. |
| `client.path_not_allowed` | A filesystem path in the payload escaped the allowed roots. |
| `client.not_found` | The referenced resource (model, vocabulary entry, template, etc.) does not exist. |
| `client.auth_failed` | Authentication failed (bearer-token mismatch on the WS path). |
| `client.consent_required` | PI-17 / NEW-PRIV-006: the engine requires explicit user consent before proceeding (e.g. HuggingFace download, cloud transcription). The envelope carries structured fields (`engine_name`, `consent_field`, `model_id`) so the renderer can surface a consent dialog deep-linked to the exact toggle in Settings. |

### Server-originated errors (5xx analog) — `server.*`
The server could not process a well-formed request. The renderer surfaces a generic "something went wrong" message and logs the detail for support.

| Code | Meaning |
|---|---|
| `server.internal_error` | Unhandled exception inside the dispatch loop. Message is the generic `"internal error"`. |
| `server.handler_error` | A handler-level `except Exception` catch-all fired (the `_error_response` helper in `validation.py` stamps this code so handler exceptions get the same envelope shape as validation errors). |
| `server.file_locked` | A file the server needs to read/write is locked by another process. |
| `server.model_switch_failed` | The model-switch handler could not switch to the requested model. |
| `server.shutting_down` | The server is mid-shutdown and refusing new work. |
| `server.unknown_command` | The `type` field did not resolve to an entry in `_COMMAND_REGISTRY`. |
| `server.unknown_tray_item` | A tray-menu RPC referenced an item the server does not know about. |
| `server.cloud_auth_failed` | PI-17: cloud provider returned 401/403 — API key invalid or revoked. The renderer should prompt the user to re-enter the key. |
| `server.cloud_rate_limited` | PI-17: cloud provider returned 429 (after retry budget exhausted). The renderer should back off and retry. |
| `server.cloud_server_error` | PI-17: cloud provider returned 5xx. The renderer may retry with exponential backoff. |
| `server.cloud_network_error` | PI-17: URLError — timeout, DNS, connection reset (after retry budget exhausted). |
| `server.cloud_config_error` | PI-17 / PI-24: cloud provider not configured (missing API key). The renderer should deep-link to Settings. |
| `server.cloud_engine_error` | PI-17: generic cloud-engine failure (typed `CloudEngineError` base — e.g. unknown HTTP status from the cloud provider). |

### Legacy non-namespaced aliases (DEPRECATED)

The namespaced `client.*` / `server.*` form is canonical for ALL emitters (PI-16 / PI-22 / PI-23).

**Current status (post-PI-16/PI-22/PI-23):**

- `internal_error` — NO LONGER emitted by the dispatch loop or handler catch-alls (PI-16 completed the migration; the dispatcher and `_respond_with_error` both emit `server.internal_error`).
- `rate_limited` / `invalid_payload` — NO LONGER emitted as the primary `code` by the dispatch loop (PI-23 migrated these to `client.rate_limited` / `client.invalid_payload`). A transitional `legacy_code` field is emitted ALONGSIDE the canonical `code` for one release cycle so the renderer (and any in-flight tests) can switch to the namespaced form without a hard cutover. Once the renderer migration is complete, the `legacy_code` field will be dropped.
- `invalid_field` / `missing_field` — same pattern: `_validate_dict_payload` emits `client.invalid_field` / `client.missing_field` as primary `code`, with `legacy_code` alias for one release cycle.
- `shutting_down`, `unknown_command`, `unknown_tray_item`, `auth_failed`, `model_switch_failed`, `payload_too_large`, `handler_error` — still emitted by some paths; the migration to namespaced forms is tracked separately.

The renderer MUST accept both forms and treat the legacy form as an alias for the namespaced equivalent. The contract test in `tests/test_error_codes_registry.py` is the regression guard.

## Per-command validation errors

Both transports return the SAME per-command validation error envelopes (e.g. `{"code": "client.invalid_payload", "message": "payload too large (N bytes; max M)"}` from `_validate_dict_payload`'s `max_payload_bytes` rule, or `{"code": "client.invalid_field", "message": "..."}` from a type-mismatch). These are explicit error codes the renderer can switch on, and they are produced identically on both transports because the WS path reuses `IPCServer._dispatch` (and therefore the same `_validate_dict_payload` helper) verbatim.

## Wire-side behavior

- **TCP path (Electron host)** — `ipc/transport_tcp.py:_handle_tcp_connection` (on `TCPTransportMixin`, inherited by `IPCServer`)'s ERR-018 block constructs the `{"type": "error", "data": {"code": "server.internal_error", "message": "internal error"}}` envelope for any unhandled dispatch exception, attaches the originating request `id` if available, and sends it back on the same socket. Validation errors from `_validate_dict_payload` are returned by the handler itself and flow back through the same `_send` path. Rate-limit rejections and invalid-JSON errors emit the namespaced `client.rate_limited` / `client.invalid_payload` codes with a transitional `legacy_code` alias (PI-23).
- **WS path (Tauri host)** — `sidecar_ws._handle_connection` reuses `IPCServer._dispatch` for inbound frames, so validation errors come back verbatim. For dispatch-loop exceptions, the WS path's catch-all (see the IPC-5 / 2026-07-18 reconciliation comment in `sidecar_ws.py`) returns the **same** `{"type": "error", "data": {"code": "server.internal_error", "message": "internal error"}}` envelope as the TCP path — same `code`, same `message`, no leakage of `str(exception)`. The two paths are byte-identical on the wire (modulo the optional `id` correlation field).

## Client-side handling

- **Renderer (`usePython.ts`)** — inspects the resolved value of `window.python.call(...)` and throws a real `Error` when it sees either of the two error-envelope shapes the Electron main process can resolve with (the synthetic `{_error: "..."}` shape for backend-not-connected / `sendToPython` failures, AND the passthrough `{type: "error", data: {code, message}}` shape for server-side dispatch errors). The throw reads `result._error || result.data?.message || "unknown error"` so `try { await python.call(...) } catch (e) {}` callers see real failures on both shapes.
- **Rust host (`dispatch` Tauri command)** — rejects the `invoke` promise on `type: "error"`, translating it to `Err("server error [<code>]: <message>")` so the renderer-side `await api.call(...)` throws before the resolved value is ever inspected. The renderer-side in-code checks are therefore unreachable dead code on the Tauri path (errors surface via promise rejection); they remain in the source because the same `usePython.ts` bundle runs under both hosts and is live on the Electron path.

## Rationale

The unification (CR-20) replaced the previous per-transport split — TCP returned detailed messages and WS returned generic ones — with a single contract: both transports emit the same generic envelope, with the same `code` registry, and the same "no `str(exc)` leakage" rule. This makes the renderer's error-handling code transport-agnostic and removes a foot-gun where a developer would see a detailed message on TCP during local testing and assume the production WS path emitted the same thing.
