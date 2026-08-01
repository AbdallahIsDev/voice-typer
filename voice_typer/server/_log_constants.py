"""Centralized log-rotation constants.

Mirrors the Rust-side ``ROTATE_MAX_BYTES`` / ``ROTATE_MAX_FILES`` in
``src-tauri/src/util.rs``. All Python logging handlers that create
``RotatingFileHandler`` instances MUST import from here to keep
rotation policy consistent across subsystems.
"""

ROTATE_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
ROTATE_MAX_FILES: int = 5
