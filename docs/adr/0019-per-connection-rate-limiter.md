# ADR 0019: Per-Connection Rate Limiter (RELIABILITY-006)

## Status

Accepted — implemented in `voice_typer/server/ipc_server.py` as the `_RateLimiter` class, instantiated per TCP connection in `_handle_tcp_connection`.

## Date

2026-07-14

## Context

Voice Typer's IPC server accepts a single persistent TCP connection from the Electron frontend. Over this connection, the frontend sends JSON-lines commands and the backend responds with JSON-lines responses and push events.

**The problem:** A crash-looping or buggy Electron client can flood the IPC socket with thousands of malformed messages per second. Without rate limiting, this flood can:

1. **Exhaust file descriptors:** Each incoming message is read and parsed. If the dispatcher cannot keep up, the TCP receive buffer fills, the client's send buffer fills, and eventually the OS runs out of socket buffer memory.

2. **Starve the tray thread:** The IPC dispatch loop and the tray event loop share the same Python process. If the dispatcher is busy parsing and rejecting malformed messages, the tray thread does not get CPU time to process OS events, making the tray icon unresponsive.

3. **Mask genuine errors:** A flood of error responses ("invalid JSON", "unknown command") fills the log, making it impossible to find the actual error that caused the crash loop.

**Similar problems in production systems:**
- A malformed `set_config` with an oversized payload triggers 1000+ JSON parse errors per second.
- A renderer in an infinite re-render loop sends `get_status` at 60 Hz instead of the expected 1 Hz.
- A crash-looping model download handler reconnects and immediately resends `download_model`, triggering a 2nd download before the 1st was cancelled.

**Alternatives considered:**

1. **Global rate limiter (single counter for all connections).** A global counter is simple but unfair — a misbehaving client can consume the entire budget, starving other clients. Since Voice Typer only has one client, this is less of a concern, but a per-connection limiter is more architecturally correct.

2. **Token-bucket algorithm.** A token bucket (fixed rate + burst) is the standard approach for network rate limiting. The sliding-window deque approach achieves the same behavior with simpler implementation.

3. **Connection-level circuit breaker.** After N rejected messages, close the connection entirely and require reconnection. This is more aggressive and can cause reconnection storms. A rate limiter that returns errors but keeps the connection open is more graceful.

## Decision

Implement a **sliding-window per-connection rate limiter** using the `_RateLimiter` class:

### Algorithm

The limiter maintains a `deque` of timestamps for recently accepted messages:

1. On each incoming message, call `allow()`.
2. `allow()` evicts timestamps older than the window (1 second).
3. If the deque length exceeds `_RATE_LIMIT_BURST`, reject the message.
4. If the deque length exceeds `_RATE_LIMIT_SUSTAINED`, reject the message.
5. Otherwise, append the current timestamp and accept the message.

### Constants

```python
_RATE_LIMIT_WINDOW_SECONDS = 1.0    # Sliding window duration
_RATE_LIMIT_BURST = 200             # Maximum messages in a single window
_RATE_LIMIT_SUSTAINED = 60          # Maximum sustained messages per second
```

These limits are intentionally generous:
- A well-behaved Electron client sends 1-5 messages per second.
- The burst allowance (200) accommodates batch operations like loading the Settings page (which may fetch `get_config`, `get_microphones`, `get_model_catalog`, etc. in quick succession).
- The sustained rate (60/sec) prevents high-frequency polling but allows reasonable event-driven updates.

### Granularity

Each TCP connection gets its own `_RateLimiter` instance, created in `_handle_tcp_connection()` after auth succeeds. This means:
- A reconnect resets the rate limit counter.
- If multiple clients connected simultaneously (not supported today, but architecturally possible), each has an independent budget.

### Response on Rejection

When the rate limit is exceeded, the server sends:
```json
{"type": "error", "data": {"message": "rate limit exceeded; backing off"}}
```
The connection is NOT closed — the client is expected to back off and retry. A warning is logged at WARNING level with the cumulative rejected count.

### Thread Safety

The `_RateLimiter` uses a `threading.Lock` for all timestamp deque mutations. The `allow()` method is designed for single-threaded use (each connection is handled by one thread in the accept loop), but the lock ensures correctness if the class is ever used from multiple threads.

## Consequences

### Easier
- **CPU starvation protection:** A flood of malformed messages is rejected quickly (lock + deque eviction + timestamp comparison = single-digit microseconds) rather than dispatched to the command handler (hundreds of microseconds to milliseconds).
- **Log flood prevention:** Rejected messages produce one WARNING log entry per batch, not one per message.
- **Graceful degradation:** The client receives structured error responses and can implement exponential backoff, rather than experiencing a silent connection drop.

### More difficult
- **No client-side backoff (yet):** The Electron main process's `sendToPython()` does not currently implement backoff on "rate limit exceeded" responses. If the client hits the limit, the user sees IPC timeouts rather than graceful fallback. This is acceptable because hitting the limit indicates a bug in the client that should be fixed, not a normal operational condition.
- **Counter resets on reconnect:** A crash-looping client that reconnects 200 times per second gets 200 fresh budgets. Mitigation: the reconnect rate is itself limited by `_tcpRetryCount` exponential backoff in the Electron client (250ms → 500ms → 1s → 2s cap). So a crash loop cannot cycle fast enough to exploit the per-connection budget reset.

### Risks
- **Limits too generous:** 60 msg/s sustained is high for normal operation but within reach of a busy renderer with multiple reactive subscriptions. If a future feature adds a high-frequency IPC call (e.g., real-time waveform at 30 Hz), the sustained rate may need to be tuned. The constants are trivially adjustable.
- **False positives under load:** A model catalog with 200+ model entries could trigger the burst limit when the user opens the Models page. Mitigation: `get_model_catalog` returns a single response regardless of the number of entries; the burst limit of 200 accommodates fetching all settings pages in parallel.

## References

- `voice_typer/server/ipc_server.py` — `_RateLimiter` class (lines 208-260), usage in `_handle_tcp_connection` (lines 977-999).
- `tests/test_rate_limiter.py` — unit tests for the sliding-window algorithm.
- SECURITY.md — RELIABILITY-006 documentation.

*End of document.*
