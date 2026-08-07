"""Centralized single-file log-size constant.

Mirrors the Rust-side ``LOG_MAX_BYTES`` in ``src-tauri/src/util.rs``.
All Python logging handlers that write log files MUST import the size
cap from here so a future bump edits ONE file.

Single-file policy (ADR-0020 §11, hardened): each log is a SINGLE file.
When it exceeds ``LOG_MAX_BYTES`` it is truncated in place (emptied) and
writing continues to the same path — numbered backups (``.1``, ``.2``,
...) are NEVER created. Disk usage is bounded at ``LOG_MAX_BYTES`` per
log file, forever.
"""

LOG_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB — single-file truncate cap
