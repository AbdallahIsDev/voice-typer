# Error Envelope Contract

Voice Typer uses **one unified error-envelope contract on both IPC transports** (TCP loopback for the Electron host, localhost WebSocket for the Tauri host). The unification was finalized in CR-20 (2026-07-18) — earlier drafts documented a per-transport split, but that split no longer exists.

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

There is **no** per-transport divergence. Both the TCP path (`ipc_server.py:_handle_tcp_connection`) and the WS path (`sidecar_ws.py:_handle_connection`) emit the same generic envelope for unhandled exceptions:

```json
{"type": "error", "data": {"code": "internal_error", "message": "internal error"}}
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

### Legacy non-namespaced aliases

The namespaced `client.*` / `server.*` form is canonical for new code. A small set of legacy non-namespaced aliases (`internal_error`, `shutting_down`, `unknown_command`, `unknown_tray_item`, `auth_failed`, `rate_limited`, `invalid_payload`, `invalid_field`, `missing_field`, `model_switch_failed`, `payload_too_large`) are still emitted by some paths for backward compatibility. The renderer MUST accept both forms and treat the legacy form as an alias for the namespaced equivalent. The contract test in `tests/test_error_codes_registry.py` is the regression guard.

## Per-command validation errors

Both transports return the SAME per-command validation error envelopes (e.g. `{"code": "client.invalid_payload", "message": "payload too large (N bytes; max M)"}` from `_validate_dict_payload`'s `max_payload_bytes` rule, or `{"code": "client.invalid_field", "message": "..."}` from a type-mismatch). These are explicit error codes the renderer can switch on, and they are produced identically on both transports because the WS path reuses `IPCServer._dispatch` (and therefore the same `_validate_dict_payload` helper) verbatim.

## Wire-side behavior

- **TCP path (Electron host)** — `ipc_server._handle_tcp_connection`'s ERR-018 block constructs the `{"type": "error", "data": {"code": "internal_error", "message": "internal error"}}` envelope for any unhandled dispatch exception, attaches the originating request `id` if available, and sends it back on the same socket. Validation errors from `_validate_dict_payload` are returned by the handler itself and flow back through the same `_send` path.
- **WS path (Tauri host)** — `sidecar_ws._handle_connection` reuses `IPCServer._dispatch` for inbound frames, so validation errors come back verbatim. For dispatch-loop exceptions, the WS path's catch-all (see the IPC-5 / 2026-07-18 reconciliation comment in `sidecar_ws.py`) returns the **same** `{"type": "error", "data": {"code": "internal_error", "message": "internal error"}}` envelope as the TCP path — same `code`, same `message`, no leakage of `str(exception)`. The two paths are byte-identical on the wire (modulo the optional `id` correlation field).

## Client-side handling

- **Renderer (`usePython.ts`)** — inspects the resolved value of `window.python.call(...)` and throws a real `Error` when it sees either of the two error-envelope shapes the Electron main process can resolve with (the synthetic `{_error: "..."}` shape for backend-not-connected / `sendToPython` failures, AND the passthrough `{type: "error", data: {code, message}}` shape for server-side dispatch errors). The throw reads `result._error || result.data?.message || "unknown error"` so `try { await python.call(...) } catch (e) {}` callers see real failures on both shapes.
- **Rust host (`dispatch` Tauri command)** — rejects the `invoke` promise on `type: "error"`, translating it to `Err("server error [<code>]: <message>")` so the renderer-side `await api.call(...)` throws before the resolved value is ever inspected. The renderer-side in-code checks are therefore unreachable dead code on the Tauri path (errors surface via promise rejection); they remain in the source because the same `usePython.ts` bundle runs under both hosts and is live on the Electron path.

## Rationale

The unification (CR-20) replaced the previous per-transport split — TCP returned detailed messages and WS returned generic ones — with a single contract: both transports emit the same generic envelope, with the same `code` registry, and the same "no `str(exc)` leakage" rule. This makes the renderer's error-handling code transport-agnostic and removes a foot-gun where a developer would see a detailed message on TCP during local testing and assume the production WS path emitted the same thing.
