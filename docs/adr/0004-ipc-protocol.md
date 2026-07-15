# ADR 0004: TCP IPC Protocol

## Status

Accepted

## Date

2024-01-20

## Context

The Electron frontend and Python backend need a communication channel. Options considered:

1. **stdin/stdout pipes** — simple but blocking; hard to handle bidirectional async messages.
2. **HTTP REST API** — request/response only; server cannot push events (recording state
   changes, transcription progress) to the client.
3. **WebSocket** — bidirectional, but adds a dependency and complexity for what is
   essentially local-only communication.
4. **Local TCP socket with JSON protocol** — bidirectional, low latency, no external
   dependencies, and works with Python's `socket` module.

## Decision

We chose **local TCP socket with JSON protocol** (option 4). The Python backend listens
on `127.0.0.1:0` (OS-assigned port), writes the port to a known file, and the Electron
client reads the port and connects. Messages are newline-delimited JSON with a simple
`{type, payload}` envelope.

## Consequences

### Positive
- No external dependencies beyond Python's stdlib `socket` module.
- Bidirectional: server can push recording state updates at any time.
- Simple protocol: JSON lines are easy to debug and extend.
- Secure by default: only listens on loopback interface.

### Negative
- No built-in schema validation; malformed messages need explicit error handling.
- TCP is stream-oriented; we must handle message framing (newline delimiters).
- Authentication token required to prevent other local processes from connecting
  (see SECURITY.md).
