"""Centralized log-retention constants (three-tier cleanup design).

Mirrors the Rust-side ``LOG_MAX_BYTES`` in ``src-tauri/src/util.rs``.
All Python logging handlers that write log files MUST import the size
cap from here so a future bump edits ONE file.

Three-tier cleanup design (every tier is best-effort):

  Tier 1 — AGE (primary): at session start, any log file in ``logs/``
    whose last write is older than ``LOG_AGE_RETENTION_SECONDS`` is
    deleted.  Keeps roughly the last 7 days of diagnostics and bounds
    storage for low-traffic installs whose logs would otherwise sit
    forever.

  Tier 2 — SIZE FALLBACK: at session start, any log file larger than
    ``LOG_SIZE_FALLBACK_BYTES`` is deleted even if freshly written —
    covers a marathon session that pushed a log past the fallback
    between startups.  Checked ONLY at session start, never mid-session.

  Tier 3 — MID-SESSION HARD CEILING: ``LOG_MAX_BYTES``.  When a log
    file exceeds this ceiling mid-session it is truncated in place
    (emptied) and writing continues — the emergency brake so a single
    never-ending session cannot grow a log without bound.  Deliberately
    set far above the Tier-2 fallback so normal multi-day usage NEVER
    reaches it (a file the ceiling truncates mid-session would have
    been deleted at the previous startup had one occurred).

Ordering invariant: ``LOG_SIZE_FALLBACK_BYTES`` (25 MB, session-start
delete) < ``LOG_MAX_BYTES`` (40 MB, mid-session truncate) so a file the
ceiling truncates mid-session is always caught by the fallback at the
next startup.
"""

# Tier 1 — age retention (session-start delete).
LOG_AGE_RETENTION_SECONDS: int = 7 * 24 * 60 * 60  # 7 days

# Tier 2 — size fallback (session-start delete).
LOG_SIZE_FALLBACK_BYTES: int = 25 * 1024 * 1024  # 25 MB

# Tier 3 — mid-session hard ceiling (truncate in place).
LOG_MAX_BYTES: int = 40 * 1024 * 1024  # 40 MB
