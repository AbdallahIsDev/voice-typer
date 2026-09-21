# Architecture Decision Records (ADR) Index

Unique, zero-padded ADR numbers: one decision per file.
Tooling note: `adr-tools` and similar expect unique `NNNN-*.md` names; this directory now satisfies that constraint.

| ADR | File | Title | Status |
|-----|------|-------|--------|
| 0000 | `0000-adr-process.md` | Architecture Decision Records | Accepted |
| 0001 | `0001-record-architecture-decisions.md` | Record Architecture Decisions | Accepted |
| 0002 | `0002-desktop-host-migration.md` | predecessor + Python Architecture (Initial) | SUPERSEDED — predecessor host removed 2026-09-17; Tauri is sole host (ADR-0020). Historical record only. |
| 0003 | `0003-desktop-host-python-architecture.md` | predecessor + Python Architecture (Refined) | SUPERSEDED — predecessor host removed 2026-09-17; Tauri is sole host (ADR-0020). Historical record only. |
| 0004 | `0004-ipc-protocol.md` | TCP IPC Protocol | SUPERSEDED for transport — predecessor/TCP removed 2026-09-17; live path is Tauri WS (ADR-0020). Historical TCP details only. |
| 0005 | `0005-silero-vad.md` | Silero VAD Adoption | Accepted |
| 0006 | `0006-clipboard-security.md` | Clipboard Security Approach | Accepted |
| 0007 | `0007-native-hotkey-architecture.md` | Native subprocess hotkey architecture | Accepted |
| 0008 | `0008-zero-command-hotkey-architecture.md` | Voice Typer: Zero-Command Hotkey Architecture Design | Accepted |
| 0009 | `0009-audio-filter-chain-architecture.md` | Audio Filter Chain Architecture | Accepted |
| 0010 | `0010-dependency-injection-boundary.md` | Dependency Injection Boundary for IPCServer | Accepted |
| 0011 | `0011-prewarm-architecture-analysis.md` | Voice Typer: Prewarm & Autostart Architecture | Accepted |
| 0012 | `0012-clipboard-borrow-restore-architecture.md` | Clipboard Borrow/Restore Architecture | Accepted |
| 0013 | `0013-desktop-runtime-migration-analysis.md` | Desktop Runtime Migration to Tauri v2 + Python Sidecar (Original, Windows-only) | SUPERSEDED — predecessor host removed 2026-09-17; Tauri is sole host (ADR-0020). Historical record only. |
| 0014 | `0014-tcp-ipc-session-token-auth.md` | TCP IPC Session Token Authentication (SEC-018) | SUPERSEDED for predecessor/TCP — token auth remains live on Tauri WS (`sidecar_ws.py` + `src-tauri/src/sidecar/ws.rs`). Historical TCP path only. |
| 0015 | `0015-host-command-allowlist.md` | predecessor-Side Command Allowlist (SEC-019) | SUPERSEDED — predecessor host removed 2026-09-17; Tauri is sole host (ADR-0020). Live surface is Rust `allowlist.rs`. Historical record only. |
| 0016 | `0016-granular-consent-flags.md` | Granular Privacy Consent Flags (PRIV-005, PRIV-006, PRIV-009) | Accepted: implemented in `voice_typer/server/config.py` as typed boolean fields on the `Config` dataclass. |
| 0017 | `0017-cloud-url-allowlist-https.md` | Cloud URL Allowlist with HTTPS Enforcement (RELIABILITY-004) | Accepted: implemented in `voice_typer/server/_secrets.py`. |
| 0018 | `0018-heartbeat-watchdog.md` | predecessor-Alive Heartbeat Watchdog (RW-10) | SUPERSEDED — predecessor host removed 2026-09-17; Tauri is sole host (ADR-0020). Rust supervisor owns liveness; heartbeat loop disabled under `TAURI_SIDECAR=1`. Historical record only. |
| 0019 | `0019-per-connection-rate-limiter.md` | Per-Connection Rate Limiter (RELIABILITY-006) | Accepted / live under Tauri WS: `_RateLimiter` in `ipc_server.py` (instantiated per connection via `_get_rate_limiter`). predecessor/TCP context in the body is historical. |
| 0020 | `0020-desktop-runtime-migration-analysis.md` | Desktop Runtime Migration to Tauri v2 + Python Sidecar (Cross-Platform Edition) | **Current authority.** Cutover complete 2026-09-17: predecessor removed; Tauri is the sole desktop host. Cross-platform rewrite of ADR-0013. |
| 0021 | `rest-encryption.md` | At-Rest Encryption for User Data (Design-Gated) | Proposed (design-only: no production code changes; implementation tracked under the "Phased rollout" section of the ADR). |
| 0022 | `0022-ws-tcp-protocol-version-asymmetry.md` | Sidecar WS Protocol-Version Check Stays Advisory While TCP Rejects | Accepted: deliberate asymmetry (WS warns-and-continues, TCP rejects with `server.protocol_version_mismatch`); revisit at the ADR-0020 single-transport cutover. |

## Template

- `template.md` Boilerplate scaffold for new ADRs (not a decision itself).

## How to add a new ADR

1. Copy `template.md` to `<next-number>-<kebab-title>.md` (zero-padded, hyphen separator).
2. Set the H1 to `# ADR <next-number>: <Title>`.
3. Set Status (Proposed / Accepted / Deprecated / Superseded).
4. Update this index and any cross-references in other ADRs/docs.
