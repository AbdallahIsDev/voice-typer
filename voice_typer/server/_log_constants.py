"""Centralized log-retention constants (three-tier cleanup design)."""

# Tier 1, age retention (session-start delete).
LOG_AGE_RETENTION_SECONDS: int = 7 * 24 * 60 * 60  # 7 days

# Tier 2, size fallback (session-start delete).
LOG_SIZE_FALLBACK_BYTES: int = 25 * 1024 * 1024  # 25 MB

# Tier 3, mid-session hard ceiling (truncate in place).
LOG_MAX_BYTES: int = 40 * 1024 * 1024  # 40 MB
