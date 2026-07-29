# Architecture Decision Records (ADR) Index

Unique, zero-padded ADR numbers — one decision per file.
Tooling note: `adr-tools` and similar expect unique `NNNN-*.md` names; this directory now satisfies that constraint.

| ADR | File | Title | Status |
|-----|------|-------|--------|
| 0000 | `0000-adr-process.md` | Architecture Decision Records | Accepted |
| 0001 | `0001-record-architecture-decisions.md` | Record Architecture Decisions | Accepted |
| 0002 | `0002-electron-migration.md` | Electron + Python Architecture (Initial) | Superseded by [ADR 0003](0003-electron-python-architecture.md) |
| 0003 | `0003-electron-python-architecture.md` | Electron + Python Architecture (Refined) | Accepted |
| 0004 | `0004-ipc-protocol.md` | TCP IPC Protocol | Accepted |
| 0005 | `0005-silero-vad.md` | Silero VAD Adoption | Accepted |
| 0006 | `0006-clipboard-security.md` | Clipboard Security Approach | Accepted |
| 0007 | `0007-native-hotkey-architecture.md` | Native subprocess hotkey architecture | Accepted |
| 0008 | `0008-zero-command-hotkey-architecture.md` | Voice Typer — Zero-Command Hotkey Architecture Design | Accepted |
| 0009 | `0009-audio-filter-chain-architecture.md` | Audio Filter Chain Architecture | Accepted |
| 0010 | `0010-dependency-injection-boundary.md` | Dependency Injection Boundary for IPCServer | Accepted |
| 0011 | `0011-prewarm-architecture-analysis.md` | Voice Typer — Prewarm & Autostart Architecture | Accepted |
| 0012 | `0012-clipboard-borrow-restore-architecture.md` | Clipboard Borrow/Restore Architecture | Accepted |
| 0013 | `0013-desktop-runtime-migration-analysis.md` | Desktop Runtime Migration to Tauri v2 + Python Sidecar (Original — Windows-only) | Superseded by [ADR 0020](0020-desktop-runtime-migration-analysis.md). The migration analysis below is preserved for historical context; the current authoritative migration ADR is ADR-0020 (cross-platform rewrite). Electron is retained intact as a reversible fallback until Tauri + Sidecar is proven and cut over. |
| 0014 | `0014-tcp-ipc-session-token-auth.md` | TCP IPC Session Token Authentication (SEC-018) | Accepted — implemented in `ipc_server.py:_accept_tcp` / `_handle_tcp_connection` and `client/src/main/index.ts`. |
| 0015 | `0015-electron-command-allowlist.md` | Electron-Side Command Allowlist (SEC-019) | Accepted — implemented in `client/src/main/allowed-commands.ts` (ALLOWED_COMMANDS, ≈ lines 70–206; was `index.ts:532-627` before the allowlist was extracted into its own module). |
| 0016 | `0016-granular-consent-flags.md` | Granular Privacy Consent Flags (PRIV-005, PRIV-006, PRIV-009) | Accepted — implemented in `voice_typer/server/config.py` as typed boolean fields on the `Config` dataclass. |
| 0017 | `0017-cloud-url-allowlist-https.md` | Cloud URL Allowlist with HTTPS Enforcement (RELIABILITY-004) | Accepted — implemented in `voice_typer/server/_secrets.py`. |
| 0018 | `0018-heartbeat-watchdog.md` | Electron-Alive Heartbeat Watchdog (RW-10) | Accepted — implemented in `voice_typer/server/ipc_server.py:_heartbeat_loop`, `_check_heartbeat_timeout`, `_handle_heartbeat`, and `client/src/main/index.ts` heartbeat interval. |
| 0019 | `0019-per-connection-rate-limiter.md` | Per-Connection Rate Limiter (RELIABILITY-006) | Accepted — implemented in `voice_typer/server/ipc_server.py` as the `_RateLimiter` class, instantiated per TCP connection in `_handle_tcp_connection`. |
| 0020 | `0020-desktop-runtime-migration-analysis.md` | Desktop Runtime Migration to Tauri v2 + Python Sidecar (Cross-Platform Edition) | Accepted — migration in progress. Cross-platform rewrite of ADR-0013: covers Windows + macOS + Linux + Wayland + Apple Silicon + Linux ARM64. Electron is retained intact as a reversible fallback until Tauri + Sidecar is proven and cut over on all three supported platforms. Cutover is per-platform (Windows first → macOS → Linux). |

## Template

- `template.md` — boilerplate scaffold for new ADRs (not a decision itself).

## How to add a new ADR

1. Copy `template.md` to `<next-number>-<kebab-title>.md` (zero-padded, hyphen separator).
2. Set the H1 to `# ADR <next-number>: <Title>`.
3. Set Status (Proposed / Accepted / Deprecated / Superseded).
4. Update this index and any cross-references in other ADRs/docs.
