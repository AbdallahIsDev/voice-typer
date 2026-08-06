# ADR 0019: Per-Connection Rate Limiter (RELIABILITY-006)

## Status

Accepted — implemented in `voice_typer/server/ipc_server.py` (canonical;
duplicate leaf copy at `voice_typer/server/ipc/rate_limiter.py` retained
as the `_RateLimiter` class, instantiated per
`IPCServer` process via `_get_rate_limiter(server)` and
shared across all TCP / WS connections within that process.

## Date

2026-07-14 (original); 2026-07-18 (IPC-4 dual-window revision); 2026-07-19 (R4-F17 doc refresh).

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

The limiter maintains TWO independent `deque`s of timestamps for recently
accepted messages — one for the per-second burst check, one for the
sustained average-rate check (IPC-4 revision; see "IPC-4 Dual-Window
Revision" below for the history of why two deques are needed):

1. On each incoming message, call `allow()`.
2. `allow()` evicts timestamps older than the burst window (1 second)
   from `_burst_timestamps` and timestamps older than the sustained
   window (10 seconds) from `_sustained_timestamps` — both evictions
   happen under a single `threading.Lock` acquisition so the decision
   is atomic.
3. If `len(_burst_timestamps) >= _RATE_LIMIT_BURST`, reject the
   message (per-second cap tripped).
4. If `len(_sustained_timestamps) >= _RATE_LIMIT_SUSTAINED`, reject
   the message (10-second average-rate cap tripped).
5. Otherwise, append the current timestamp to BOTH deques and accept
   the message.

The two checks are **independent** — a client can trip burst (201 msgs
in any 1 s) without tripping sustained (601 msgs in any 10 s), and vice
versa. The rejected counter is incremented atomically inside `allow()`
when it returns `False` (SEC-6 fix), so the benign race where two
threads both decide to reject and double-count the rejection no longer
exists.

### IPC-4 Dual-Window Revision (2026-07-18)

The original comment claimed "burst is the hard
per-second cap" but the implementation used a SINGLE deque for both
checks, with the same `window` (10 s). With `burst=200` and
`sustained=600` over the same 10 s deque, the burst check (`>= 200`)
ALWAYS fired first, making the sustained check (`>= 600`) unreachable
dead code.

**Pre-IPC-4 effective behavior:** only the burst check mattered. A
slow-drip attacker sending 100 msgs/s for 10 s (1000 msgs total, well
above the 600 sustained cap) was throttled at the 201st msg by the
burst check, NOT at the 601st msg by the sustained check — but the
throttle was the same either way (a `rate_limited` response). The real
regression was that a slow-drip attacker sending 50 msgs/s under the
200/s burst, but 500 msgs in 10 s — also under sustained because 500 <
600) was NOT throttled at all, when the design intent was that 60
msgs/s average should be the sustainable ceiling.

**Post-IPC-4 fix:** TWO independent deques:

- `_burst_timestamps` — 1-second sliding window (`burst_window`
  parameter, default `_RATE_LIMIT_BURST_WINDOW_SECONDS = 1.0`). If
  the deque reaches `burst` entries (default 200), the next message
  is rejected. Catches fast-burst attacks (201+ msgs in any 1 s).
- `_sustained_timestamps` — `window`-second sliding window (default
  10 s). If the deque reaches `sustained` entries (default 600 =
  60 msg/s avg), the next message is rejected. Catches slow-drip
  attacks (601+ msgs in any 10 s = 60.1 msg/s avg) that never trip
  the per-second burst.

See `tests/test_ipc_rate_limiter_dual_window.py` for the behavioral
pin: a 100 msg/s × 7 s slow-drip attacker is now correctly throttled
at the 601st msg by the sustained check, where pre-IPC-4 it would
have leaked through (burst deque never reached 200 in any 1 s, so
the single-deque `len >= 200` check never tripped either).

### Constants

```python
# Burst window — fast-burst attack cap (per-second).
_RATE_LIMIT_BURST_WINDOW_SECONDS = 1.0
_RATE_LIMIT_BURST = 200  # Max msgs in any 1 s window

# Sustained window — slow-drip attack cap (10 s average rate).
_RATE_LIMIT_WINDOW_SECONDS = 10.0
_RATE_LIMIT_SUSTAINED = 600  # Max msgs in any 10 s window
# (= 60 msg/s average)
```

These limits are intentionally generous:
- A well-behaved Electron client sends 1-5 messages per second.
- The burst allowance (200/s) accommodates batch operations like
  loading the Settings page (which may fetch `get_config`,
  `get_microphones`, `get_model_catalog`, etc. in quick succession).
- The sustained rate (60/sec average over 10 s) prevents high-frequency
  polling but allows reasonable event-driven updates.

The constants live in both `voice_typer/server/ipc_server.py` (the
canonical implementation; imported by tests as
`from voice_typer.server.ipc_server import _RATE_LIMIT_BURST`) and the
parallel leaf copy `voice_typer/server/ipc/rate_limiter.py` (retained
the duplicate `ipc/` package was NOT deleted in this
IMPROVE-mode run because the reviewer cycle for a package delete is too
risky without a full test sweep). The two copies MUST stay in sync; a
drift would surface as a test failure in
`tests/test_ipc_rate_limiter_dual_window.py` (which imports from
`ipc_server`).

### Granularity

**(2026-07-15) fix:** the limiter is now **per-process**, not
per-connection. A single `_RateLimiter` instance is lazily created
and stored on the `IPCServer` instance via `_get_rate_limiter(server)`.
All TCP reconnects and WS reconnects within the same server process
share the same sliding-window deques, so a local attacker can no
longer reset the budget by disconnecting and reconnecting. (The
original per-connection design allowed a crash-looping client to
burst 200 msgs, disconnect, reconnect, and burst again — bypassing
the sustained cap entirely.)

The lazy init is guarded by a module-level `threading.Lock`
(`_RATE_LIMITER_INIT_LOCK`, added R4-F18) so two threads
simultaneously hitting `_get_rate_limiter(server)` on a fresh
server instance cannot race past the `isinstance` check and create
two competing `_RateLimiter` instances (which would have diverged
timestamp deques — one of the two would be orphaned and its accepted
messages would not count toward the canonical budget).

### Response on Rejection

When the rate limit is exceeded, the server sends:
```json
{"type": "error", "data": {"code": "rate_limited", "message": "rate limit exceeded; backing off"}}
```
(IPC-5 fix: the envelope now carries a structured `code: "rate_limited"`
field so the client can branch on code rather than parsing the message
text; pre-IPC-5 only the `message` field was present.) The connection
is NOT closed — the client is expected to back off and retry. A warning
is logged at WARNING level with the cumulative rejected count.

### Thread Safety

The `_RateLimiter` instance uses a `threading.Lock` (`self._lock`,
acquired in `allow()`) for all timestamp-deque mutations. The
`allow()` method is designed for single-threaded use per connection,
but the lock ensures correctness when the same limiter is reached
concurrently from the TCP accept thread and the WS dispatch coroutine
(both share the per-process limiter after the CR-11 fix).

The lazy-init lookup `_get_rate_limiter(server)` is guarded by a
separate module-level lock (`_RATE_LIMITER_INIT_LOCK`, R4-F18) so
the get-or-create sequence is atomic across threads. Without this
second lock, two threads simultaneously hitting the helper on a
fresh server instance could both observe `limiter is None`, both
construct a fresh `_RateLimiter`, and one of the two would be
orphaned (its accepted timestamps would not count toward the
canonical budget). The init lock is held only for the brief
get-or-create window, not for the `allow()` call itself, so it does
not serialize dispatch.

## Consequences

### Easier
- **CPU starvation protection:** A flood of malformed messages is rejected quickly (lock + deque eviction + timestamp comparison = single-digit microseconds) rather than dispatched to the command handler (hundreds of microseconds to milliseconds).
- **Log flood prevention:** Rejected messages produce one WARNING log entry per batch, not one per message.
- **Graceful degradation:** The client receives structured error responses and can implement exponential backoff, rather than experiencing a silent connection drop.

### More difficult
- **No client-side backoff (yet):** The Electron main process's `sendToPython()` does not currently implement backoff on "rate limit exceeded" responses. If the client hits the limit, the user sees IPC timeouts rather than graceful fallback. This is acceptable because hitting the limit indicates a bug in the client that should be fixed, not a normal operational condition.
- **Per-process budget (post-CR-11):** a single misbehaving connection consumes the budget for ALL connections in the same server process. Acceptable because Voice Typer has exactly one client per process; if a future multi-client mode is added, the limiter would need to move back to per-connection (with a separate cross-connection aggregate cap to prevent the reconnect-reset bypass that CR-11 fixed).

### Risks
- **Limits too generous:** 60 msg/s sustained is high for normal operation but within reach of a busy renderer with multiple reactive subscriptions. If a future feature adds a high-frequency IPC call (e.g., real-time waveform at 30 Hz), the sustained rate may need to be tuned. The constants are trivially adjustable.
- **False positives under load:** A model catalog with 200+ model entries could trigger the burst limit when the user opens the Models page. Mitigation: `get_model_catalog` returns a single response regardless of the number of entries; the burst limit of 200 accommodates fetching all settings pages in parallel.

## References

- `voice_typer/server/ipc_server.py` — `_RateLimiter` class and
  `_get_rate_limiter(server)` lazy-init helper (canonical implementation,
  imported by tests).
- `voice_typer/server/ipc/rate_limiter.py` — parallel leaf copy retained
  per CR-14; must stay in sync with `ipc_server.py`.
- `tests/test_ipc_rate_limiter_dual_window.py` — pins the dual-window
  behavior (burst / sustained independence).
- `tests/test_comprehensive_review_fixes.py::TestRateLimiterPerProcess` — pins the
  per-process (CR-11) instance-sharing behavior.
- `tests/test_ipc_error_envelope_parity.py` — pins the
  `code: "rate_limited"` envelope shape on both TCP and WS paths.
- SECURITY.md — RELIABILITY-006 documentation.

*End of document.*
